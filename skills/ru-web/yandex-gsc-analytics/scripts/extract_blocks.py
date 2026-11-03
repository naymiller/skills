#!/usr/bin/env python3
"""Вытаскивает блоки @@BLOCK имя@@ ... @@END@@ из транскрипта сессии в файлы.

Браузерный агент печатает выгрузку блоками (см. references/export-playbook.md),
этот скрипт собирает их в папку. Поздний блок с тем же именем перекрывает ранний.

  python3 scripts/extract_blocks.py <папка_вывода> [файл_транскрипта.jsonl]
  python3 scripts/extract_blocks.py --help

Без второго аргумента берётся самый свежий *.jsonl в ~/.claude/projects/*/.
Пример: python3 scripts/extract_blocks.py raw
"""
import argparse, json, re, sys, os, glob

ap = argparse.ArgumentParser(description='Извлечь блоки @@BLOCK@@ из транскрипта в файлы.')
ap.add_argument('outdir', help='папка, куда писать файлы (создаётся)')
ap.add_argument('transcript', nargs='?', help='.jsonl транскрипта; по умолчанию — самый свежий')
args = ap.parse_args()

if args.transcript:
    transcript = args.transcript
else:
    cands = glob.glob(os.path.expanduser('~/.claude/projects/*/*.jsonl'))
    if not cands:
        sys.exit('Не найден транскрипт: укажи путь вторым аргументом.')
    transcript = max(cands, key=os.path.getmtime)

outdir = args.outdir
os.makedirs(outdir, exist_ok=True)

pat = re.compile(r'@@BLOCK ([^@]+)@@\n(.*?)\n@@END@@', re.S)
blocks = {}


def texts(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, list):
        for x in obj:
            yield from texts(x)
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from texts(v)


for line in open(transcript, encoding='utf-8'):
    try:
        d = json.loads(line)
    except Exception:
        continue
    if d.get('type') != 'user':
        continue
    for t in texts(d.get('message', {}).get('content')):
        if '@@BLOCK' not in t:
            continue
        s = t
        # результат инструмента часто приходит как JSON-строка: "...\n..."
        for cand in re.findall(r'"((?:[^"\\]|\\.)*@@BLOCK(?:[^"\\]|\\.)*)"', t, re.S):
            try:
                s += '\n' + json.loads('"' + cand + '"')
            except Exception:
                pass
        for name, body in pat.findall(s):
            if '\\n' in body and '\n' not in body.strip():
                continue  # ещё экранированная копия; раскодированная побеждает
            blocks[name.strip()] = body

for name, body in blocks.items():
    site, report, variant = (name.split('|') + ['', ''])[:3]
    ext = 'json' if variant == 'json' else 'txt'
    fn = f"{site}__{report}__{variant}.{ext}".replace('/', '_')
    with open(os.path.join(outdir, fn), 'w', encoding='utf-8') as f:
        f.write(body)

print(len(blocks), 'blocks')
for n in sorted(blocks):
    print(' ', n, len(blocks[n]))
