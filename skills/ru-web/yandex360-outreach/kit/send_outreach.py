#!/usr/bin/env python3
"""Холодная рассылка цепочек писем через Яндекс 360 (SMTP) с контролем ответов (IMAP).

Запуск (в Терминале на Маке, из папки sender):
  python3 send_outreach.py               # пробный прогон: что ушло бы сегодня, ничего не отправляет (по умолчанию)
  python3 send_outreach.py --send        # отправить сегодняшнюю порцию (вызывает send_today.command после слова «да»)
  python3 send_outreach.py --auto        # то же для launchd; работает только при auto_send_approved = true в config.json
  python3 send_outreach.py --check       # только проверить ответы, отписки и недоставленные
  python3 send_outreach.py --report      # сводка по статусам и по ящикам
  python3 send_outreach.py --test        # одно тестовое письмо каждому ящику себе (проверка логина и подписи)
  python3 send_outreach.py --help        # эта справка

Пароль приложения Яндекса хранится в Связке ключей macOS (сервис из config.json, keychain_service),
в файлы, логи и чат не пишется. Скрипт использует только стандартную библиотеку Python 3.9+.
"""
import sys
if "-h" in sys.argv or "--help" in sys.argv:
    print(__doc__)
    sys.exit(0)
import argparse, csv, shutil, datetime as dt, email, email.header, imaplib, json, random, re, smtplib, ssl, subprocess, sys, time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr, formatdate
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
CFG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
MSK = ZoneInfo("Europe/Moscow")
LOG = HERE / CFG.get("log_file", "outreach_log.csv")
STATUS = HERE / CFG.get("status_file", "lead_status.json")
FREEMAIL = {"gmail.com", "mail.ru", "yandex.ru", "ya.ru", "bk.ru", "list.ru", "inbox.ru", "rambler.ru", "icloud.com", "outlook.com", "hotmail.com"}
# Отписка: первая строка ответа начинается со стоп-фразы. «Нет ли…» (вопрос) отпиской не считается.
STOP_RE = re.compile(r"^\W*(?:не актуально|неактуально|не пишите|отпишите|удалите|unsubscribe|нет(?!\s+ли)(?![^\n]*\?)\b)", re.I)


