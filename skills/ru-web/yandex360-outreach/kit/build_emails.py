#!/usr/bin/env python3
"""Сборка CSV писем для send_outreach.py из markdown-шаблона цепочек.

  python3 build_emails.py leads.csv emails_template.md emails_wave1.csv [волна]
  python3 build_emails.py --help

leads.csv (см. leads_example.csv): колонки company, segment (partners|companies|events), email, hook, offer, wave.
  hook  — проверяемый факт с сайта компании (первая персональная строка); пусто — абзац с {hook} не выводится.
  offer — одно предложение под задачу компании; пусто — абзац с {offer} не выводится.
Шаблон: разделы «## Партнёры» / «## Компании» / «## Мероприятия», внутри четыре «### Письмо N · день D · тема: «…»»
и текст строками «> …». Плейсхолдер {sender_name} заменяет send_outreach.py из config.json при отправке.
Дни касаний (0/3/7/14) меняются в DELAYS, если цепочка другая.
После сборки проверь CSV: в теле не должно остаться {плейсхолдеров}, кроме {sender_name}.
"""
import csv, re, sys

if "-h" in sys.argv or "--help" in sys.argv:
    print(__doc__)
    sys.exit(0)

LEADS, MD, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
WAVE = sys.argv[4] if len(sys.argv) > 4 else "1"
SEGS = {"Партнёры": "partners", "Компании": "companies", "Мероприятия": "events"}
DELAYS = [0, 3, 7, 14]
LEFT_OVER = re.compile(r"\{(?!sender_name\})\w+\}")


def parse(md):
    seq, seg, cur, buf = {}, None, None, []

    def flush():
        if cur and buf:
            seq.setdefault(seg, []).append((cur, "\n".join(buf).strip()))

    for line in md.splitlines():
        if line.startswith("## "):
            flush(); cur, buf = None, []
            seg = next(v for k, v in SEGS.items() if line[3:].startswith(k))
        elif line.startswith("### "):
            flush(); buf = []
            cur = re.search(r"тема: «(.+?)»", line).group(1)
        elif line.startswith(">"):
            buf.append(line[2:] if line.startswith("> ") else "")
    flush()
    return seq


def fill(body, values):
    """Подставляет {hook} и {offer}; абзац, где значение пустое, целиком убирается."""
    paras = []
    for para in body.split("\n\n"):
        keep = True
        for key, val in values.items():
            if "{" + key + "}" in para:
                if val.strip():
                    para = para.replace("{" + key + "}", val.strip())
                else:
                    keep = False
        if keep:
            paras.append(para)
    return "\n\n".join(paras)


def render(seg, n, r, seq):
    subj, body = seq[seg][n]
    return subj, fill(body, {"hook": r.get("hook", ""), "offer": r.get("offer", ""), "company": r.get("company", "")})


if __name__ == "__main__":
    seq = parse(open(MD, encoding="utf-8").read())
    assert {k: len(v) for k, v in seq.items()} == {"partners": 4, "companies": 4, "events": 4}, seq.keys()
    rows = [r for r in csv.DictReader(open(LEADS, encoding="utf-8")) if r["wave"] == WAVE]
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["company", "segment", "to", "touch", "send_day", "subject", "body"])
        for r in rows:
            for n in range(4):
                s, b = render(r["segment"], n, r, seq)
                left = LEFT_OVER.findall(b)
                assert not left, (r["company"], n, left)
                w.writerow([r["company"], r["segment"], r["email"], n + 1, DELAYS[n], s, b])
    print(len(rows), "leads ->", len(rows) * 4, "emails")
