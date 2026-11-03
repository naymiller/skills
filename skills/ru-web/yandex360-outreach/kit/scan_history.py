#!/usr/bin/env python3
"""Выгрузка старой переписки для CRM: с кем вы переписывались (они писали вам и вы им) за последние N месяцев.
Нужна, чтобы найти «зависших» клиентов: те, кто спрашивал о заказе, но не купил.

Запуск в Терминале на Маке, из папки sender:
  python3 scan_history.py            # за 12 месяцев
  python3 scan_history.py --months 24
  python3 scan_history.py --help

Читает оба ящика из config.json (входящие и отправленные), ничего не меняет и не отправляет.
Результат: history/threads.json и history/threads.md — по одному блоку на собеседника:
даты, темы и короткие выдержки последних писем. Рассылки, уведомления и no-reply отсеиваются.
"""
import argparse, datetime as dt, email, email.header, imaplib, json, re, sys, time
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from pathlib import Path

if "-h" in sys.argv or "--help" in sys.argv:
    print(__doc__)
    sys.exit(0)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import send_outreach as so  # noqa: E402  (тот же config.json и пароли из Связки ключей)

BULK_LOCAL = re.compile(r"(no-?reply|noreply|do-?not-?reply|notif|notification|mailer|robot|news|newsletter|digest|bounce|postmaster|daemon|billing|support|service|info@yandex|subscribe)", re.I)
BULK_DOMAINS = {"yandex.ru", "ya.ru", "mail.ru", "gmail.com"}  # только для служебных адресов этих сервисов, см. ниже
SERVICE_DOMAINS = re.compile(r"(yandex|google|apple|telegram|tinkoff|tbank|sberbank|ozon|wildberries|avito|hh\.ru|vk\.com|timeweb|reg\.ru|nic\.ru|github|anthropic|claude|higgsfield|figma|notion|zoom|tilda|bitrix|moysklad|cdek|boxberry|pochta|gosuslugi|nalog)", re.I)


def hdr(msg, name):
    try:
        return str(email.header.make_header(email.header.decode_header(msg.get(name, ""))))
    except Exception:
        return msg.get(name, "") or ""


def when(msg):
    try:
        d = parsedate_to_datetime(msg.get("Date"))
        return d if d.tzinfo else d.replace(tzinfo=so.MSK)
    except Exception:
        return None


def clean_body(text):
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(">"):
            continue
        if re.match(r"^(--\s*$|On .+wrote:|.+ писал\(а\):|.+ написал\(а\):|-----Original Message|От:\s|From:\s)", s):
            break
        out.append(s)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


LIST_RE = re.compile(r'\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)$')


def q(name):
    return '"' + name.replace('\\', '\\\\').replace('"', '\\"') + '"'


def folders(M):
    typ, data = M.list()
    inbox, sent = "INBOX", None
    for raw in data or []:
        line = raw.decode(errors="ignore") if isinstance(raw, bytes) else str(raw)
        m = LIST_RE.match(line.strip())
        if not m:
            continue
        name = m.group("name").strip()
        if name.startswith('"') and name.endswith('"'):
            name = name[1:-1]
        if "\\Sent" in m.group("flags") or name.lower() in ("sent", "отправленные", "&bbieqgq,beaemaqybdsenqq9bd0eswq1-"):
            sent = name
    return inbox, sent


