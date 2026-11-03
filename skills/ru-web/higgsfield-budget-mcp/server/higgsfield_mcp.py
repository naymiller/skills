#!/usr/bin/env python3
"""higgsfield-budget-mcp — локальный MCP-адаптер Higgsfield Developer API (stdio, только стандартная библиотека).

Ключ читает сам процесс адаптера — из Связки ключей macOS (service "higgsfield-api")
или из переменных окружения HF_KEY / HF_API_KEY_ID + HF_API_KEY_SECRET.
Модель (Claude) ключ не видит: инструменты возвращают только статусы, request_id, оценки и пути файлов.

Защита денег:
- каждая генерация идёт внутри «пакета» с лимитом в USD и по числу запросов;
- перед отправкой обязательна оценка стоимости (/estimate); неизвестная цена = отказ;
- Idempotency-Key создаётся и записывается в журнал ДО отправки; повтор после таймаута/5xx — только с тем же ключом;
- журнал заданий хранится вне проекта: ~/.higgsfield-mcp/jobs/ (без ключей и без ссылок на файлы).
"""
try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path

VERSION = "0.4.0"
API = os.environ.get("HF_API_BASE", "https://api.higgsfield.ai").rstrip("/")
STATE_DIR = Path(os.environ.get("HFMCP_STATE_DIR", Path.home() / ".higgsfield-mcp" / "jobs"))
KEYCHAIN_SERVICE = os.environ.get("HFMCP_KEYCHAIN_SERVICE", "higgsfield-api")
TERMINAL = {"completed", "failed", "nsfw", "canceled"}
NOT_CHARGED = {"failed", "nsfw", "canceled"}
MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*){1,5}$")
UUID_RE = re.compile(r"^[0-9a-fA-F-]{8,64}$")
UPLOAD_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
                ".gif": "image/gif", ".wav": "audio/wav", ".mp4": "video/mp4"}
MAX_DOWNLOAD = 2 * 1024 ** 3
SECRET_PATTERNS = [re.compile(r"Key\s+\S+"), re.compile(r"[0-9a-f]{8}-[0-9a-f-]{27,}:[0-9a-f]{32,}", re.I)]


class ToolError(Exception):
    pass


# ---------- credentials (never returned, never logged) ----------
def _credential():
    key = os.environ.get("HF_KEY")
    if key:
        return key.strip(), "env:HF_KEY"
    kid, sec = os.environ.get("HF_API_KEY_ID") or os.environ.get("HF_API_KEY"), \
        os.environ.get("HF_API_KEY_SECRET") or os.environ.get("HF_API_SECRET")
    if kid and sec:
        return f"{kid.strip()}:{sec.strip()}", "env:HF_API_KEY_ID+SECRET"
    if sys.platform == "darwin" and shutil.which("security"):
        r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                           capture_output=True, text=True, timeout=15)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip(), f"keychain:{KEYCHAIN_SERVICE}"
    return None, None


def _redact(text):
    text = str(text)
    for p in SECRET_PATTERNS:
        text = p.sub("[скрыто]", text)
    return text[:500]


# ---------- HTTP ----------
def _http(method, url, body=None, headers=None, auth=True, timeout=60):
    h = {"User-Agent": f"higgsfield-budget-mcp/{VERSION}", "Accept": "application/json"}
    if auth:
        key, _ = _credential()
        if not key:
            raise ToolError("Ключ Higgsfield не настроен. Сохрани его в Связке ключей или переменной HF_KEY (см. SKILL.md). Ключ в чат не присылай.")
        h["Authorization"] = f"Key {key}"
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read() or b"{}")
        except ValueError:
            payload = {}
        return e.code, payload


def _detail(payload):
    if isinstance(payload, dict):
        return _redact(payload.get("detail") or payload.get("message") or payload.get("error") or payload)
    return _redact(payload)


ERRORS = {401: "Неверный или отозванный ключ (401). Повторять бессмысленно — обнови ключ в Связке ключей.",
          403: "Недостаточно кредитов на балансе API (403). Пополнение — только решением пользователя.",
          404: "Не найдено (404): проверь модель/request_id и аккаунт.",
          422: "Параметры не прошли проверку (422)"}