def password(login=None, required=True):
    login = login or CFG["login"]
    cmd = ["security", "find-generic-password", "-s", CFG["keychain_service"], "-a", login, "-w"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:  # нет утилиты security (не macOS)
        if required:
            sys.exit(f"Пароль не найден: нет доступа к Связке ключей для {login}.")
        return None
    if r.returncode != 0:  # вне сессии пользователя связка ключей не видна — пробуем путь явно
        kc = Path.home() / "Library/Keychains/login.keychain-db"
        r = subprocess.run(cmd + [str(kc)], capture_output=True, text=True)
    if r.returncode != 0:
        if required:
            sys.exit(f"Пароль не найден в Связке ключей для {login}.")
        return None
    return r.stdout.strip()


def now():
    return dt.datetime.now(MSK)


def load_plan():
    plan = {}
    for r in csv.DictReader(open(HERE / CFG["emails_file"], encoding="utf-8")):
        plan.setdefault(r["to"].strip().lower(), {})[int(r["touch"])] = r
    return plan


def load_log():
    sent = {}
    if LOG.exists():
        for r in csv.DictReader(open(LOG, encoding="utf-8")):
            if r["result"] == "sent":
                sent.setdefault(r["to"], {})[int(r["touch"])] = dt.datetime.fromisoformat(r["sent_at"])
    return sent


LOG_FIELDS = ["sent_at", "to", "company", "touch", "subject", "result", "message_id", "mailbox"]


def migrate_log():
    """Старый журнал без колонки mailbox: дописываем её (все прежние отправки были с основного ящика)."""
    if not LOG.exists():
        return
    with open(LOG, encoding="utf-8", newline="") as f:
        head = f.readline()
    if "mailbox" in head:
        return
    shutil.copy(LOG, LOG.with_name(LOG.name + ".bak"))
    with open(LOG, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    with open(LOG, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        w.writeheader()
        for r in rows:
            r["mailbox"] = CFG["login"]
            w.writerow(r)


def log(row):
    new = not LOG.exists()
    row.setdefault("mailbox", "")
    with open(LOG, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)


MB_STATE = HERE / "mailbox_state.json"
ALERTS = HERE / "mailbox_alerts.txt"


def mailboxes():
    items = CFG.get("mailboxes") or [{"login": CFG["login"]}]
    return [b["login"] for b in items]


def load_mb_state():
    return json.loads(MB_STATE.read_text(encoding="utf-8")) if MB_STATE.exists() else {}


def pause_mailbox(mb, hours, reason):
    st = load_mb_state()
    st[mb] = {"paused_until": (now() + dt.timedelta(hours=hours)).isoformat(), "reason": reason, "at": now().isoformat()}
    MB_STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    line = f"{now().isoformat()}  {mb} ПАУЗА {hours} ч: {reason}"
    with open(ALERTS, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print("!!!", line)


def is_paused(mb):
    u = load_mb_state().get(mb, {}).get("paused_until")
    return bool(u) and now() < dt.datetime.fromisoformat(u)


def mb_stats():
    """first — дата первой отправки ящика; today_n — отправлено сегодня; owner — ящик первого касания лида;
    recent — лиды, которым ящик писал за последние 14 дней."""
    first, today_n, owner, recent = {}, {}, {}, {}
    if not LOG.exists():
        return first, today_n, owner, recent
    today = now().date()
    for r in csv.DictReader(open(LOG, encoding="utf-8")):
        if r["result"] != "sent":
            continue
        mb = r.get("mailbox") or CFG["login"]
        d = dt.datetime.fromisoformat(r["sent_at"]).astimezone(MSK).date()
        first[mb] = min(first.get(mb, d), d)
        if d == today:
            today_n[mb] = today_n.get(mb, 0) + 1
        owner.setdefault(r["to"], mb)
        if (today - d).days <= 14:
            recent.setdefault(mb, set()).add(r["to"])
    return first, today_n, owner, recent


def cap_for(mb, first):
    """Прогрев: суточный потолок ящика растёт с возрастом (дней с первой отправки)."""
    steps = CFG.get("warmup", [[0, 10], [3, 15], [7, 20], [14, 30]])
    age = (now().date() - first[mb]).days if mb in first else 0
    cap = steps[0][1]
    for d, c in steps:
        if age >= d:
            cap = c
    return min(cap, CFG.get("daily_limit", 30))


def guard(status):
    """Автопауза ящика, если доля недоставленных среди его адресатов за 14 дней выше порога."""
    g = CFG.get("guard", {})
    _, _, _, recent = mb_stats()
    for mb, leads in recent.items():
        if is_paused(mb) or len(leads) < g.get("min_sample", 10):
            continue
        bad = sum(1 for e in leads if status.get(e, {}).get("state") == "bounced")
        if bad / len(leads) > g.get("max_bounce_rate", 0.08):
            pause_mailbox(mb, g.get("pause_hours", 72), f"недоставлено {bad} из {len(leads)} адресатов за 14 дней")


def plan_today(plan, sent, status, limit, avail):
    """Что и с какого ящика уйдёт сегодня. Цепочка лида всегда идёт с того же ящика, что и первое письмо."""
    first, today_n, owner, _ = mb_stats()
    cap = {mb: (0 if is_paused(mb) else max(0, cap_for(mb, first) - today_n.get(mb, 0))) for mb in avail}
    out, skipped = [], []
    for e, t in due_today(plan, sent, status):
        if len(out) >= limit:
            break
        mb = owner.get(e)
        if mb is None:
            mb = max(avail, key=lambda m: cap[m]) if avail else None
            if mb is None or cap[mb] <= 0:
                continue
        elif mb not in avail or cap[mb] <= 0:
            skipped.append((e, t, mb))
            continue
        cap[mb] -= 1
        out.append((e, t, mb))
    return out, skipped, cap


def load_status():
    return json.loads(STATUS.read_text(encoding="utf-8")) if STATUS.exists() else {}


def save_status(s):
    STATUS.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")


SEEN = HERE / "seen_messages.json"
LEAD_SUBJ = re.compile(CFG.get("lead_subject_re", r"заявк|новый лид|новая заявка|lead|обратн\w* связ"), re.I)
OWN_DOMAINS = set(CFG.get("own_domains", []))


def _text(msg):
    body = ""
    for part in msg.walk():
        if part.get_content_type() == "text/plain" and not part.get_filename():
            try:
                body += part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "ignore")
            except Exception:
                pass
    if not body:
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    html = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "ignore")
                    body += re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", " ", html))
                except Exception:
                    pass
    return re.sub(r"[ \t]+", " ", body)


def _hdr(msg, name):
    try:
        return str(email.header.make_header(email.header.decode_header(msg.get(name, ""))))
    except Exception:
        return msg.get(name, "")


def _save(folder, key, msg, frm, body, extra):
    d = HERE / folder
    d.mkdir(exist_ok=True)
    safe = re.sub(r"[^a-z0-9@._-]+", "_", key.lower())[:80]
    stamp = now().strftime("%Y-%m-%d_%H%M")
    f = d / f"{stamp}_{safe}.txt"
    i = 2
    while f.exists():
        f = d / f"{stamp}_{safe}_{i}.txt"; i += 1
    head = "\n".join(f"{k}: {v}" for k, v in extra.items())
    f.write_text(f"{head}\nОт: {frm}\nТема: {_hdr(msg, 'Subject')}\nДата письма: {msg.get('Date', '')}\n\n{body[:8000]}", encoding="utf-8")


def check_replies(status, plan, pwd):
    """Читает все ящики: ответы лидов (сохраняет каждое письмо, первая реакция останавливает цепочку),
    отписки, недоставленные и заявки с сайтов. Повторно одно и то же письмо не сохраняет."""
    leads = set(plan)
    domains = {e.split("@")[1]: e for e in leads if e.split("@")[1] not in FREEMAIL}
    since = (now() - dt.timedelta(days=CFG.get("imap_lookback_days", 30))).strftime("%d-%b-%Y")
    seen = set(json.loads(SEEN.read_text(encoding="utf-8"))) if SEEN.exists() else set()
    found = 0
    boxes = [(l, CFG["imap_host"], p) for l, p in pwd.items()]
    for b in CFG.get("extra_inboxes", []):
        if b["login"] in pwd:
            continue
        p = password(b["login"], required=False)
        if p:
            boxes.append((b["login"], b.get("imap_host", "imap.yandex.ru"), p))
        else:
            print(f"Пропускаю {b['login']}: нет пароля в Связке ключей.")
    for login, host, pw in boxes:
        try:
            M = imaplib.IMAP4_SSL(host, 993)
            M.login(login, pw)
        except Exception as ex:
            print(f"Не смог войти в {login}: {ex}")
            continue
        with M:
            M.select("INBOX", readonly=True)
            typ, data = M.search(None, "SINCE", since)
            for num in data[0].split():
                typ, hd = M.fetch(num, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM SUBJECT)])")
                h = email.message_from_bytes(hd[0][1])
                mid = (h.get("Message-ID") or f"{login}:{num.decode()}").strip()
                if mid in seen:
                    continue
                frm = parseaddr(h.get("From", ""))[1].lower()
                subj = _hdr(h, "Subject")
                fdom = frm.split("@")[-1]
                lead = frm if frm in leads else domains.get(fdom)
                bounce = "mailer-daemon" in frm or "postmaster" in frm
                site_lead = (not lead) and (fdom in OWN_DOMAINS or bool(LEAD_SUBJ.search(subj)))
                if not (lead or bounce or site_lead):
                    seen.add(mid)
                    continue
                typ, msg_data = M.fetch(num, "(BODY.PEEK[])")
                msg = email.message_from_bytes(msg_data[0][1])
                seen.add(mid)
                if msg.get("Auto-Submitted", "no").lower() != "no" or msg.get("X-Autoreply") or msg.get("X-Autorespond"):
                    continue
                body = _text(msg)
                if bounce:
                    for e in leads:
                        if e in body.lower() and status.get(e, {}).get("state") != "bounced":
                            status[e] = {"state": "bounced", "at": now().isoformat()}
                            found += 1
                    continue
                if site_lead:
                    _save("leads_site", fdom, msg, frm, body, {"Ящик": login, "Тип": "заявка с сайта или входящий запрос"})
                    found += 1
                    continue
                first = next((l.strip() for l in body.splitlines() if l.strip() and not l.startswith(">")), "")
                state = "unsubscribed" if len(first) < 120 and STOP_RE.match(first) else "replied"
                if status.get(lead, {}).get("state") not in ("replied", "unsubscribed"):
                    status[lead] = {"state": state, "at": now().isoformat(), "from": frm, "subject": subj}
                _save("replies", lead, msg, frm, body, {"Компания": plan[lead][1]["company"], "Лид": lead,
                                                        "Ящик": login, "Статус": state})
                found += 1
    save_status(status)
    SEEN.write_text(json.dumps(sorted(seen), ensure_ascii=False), encoding="utf-8")
    return found


