#!/usr/bin/env python3
"""CSV-CRM холодной рассылки: один файл crm/crm.csv, который открывается в Excel или Numbers.

  python3 crm_sync.py            # пересобрать crm/crm.csv из журнала отправок, статусов и папки replies/
  python3 crm_sync.py --help

Автополя (пересчитываются каждый запуск): email, company, segment, stage, touches_sent, last_touch_at,
reply_state, next_step. Ручные поля (сохраняются как есть): manual_stage, amount, notes.
manual_stage имеет приоритет над автоматической стадией: ставь его вручную для quote, paid, shipped, production.
Стадии: queue → sent → replied → quote → paid → shipped; lost (отписка), bounced (недоставлено).
Ничего не отправляет и не меняет журнал отправок.
"""
import csv, datetime as dt, json, re, sys
from pathlib import Path

if "-h" in sys.argv or "--help" in sys.argv:
    print(__doc__)
    sys.exit(0)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import send_outreach as so  # noqa: E402  (тот же config.json)

OUT_DIR = so.HERE / "crm"
OUT = OUT_DIR / "crm.csv"
FIELDS = ["email", "company", "segment", "stage", "touches_sent", "last_touch_at", "reply_state", "next_step",
          "manual_stage", "amount", "notes"]
MANUAL = ("manual_stage", "amount", "notes")


def read_old():
    if not OUT.exists():
        return {}
    with open(OUT, encoding="utf-8-sig", newline="") as f:
        return {r["email"]: r for r in csv.DictReader(f)}


def replied_emails():
    found = set()
    d = so.HERE / "replies"
    if d.exists():
        for f in d.glob("*.txt"):
            m = re.search(r"^Лид: (\S+)", f.read_text(encoding="utf-8", errors="ignore"), re.M)
            if m:
                found.add(m.group(1).lower())
    return found


def main():
    plan, sent, status = so.load_plan(), so.load_log(), so.load_status()
    old = read_old()
    replied = replied_emails()
    rows = []
    for e in sorted(plan):
        first = plan[e][1]
        touches = sent.get(e, {})
        st = status.get(e, {}).get("state")
        if st == "unsubscribed":
            auto, nxt = "lost", "Отписался: не писать"
        elif st == "bounced":
            auto, nxt = "bounced", "Недоставлено: адрес не использовать"
        elif e in replied or st == "replied":
            auto, nxt = "replied", "Ответить: см. папку replies/"
        elif touches:
            n = max(touches) + 1
            auto = "sent"
            nxt = f"Письмо {n} по графику" if n <= 4 else "Цепочка завершена, ждём ответа"
        else:
            auto, nxt = "queue", "Письмо 1"
        o = old.get(e, {})
        rows.append({
            "email": e, "company": first["company"], "segment": first["segment"],
            "stage": o.get("manual_stage") or auto, "touches_sent": len(touches),
            "last_touch_at": max(touches.values()).isoformat(timespec="minutes") if touches else "",
            "reply_state": st or "", "next_step": nxt,
            "manual_stage": o.get("manual_stage", ""), "amount": o.get("amount", ""), "notes": o.get("notes", ""),
        })
    # ручные строки, которых нет в базе (например, заказ от клиента из переписки), не теряем
    for e, o in old.items():
        if e not in plan and any(o.get(k) for k in MANUAL):
            rows.append({k: o.get(k, "") for k in FIELDS})
    OUT_DIR.mkdir(exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(OUT)
    counts = {}
    for r in rows:
        counts[r["stage"]] = counts.get(r["stage"], 0) + 1
    print(f"crm/crm.csv: {len(rows)} строк. " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    print("Сверка: сумма touches_sent =", sum(int(r["touches_sent"]) for r in rows), "(должна совпадать с числом строк журнала 'sent')")


if __name__ == "__main__":
    main()