def _raise(code, payload):
    msg = ERRORS.get(code, f"Ошибка API {code}")
    raise ToolError(f"{msg}: {_detail(payload)}" if code in (404, 422) or code not in ERRORS else msg)


# ---------- journal & budget ----------
class Store:
    def __init__(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(STATE_DIR, 0o700)
        except OSError:
            pass
        self.path = STATE_DIR / "state.json"
        self.lock = STATE_DIR / ".lock"

    def __enter__(self):
        self.fh = open(self.lock, "w")
        if fcntl:
            fcntl.flock(self.fh, fcntl.LOCK_EX)
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {"batches": {}, "jobs": {}}
        return self.data

    def __exit__(self, exc_type, *_):
        if exc_type is None:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1))
            os.chmod(tmp, 0o600)
            tmp.replace(self.path)
        if fcntl:
            fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


def _spent(data, batch_id):
    used = 0.0
    n = 0
    for j in data["jobs"].values():
        if j["batch_id"] != batch_id:
            continue
        n += 1
        if j.get("status") not in NOT_CHARGED:
            used += float(j.get("estimate_usd") or 0)
    return round(used, 4), n


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


# ---------- tools ----------
# ---------- рабочая папка: единственное место, где адаптер читает и пишет файлы проекта ----------
CONFIG_PATH = STATE_DIR.parent / "config.json"
DEFAULT_WORKSPACE = Path.home() / "HiggsfieldWork"
WORKSPACE_DIRS = ("inbox", "references", "projects", "recipes")


def _workspace():
    """Текущая рабочая папка или None, если ещё не создана. Сменить можно только вручную (config.json)."""
    env = os.environ.get("HFMCP_WORKSPACE")
    if env:
        return Path(os.path.expanduser(env)).resolve()
    if CONFIG_PATH.exists():
        try:
            ws = json.loads(CONFIG_PATH.read_text()).get("workspace")
            if ws:
                return Path(ws).resolve()
        except ValueError:
            pass
    return None


def _inside_workspace(path, must_exist):
    ws = _workspace()
    if not ws or not ws.is_dir():
        raise ToolError("Рабочая папка не создана. Запусти hf_workspace_init (первичная настройка).")
    p = Path(os.path.expanduser(str(path)))
    if not p.is_absolute():
        p = ws / p
    p = p.resolve()  # раскрывает символические ссылки и '..'
    if p != ws and ws not in p.parents:
        raise ToolError(f"Доступ только внутри рабочей папки {ws}. Скопируй файл туда (например, в references/).")
    if must_exist and not p.exists():
        raise ToolError("Файл не найден в рабочей папке")
    return p


def t_workspace_init(a):
    current = _workspace()
    if current:
        current.mkdir(parents=True, exist_ok=True)
        for d in WORKSPACE_DIRS:
            (current / d).mkdir(exist_ok=True)
        return {"workspace": str(current), "created": False,
                "note": "Папка уже настроена. Сменить её можно только вручную в ~/.higgsfield-mcp/config.json."}
    target = Path(os.path.expanduser(str(a.get("path") or DEFAULT_WORKSPACE))).resolve()
    home = Path.home().resolve()
    forbidden = {Path("/"), home, home / "Desktop", home / "Documents", home / "Downloads", home / "Library"}
    if target in forbidden or home not in target.parents:
        raise ToolError("Рабочая папка должна быть отдельной новой папкой внутри домашней, например ~/HiggsfieldWork")
    if target.exists() and any(target.iterdir()):
        raise ToolError("Папка уже существует и не пуста — выбери новое имя, чтобы адаптер не получил доступ к чужим файлам")
    target.mkdir(parents=True, exist_ok=True)
    for d in WORKSPACE_DIRS:
        (target / d).mkdir(exist_ok=True)
    (target / "README.txt").write_text("Рабочая папка. Клади исходники в inbox/, референсы в references/.\n"
                                       "Результаты задач появятся в projects/<id>/outputs/.\n"
                                       "Адаптер Higgsfield читает и пишет файлы только внутри этой папки.\n")
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps({"workspace": str(target)}, ensure_ascii=False))
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass
    return {"workspace": str(target), "created": True, "subfolders": list(WORKSPACE_DIRS),
            "next": "Подключи эту папку в Claude (Add folder / доступ к папке) — и работай только в ней."}