def due_today(plan, sent, status):
    """Сначала догоняющие письма по графику, потом новые первые письма."""
    today = now()
    follow, first = [], []
    for e, touches in plan.items():
        if status.get(e, {}).get("state") in ("replied", "unsubscribed", "bounced"):
            continue
        s = sent.get(e, {})
        if not s:
            first.append((e, 1))
            continue
        nxt = max(s) + 1
        if nxt not in touches:
            continue
        start = s[1]
        if (today.date() - start.date()).days >= int(touches[nxt]["send_day"]):
            follow.append((e, nxt))
    random.Random(now().date().isoformat()).shuffle(first)  # порядок фиксирован на день: прогон и отправка совпадают
    order = {"partners": 0, "events": 1, "companies": 2}
    first.sort(key=lambda x: order.get(plan[x[0]][1]["segment"], 9))
    return follow + first


def build(r, to, login=None):
    login = login or CFG["login"]
    m = EmailMessage()
    m["From"] = formataddr((CFG["from_name"], login))
    m["To"] = to
    m["Subject"] = r["subject"]
    m["Date"] = formatdate(localtime=True)
    m["Message-ID"] = make_msgid(domain=login.split("@")[1])
    m["List-Unsubscribe"] = f"<mailto:{login}?subject=unsubscribe>"
    m.set_content(r["body"].replace("{sender_name}", CFG["from_name"]))
    if int(r["touch"]) in CFG.get("attach_kp_on_touch", []):
        pdf = HERE / CFG["kp_file"]
        m.add_attachment(pdf.read_bytes(), maintype="application", subtype="pdf", filename=CFG["kp_attachment_name"])
    return m