class Box:
    """IMAP-ящик, который сам переподключается при обрыве и заново открывает папку."""

    def __init__(self, login, host, pw):
        self.login, self.host, self.pw, self.M, self.folder = login, host, pw, None, None
        self.connect()

    def connect(self):
        try:
            if self.M:
                self.M.logout()
        except Exception:
            pass
        self.M = imaplib.IMAP4_SSL(self.host, 993, timeout=120)
        self.M.login(self.login, self.pw)
        if self.folder:
            self.M.select(q(self.folder), readonly=True)

    def select(self, folder):
        self.folder = folder
        return self.call(lambda M: M.select(q(folder), readonly=True))

    def call(self, fn, tries=5):
        for k in range(tries):
            try:
                return fn(self.M)
            except (imaplib.IMAP4.abort, OSError, TimeoutError) as ex:
                print(f"  обрыв связи ({ex.__class__.__name__}), переподключаюсь… {k + 1}/{tries}")
                time.sleep(3 + 4 * k)
                self.connect()
        raise RuntimeError("Яндекс не даёт дочитать ящик, попробуй позже")

    def close(self):
        try:
            self.M.logout()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=12)
    a = ap.parse_args()
    since = (so.now() - dt.timedelta(days=30 * a.months)).strftime("%d-%b-%Y")
    boxes = [so.CFG["login"]] + [b["login"] for b in so.CFG.get("extra_inboxes", [])]
    own = {b.lower() for b in boxes}
    own_domains = set(so.CFG.get("own_domains", [])) | {so.CFG["login"].split("@")[1]}
    people = {}

    def add(addr, name, direction, d, subj, ref):
        addr = addr.lower().strip()
        if not addr or "@" not in addr or addr in own:
            return
        local, dom = addr.split("@", 1)
        if dom in own_domains or BULK_LOCAL.search(addr) or SERVICE_DOMAINS.search(dom):
            return
        p = people.setdefault(addr, {"email": addr, "name": "", "in": 0, "out": 0, "first": None, "last": None,
                                     "last_in": None, "last_out": None, "subjects": [], "refs": []})
        if name and not p["name"]:
            p["name"] = name
        p[direction] += 1
        if d:
            iso = d.isoformat()
            p["first"] = min(p["first"] or iso, iso)
            p["last"] = max(p["last"] or iso, iso)
            k = "last_in" if direction == "in" else "last_out"
            p[k] = max(p[k] or iso, iso)
        if subj and subj not in p["subjects"]:
            p["subjects"].append(subj)
        p["refs"].append((d.isoformat() if d else "", direction, ref))

    conns = {}
    for login in boxes:
        pw = so.password(login, required=False)
        if not pw:
            print(f"Пропускаю {login}: нет пароля в Связке ключей.")
            continue
        host = so.CFG["imap_host"] if login == so.CFG["login"] else next(
            (b.get("imap_host", "imap.yandex.ru") for b in so.CFG.get("extra_inboxes", []) if b["login"] == login), "imap.yandex.ru")
        try:
            B = Box(login, host, pw)
        except Exception as ex:
            print(f"Не смог войти в {login}: {ex}")
            continue
        conns[login] = B
        inbox, sent = folders(B.M)
        for folder, direction in ((sent, "out"), (inbox, "in")):
            if not folder:
                print(f"{login}: не нашёл папку «Отправленные», смотрю только входящие.")
                continue
            try:
                B.select(folder)
                typ, data = B.call(lambda M: M.uid("SEARCH", None, "SINCE", since))
            except Exception as ex:
                print(f"{login} / {folder}: не открылась ({ex}), пропускаю")
                continue
            uids = data[0].split()
            print(f"{login} / {folder}: писем за период {len(uids)}")
            step = 100
            for i in range(0, len(uids), step):
                chunk = b",".join(uids[i:i + step]).decode()
                typ, resp = B.call(lambda M: M.uid("FETCH", chunk, "(UID BODY.PEEK[HEADER.FIELDS (FROM TO CC DATE SUBJECT LIST-UNSUBSCRIBE PRECEDENCE AUTO-SUBMITTED)])"))
                for item in resp or []:
                    if not isinstance(item, tuple):
                        continue
                    m = re.search(rb"UID (\d+)", item[0])
                    if not m:
                        continue
                    uid = m.group(1).decode()
                    h = email.message_from_bytes(item[1])
                    if direction == "in" and (h.get("List-Unsubscribe") or (h.get("Precedence", "").lower() in ("bulk", "list", "junk"))
                                              or h.get("Auto-Submitted", "no").lower() != "no"):
                        continue
                    d, subj = when(h), hdr(h, "Subject")
                    ref = (login, folder, uid)
                    if direction == "in":
                        n, ad = parseaddr(hdr(h, "From"))
                        add(ad, n, "in", d, subj, ref)
                    else:
                        for n, ad in getaddresses([hdr(h, "To"), hdr(h, "Cc")]):
                            add(ad, n, "out", d, subj, ref)
                if (i // step) % 10 == 9:
                    print(f"  прочитано {min(i + step, len(uids))} из {len(uids)}")

    # оставляем только настоящую переписку: и они писали, и вы отвечали
    plan = set(so.load_plan())
    keep = [p for p in people.values() if p["in"] >= 1 and p["out"] >= 1]
    keep.sort(key=lambda p: p["last"] or "", reverse=True)

    # тексты последних трёх писем каждого собеседника
    print(f"Собеседников с перепиской в обе стороны: {len(keep)}. Читаю последние письма каждого…")
    for n_p, p in enumerate(keep, 1):
        p["in_outreach"] = p["email"] in plan
        p["messages"] = []
        for iso, direction, (login, folder, uid) in sorted(p["refs"], reverse=True)[:3]:
            B = conns.get(login)
            if not B:
                continue
            try:
                if B.folder != folder:
                    B.select(folder)
                typ, resp = B.call(lambda M: M.uid("FETCH", uid, "(BODY.PEEK[])"))
                raw = next(x[1] for x in resp if isinstance(x, tuple))
                msg = email.message_from_bytes(raw)
                body = clean_body(so._text(msg))[:1500]
                p["messages"].append({"date": iso, "direction": "от них" if direction == "in" else "от вас",
                                      "subject": hdr(msg, "Subject"), "text": body, "box": login})
            except Exception as ex:
                p["messages"].append({"date": iso, "direction": "от них" if direction == "in" else "от вас", "subject": "", "text": f"(не прочиталось: {ex})", "box": login})
        p["box"] = p["messages"][0]["box"] if p["messages"] else ""
        del p["refs"]
        p["subjects"] = p["subjects"][-8:]
        if n_p % 25 == 0:
            print(f"  {n_p} из {len(keep)}")
    for B in conns.values():
        B.close()

    out = HERE / "history"
    out.mkdir(exist_ok=True)
    (out / "threads.json").write_text(json.dumps(keep, ensure_ascii=False, indent=1), encoding="utf-8")
    md = [f"# Переписка за {a.months} мес. — {len(keep)} собеседников\n"]
    for p in keep:
        md.append(f"## {p['name'] or p['email']} <{p['email']}>\nписем от них {p['in']}, от вас {p['out']}; первое {str(p['first'])[:10]}, последнее {str(p['last'])[:10]}"
                  + ("; есть в рассылке" if p["in_outreach"] else "") + "\nТемы: " + " | ".join(p["subjects"]) + "\n")
        for m in p["messages"]:
            md.append(f"**{m['date'][:16]} · {m['direction']} · {m['subject']}**\n\n{m['text']}\n")
    (out / "threads.md").write_text("\n".join(md), encoding="utf-8")
    print(f"Готово: {len(keep)} собеседников с перепиской. Файлы: history/threads.json и history/threads.md")


if __name__ == "__main__":
    main()