def t_status(_):
    key, source = _credential()
    out = {"adapter_version": VERSION, "api": API, "credential_configured": bool(key),
           "credential_source": source, "state_dir": str(STATE_DIR),
           "workspace": str(_workspace()) if _workspace() else None}
    if not out["workspace"]:
        out["workspace_hint"] = "Рабочая папка не создана — вызови hf_workspace_init."
    if not key:
        out["hint"] = "macOS: security add-generic-password -a \"$USER\" -s higgsfield-api -w  (ключ вводится в Терминале, не в чат)"
        return out
    # Документированная проверка: GET статуса заведомо несуществующего запроса — 404 = ключ принят, 401 = ключ отклонён. Бесплатно.
    code, payload = _http("GET", f"{API}/requests/{uuid.uuid4()}/status")
    out["auth_check"] = {404: "ok (ключ принят)", 401: "FAIL: ключ отклонён (401)"}.get(code, f"неожиданный ответ {code}")
    return out


def t_estimate(a):
    model, args = _model(a), a.get("arguments") or {}
    code, payload = _http("POST", f"{API}/estimate/{model}", args)
    if code != 200:
        _raise(code, payload)
    return {"model": model, "credits": payload.get("credits"), "usd": payload.get("usd"),
            "note": "Оценка сервиса; фактическое списание — только за успешный результат."}


def t_batch_open(a):
    name = str(a.get("name") or "batch")[:60]
    max_usd, max_req = float(a["max_usd"]), int(a["max_requests"])
    if max_usd <= 0 or max_req <= 0 or max_req > 200:
        raise ToolError("max_usd > 0, 1 ≤ max_requests ≤ 200")
    bid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    with Store() as d:
        d["batches"][bid] = {"name": name, "max_usd": max_usd, "max_requests": max_req, "opened_at": _now(),
                             "approved_by_user_note": str(a.get("approval_note") or "")[:300], "closed": False}
    return {"batch_id": bid, "max_usd": max_usd, "max_requests": max_req}


def t_batch_status(a):
    with Store() as d:
        b = d["batches"].get(a["batch_id"])
        if not b:
            raise ToolError("Пакет не найден")
        used, n = _spent(d, a["batch_id"])
        jobs = [{"intent_id": k, **{x: v.get(x) for x in ("request_id", "status", "model", "estimate_usd")}}
                for k, v in d["jobs"].items() if v["batch_id"] == a["batch_id"]]
    return {**b, "reserved_usd": used, "requests": n, "remaining_usd": round(b["max_usd"] - used, 4),
            "remaining_requests": b["max_requests"] - n, "jobs": jobs}


def t_batch_close(a):
    with Store() as d:
        if a["batch_id"] not in d["batches"]:
            raise ToolError("Пакет не найден")
        d["batches"][a["batch_id"]]["closed"] = True
    return {"closed": a["batch_id"]}


def _model(a):
    m = str(a.get("model", "")).strip("/")
    if not MODEL_RE.match(m):
        raise ToolError("model — путь модели из документации Higgsfield, например 'higgsfield-ai/soul/v2/standard'")
    return m