def in_window():
    t = now()
    return t.weekday() < 5 and CFG["send_hours"][0] <= t.hour < CFG["send_hours"][1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--auto", action="store_true", help="отправка по расписанию launchd (только при auto_send_approved)")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--limit", type=int, default=CFG.get("daily_limit_total", 60))
    ap.add_argument("--anytime", action="store_true", help="не проверять рабочее время")
    a = ap.parse_args()

    migrate_log()
    plan, sent, status = load_plan(), load_log(), load_status()

    if a.report:
        st = {}
        for e in plan:
            k = status.get(e, {}).get("state") or (f"в цепочке, отправлено {max(sent[e])} из 4" if e in sent else "не начато")
            st[k] = st.get(k, 0) + 1
        for k, v in sorted(st.items()):
            print(f"{v:4d}  {k}")
        for e, s in status.items():
            print(f"  {s['state']:12s} {e}  {s.get('subject', '')[:60]}")
        first, today_n, _, recent = mb_stats()
        print("Ящики:")
        for mb in mailboxes():
            u = load_mb_state().get(mb, {}).get("paused_until")
            pz = f", ПАУЗА до {u[:16]}" if u and is_paused(mb) else ""
            print(f"  {mb}: сегодня {today_n.get(mb, 0)}, потолок {cap_for(mb, first)}, адресатов за 14 дн. {len(recent.get(mb, ()))}{pz}")
        return

    if not (a.send or a.auto or a.check or a.test):
        avail = [mb for mb in mailboxes() if password(mb, required=False)]
        note = ""
        if not avail:  # нет Связки ключей (например, проверка на другом компьютере): показываем очередь без пароля
            avail, note = mailboxes(), " (пароли не проверялись: очередь показана для контроля)"
        guard(status)
        q, skipped, cap = plan_today(plan, sent, status, a.limit, avail)
        print(f"ПРОБНЫЙ ПРОГОН. Сегодня ушло бы {len(q)} писем (лимит {a.limit}). Ящики: {', '.join(avail) or 'нет'}{note}")
        for e, t, mb in q:
            print(f"  письмо {t}  {plan[e][t]['company'][:30]:30s} {e}  ← {mb}")
        for e, t, mb in skipped:
            print(f"  отложено (ящик {mb} на паузе, в лимите или без пароля): письмо {t} → {e}")
        print("Чтобы отправить: python3 send_outreach.py --send")
        return

    if a.auto and not CFG.get("auto_send_approved"):
        sys.exit("Автоотправка не включена (auto_send_approved = false в config.json). "
                 "Сначала первая волна вручную через send_today.command, потом владелец ставит true.")
    if "@" not in CFG["login"] or not CFG["login"].isascii():
        sys.exit("Впиши адрес ящика в config.json (поле login).")
    pwds = {}
    for mb in mailboxes():
        p = password(mb, required=(mb == CFG["login"]))
        if p:
            pwds[mb] = p
        else:
            print(f"Ящик {mb}: нет пароля в Связке ключей, пропускаю (запусти setup.command).")

    if a.test:
        r = next(iter(plan.values()))[2]
        for mb, pw in pwds.items():
            m = build(r, mb, mb)
            m.replace_header("Subject", "[ТЕСТ] " + r["subject"])
            with smtplib.SMTP_SSL(CFG["smtp_host"], 465, context=ssl.create_default_context()) as S:
                S.login(mb, pw)
                S.send_message(m)
            print("Тестовое письмо (касание 2, с КП) отправлено на", mb)
        return

    n = check_replies(status, plan, pwds)
    print(f"Проверил входящие: новых ответов/недоставленных {n}.")
    guard(status)
    if a.check:
        return

    if not in_window() and not a.anytime:
        sys.exit(f"Сейчас не рабочее время (пн–пт, {CFG['send_hours'][0]}:00–{CFG['send_hours'][1]}:00 МСК). Ничего не отправлено.")

    avail = list(pwds)
    q, skipped, _ = plan_today(plan, sent, status, a.limit, avail)
    print(f"К отправке сегодня: {len(q)}" + (f", отложено {len(skipped)}" if skipped else ""))
    ctx = ssl.create_default_context()
    broken = set()
    for i, (e, t, mb) in enumerate(q):
        if mb in broken:
            continue
        r = plan[e][t]
        m = build(r, e, mb)
        try:
            with smtplib.SMTP_SSL(CFG["smtp_host"], 465, context=ctx) as S:
                S.login(mb, pwds[mb])
                S.send_message(m)
            res = "sent"
        except smtplib.SMTPRecipientsRefused:
            res = "refused"
            status[e] = {"state": "bounced", "at": now().isoformat()}
            save_status(status)
        except Exception as ex:
            print(f"ОШИБКА ящика {mb}: {ex}")
            log({"sent_at": now().isoformat(), "to": e, "company": r["company"], "touch": t, "subject": r["subject"], "result": f"error: {ex}", "message_id": "", "mailbox": mb})
            pause_mailbox(mb, CFG.get("guard", {}).get("error_pause_hours", 20), f"ошибка отправки: {str(ex)[:120]}")
            broken.add(mb)
            continue
        log({"sent_at": now().isoformat(), "to": e, "company": r["company"], "touch": t, "subject": r["subject"], "result": res, "message_id": m["Message-ID"], "mailbox": mb})
        print(f"  [{i + 1}/{len(q)}] письмо {t} → {e} ← {mb}: {res}")
        if res == "refused":
            guard(status)
        if i < len(q) - 1:
            time.sleep(random.randint(*CFG["pause_seconds"]))
    print("Готово.")

if __name__ == "__main__":
    main()