def t_submit(a):
    model, args, bid = _model(a), a.get("arguments") or {}, a.get("batch_id")
    if not isinstance(args, dict):
        raise ToolError("arguments должен быть JSON-объектом")
    intent = a.get("intent_id")
    with Store() as d:
        if intent and intent in d["jobs"]:
            job = d["jobs"][intent]
            if job.get("request_id"):
                return {"already_submitted": True, "intent_id": intent, "request_id": job["request_id"],
                        "status": job.get("status"), "note": "Повторной генерации нет. Проверь hf_get."}
        b = d["batches"].get(bid)
        if not b or b["closed"]:
            raise ToolError("Нужен открытый пакет (hf_batch_open) с согласованным пользователем лимитом.")
        used, n = _spent(d, bid)
    if not intent:
        est_code, est = _http("POST", f"{API}/estimate/{model}", args)
        if est_code != 200 or est.get("usd") in (None, ""):
            raise ToolError("Не удалось получить оценку стоимости — генерация не отправлена. " +
                            (ERRORS.get(est_code, "") or _detail(est)))
        usd = float(est["usd"])
        if n + 1 > b["max_requests"] or used + usd > b["max_usd"] + 1e-9:
            raise ToolError(f"Превышение лимита пакета: оценка ${usd}, уже зарезервировано ${used} из ${b['max_usd']}, "
                            f"запросов {n} из {b['max_requests']}. Нужен новый пакет с согласия пользователя.")
        intent = str(uuid.uuid4())
        with Store() as d:
            d["jobs"][intent] = {"batch_id": bid, "model": model, "arguments": args, "estimate_usd": usd,
                                 "estimate_credits": est.get("credits"), "status": "sending", "created_at": _now()}
    else:
        with Store() as d:
            job = d["jobs"].get(intent)
            if not job or job["batch_id"] != bid:
                raise ToolError("intent_id не найден в этом пакете")
            model, args = job["model"], job["arguments"]
    last = None
    for attempt in range(3):  # повтор только с тем же Idempotency-Key — дубля и двойного списания не будет
        try:
            code, payload = _http("POST", f"{API}/{model}", args, headers={"Idempotency-Key": intent}, timeout=90)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = f"сеть/таймаут: {_redact(e)}"
            time.sleep(2 * (attempt + 1))
            continue
        if code >= 500:
            last = f"ошибка сервера {code}"
            time.sleep(2 * (attempt + 1))
            continue
        if code not in (200, 201, 202):
            with Store() as d:
                d["jobs"][intent]["status"] = "rejected"
                d["jobs"][intent]["error"] = f"{code}"
            _raise(code, payload)
        with Store() as d:
            d["jobs"][intent].update(request_id=payload.get("request_id"), status=payload.get("status", "queued"),
                                     submitted_at=_now())
            usd_reserved = d["jobs"][intent]["estimate_usd"]
        return {"intent_id": intent, "request_id": payload.get("request_id"), "status": payload.get("status"),
                "estimate_usd": usd_reserved}
    with Store() as d:
        d["jobs"][intent]["status"] = "unknown"
    raise ToolError(f"Исход отправки неизвестен ({last}). НЕ создавай новую генерацию: повтори hf_submit с "
                    f"intent_id={intent} — сервис вернёт тот же request_id без нового списания.")


def _find(request_id):
    with Store() as d:
        for k, v in d["jobs"].items():
            if v.get("request_id") == request_id:
                return k
    return None


def _outputs(payload):
    urls = []
    for k in ("images", "audios", "videos"):
        for it in payload.get(k) or []:
            if isinstance(it, dict) and it.get("url"):
                urls.append(it["url"])
    for k in ("video", "audio", "zip", "mov", "jsx", "fbx", "ply", "glb", "model"):
        it = payload.get(k)
        if isinstance(it, dict) and it.get("url") and it["url"] not in urls:
            urls.append(it["url"])
    return urls


def t_get(a):
    rid = str(a["request_id"])
    if not UUID_RE.match(rid):
        raise ToolError("Некорректный request_id")
    code, payload = _http("GET", f"{API}/requests/{rid}/status")
    if code != 200:
        _raise(code, payload)
    st = payload.get("status")
    intent = _find(rid)
    if intent:
        with Store() as d:
            d["jobs"][intent]["status"] = st
    out = {"request_id": rid, "status": st, "terminal": st in TERMINAL, "outputs": len(_outputs(payload))}
    if st == "failed":
        out["error"] = _detail(payload.get("error") or "")
    if st in NOT_CHARGED:
        out["charged"] = False
    return out


def t_download(a):
    rid = str(a["request_id"])
    out_dir = _inside_workspace(a["output_dir"], must_exist=False)
    if out_dir == _workspace():
        raise ToolError("output_dir — папка проекта внутри рабочей папки, например projects/<id>/outputs/images")
    code, payload = _http("GET", f"{API}/requests/{rid}/status")
    if code != 200:
        _raise(code, payload)
    if payload.get("status") != "completed":
        raise ToolError(f"Задание не завершено: {payload.get('status')}")
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = re.sub(r"[^a-zA-Z0-9_-]", "", str(a.get("prefix") or rid[:8]))[:40]
    files = []
    for i, url in enumerate(_outputs(payload), 1):
        if not (url.startswith("https://") or (API.startswith("http://127.0.0.1") and url.startswith("http://127.0.0.1"))):
            raise ToolError("Небезопасная ссылка на результат (не https) — пропущено")
        ext = os.path.splitext(url.split("?")[0])[1][:6] or ".bin"
        target = out_dir / f"{prefix}-{i:02d}{ext}"
        n = 1
        while target.exists():
            n += 1
            target = out_dir / f"{prefix}-{i:02d}-{n}{ext}"
        req = urllib.request.Request(url, headers={"User-Agent": f"higgsfield-budget-mcp/{VERSION}"})  # без Authorization
        size = 0
        with urllib.request.urlopen(req, timeout=300) as r, open(target, "wb") as fh:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_DOWNLOAD:
                    raise ToolError("Файл больше 2 ГБ — остановлено")
                fh.write(chunk)
        files.append({"path": str(target), "bytes": size})
    intent = _find(rid)
    meta = {"request_id": rid, "intent_id": intent, "status": "completed", "downloaded_at": _now(), "files": files}
    if intent:
        with Store() as d:
            j = d["jobs"][intent]
            meta.update(model=j["model"], arguments=j["arguments"], estimate_usd=j["estimate_usd"],
                        cost_note="estimate (фактическое списание смотри в консоли Higgsfield)")
            j["downloaded_files"] = [f["path"] for f in files]
    (out_dir / f"{prefix}.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    return {"files": files, "meta": str(out_dir / f"{prefix}.meta.json")}


def t_cancel(a):
    rid = str(a["request_id"])
    code, payload = _http("POST", f"{API}/requests/{rid}/cancel")
    if code in (200, 202):
        intent = _find(rid)
        if intent:
            with Store() as d:
                d["jobs"][intent]["status"] = "canceled"
        return {"request_id": rid, "canceled": True, "note": "Отмена принята; проверь hf_get."}
    if code == 400:
        return {"request_id": rid, "canceled": False, "note": "Обработка уже началась — отменить нельзя."}
    _raise(code, payload)


def t_upload(a):
    p = _inside_workspace(a["path"], must_exist=True)
    if not p.is_file():
        raise ToolError("Файл не найден на этом компьютере")
    ctype = UPLOAD_TYPES.get(p.suffix.lower())
    if not ctype:
        raise ToolError("Поддерживаются jpg, png, webp, gif, wav, mp4")
    if p.stat().st_size > 200 * 1024 ** 2:
        raise ToolError("Файл больше 200 МБ")
    code, payload = _http("POST", f"{API}/files/generate-upload-url", {"content_type": ctype})
    if code != 200:
        _raise(code, payload)
    headers = payload.get("upload_headers") or {"Content-Type": ctype}
    req = urllib.request.Request(payload["upload_url"], data=p.read_bytes(), method="PUT", headers=headers)  # без Authorization
    with urllib.request.urlopen(req, timeout=300) as r:
        if r.status not in (200, 201, 204):
            raise ToolError(f"Загрузка не удалась: {r.status}")
    return {"public_url": payload["public_url"], "content_type": ctype,
            "note": "Передай public_url в параметр модели (image_url / video_url / audio_url)."}


def t_jobs(a):
    limit = int(a.get("limit") or 20)
    with Store() as d:
        items = sorted(d["jobs"].items(), key=lambda kv: kv[1].get("created_at", ""), reverse=True)[:limit]
    return [{"intent_id": k, **{x: v.get(x) for x in ("batch_id", "model", "request_id", "status", "estimate_usd", "created_at")}}
            for k, v in items]


OBJ = {"type": "object"}
TOOLS = {
    "hf_status": (t_status, "Проверить, настроен ли ключ Higgsfield и принимает ли его API (бесплатно). Ключ не возвращается.", {}, []),
    "hf_estimate": (t_estimate, "Бесплатная оценка стоимости генерации (credits и USD) для модели и параметров.",
                    {"model": {"type": "string"}, "arguments": OBJ}, ["model", "arguments"]),
    "hf_batch_open": (t_batch_open, "Открыть пакет генераций с лимитом USD и числа запросов. Вызывать ТОЛЬКО после явного согласия пользователя на эти лимиты.",
                      {"name": {"type": "string"}, "max_usd": {"type": "number"}, "max_requests": {"type": "integer"},
                       "approval_note": {"type": "string"}}, ["max_usd", "max_requests"]),
    "hf_batch_status": (t_batch_status, "Остаток лимита и задания пакета.", {"batch_id": {"type": "string"}}, ["batch_id"]),
    "hf_batch_close": (t_batch_close, "Закрыть пакет (дальнейшие отправки запрещены).", {"batch_id": {"type": "string"}}, ["batch_id"]),
    "hf_submit": (t_submit, "Отправить генерацию внутри пакета: оценка цены → проверка лимита → журнал → отправка с Idempotency-Key. "
                            "Для повтора после неизвестного исхода передай intent_id из ошибки.",
                  {"batch_id": {"type": "string"}, "model": {"type": "string"}, "arguments": OBJ,
                   "intent_id": {"type": "string"}}, ["batch_id", "model", "arguments"]),
    "hf_get": (t_get, "Статус задания по request_id (без повторной генерации).", {"request_id": {"type": "string"}}, ["request_id"]),
    "hf_download": (t_download, "Скачать результаты завершённого задания в папку проекта ВНУТРИ рабочей папки (путь относительный, например projects/<id>/outputs/images) и записать .meta.json без ключей и ссылок.",
                    {"request_id": {"type": "string"}, "output_dir": {"type": "string"}, "prefix": {"type": "string"}},
                    ["request_id", "output_dir"]),
    "hf_cancel": (t_cancel, "Отменить задание, пока оно в очереди.", {"request_id": {"type": "string"}}, ["request_id"]),
    "hf_upload": (t_upload, "Загрузить референс из рабочей папки (например references/tee.jpg) в Higgsfield и получить public_url. Файлы вне рабочей папки недоступны. Только с согласия пользователя на передачу файла.",
                  {"path": {"type": "string"}}, ["path"]),
    "hf_workspace_init": (t_workspace_init, "Первичная настройка: создать отдельную рабочую папку (по умолчанию ~/HiggsfieldWork) "
                             "с inbox/, references/, projects/, recipes/. Адаптер работает с файлами только внутри неё. "
                             "Уже настроенную папку не меняет.", {"path": {"type": "string"}}, []),
    "hf_jobs": (t_jobs, "Последние задания из локального журнала — для продолжения после перезапуска.", {"limit": {"type": "integer"}}, []),
}


def handle(msg):
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        return {"protocolVersion": msg.get("params", {}).get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}}, "serverInfo": {"name": "higgsfield-budget-mcp", "version": VERSION}}
    if method == "tools/list":
        return {"tools": [{"name": n, "description": d, "inputSchema": {"type": "object", "properties": p, "required": r}}
                          for n, (_, d, p, r) in TOOLS.items()]}
    if method == "tools/call":
        name = msg["params"]["name"]
        args = msg["params"].get("arguments") or {}
        if name not in TOOLS:
            return {"content": [{"type": "text", "text": f"Неизвестный инструмент {name}"}], "isError": True}
        try:
            res = TOOLS[name][0](args)
            return {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=1)}]}
        except ToolError as e:
            return {"content": [{"type": "text", "text": str(e)}], "isError": True}
        except (KeyError, ValueError, TypeError) as e:
            return {"content": [{"type": "text", "text": f"Неверные параметры: {_redact(e)}"}], "isError": True}
        except Exception as e:  # noqa: BLE001
            return {"content": [{"type": "text", "text": f"Сбой адаптера: {type(e).__name__}: {_redact(e)}"}], "isError": True}
    if method == "ping":
        return {}
    if mid is not None:
        raise LookupError(method)
    return None


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        mid = msg.get("id")
        try:
            result = handle(msg)
        except LookupError:
            if mid is not None:
                sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid,
                                             "error": {"code": -32601, "message": "Method not found"}}) + "\n")
                sys.stdout.flush()
            continue
        if mid is not None and result is not None:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
