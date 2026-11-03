#!/usr/bin/env python3
"""Цветоделение ч/б или тонового арта по градациям яркости → векторные плёнки для шелкографии.

  python3 sep_tones.py art.png --out OUTDIR --shirt black --height-mm 420  # пример: 3 краски на чёрной футболке
  python3 sep_tones.py art.png --out OUTDIR --thresholds 55,120,185 --crop 10,400,1280,2390
  python3 sep_tones.py art.png --out OUTDIR --shirt white --classes 3 --names "Чёрный,Серый"

Классы яркости 0..K-1 (0 — самый тёмный). На чёрной футболке класс 0 = ткань (не печатается),
на белой — класс K-1 = ткань. Пороги без --thresholds: multi-Otsu по пикселям арта.
Сначала смотри OUTDIR/preview_posterized.png и подбирай пороги, потом трассировка (она медленная).
  --preview-only  — только постеризация, без трассировки.

Выход OUTDIR/: composite.svg и .pdf (слои по краскам + слой «ткань, не печатать» + метки совмещения),
sep_<N>_<name>.pdf — плёнки (чёрное на белом), preview.png, separations.zip.
Треппинг: каждая краска заходит на --trap-px в соседние более тёмные печатные (≈0,2 мм при 1 px на 0,2 мм).
Нужно: pip install --break-system-packages potracer cairosvg scikit-image scipy
"""
import argparse, os, sys, zipfile
from multiprocessing import Pool
import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage as nd


def posterize(a_):
    im = Image.open(a_.src).convert('L')
    if a_.crop:
        im = im.crop(tuple(int(v) for v in a_.crop.split(',')))
    if a_.blur > 0:
        im = im.filter(ImageFilter.GaussianBlur(a_.blur))
    if a_.scale != 1:
        im = im.resize((int(im.width * a_.scale), int(im.height * a_.scale)), Image.LANCZOS)
    g = np.array(im)
    if a_.thresholds:
        t = [int(x) for x in a_.thresholds.split(',')]
    else:
        from skimage.filters import threshold_multiotsu
        v = g[g > 25] if a_.shirt == 'black' else g[g < 230]
        k = a_.classes - 1                                   # печатных классов (без ткани)
        t = [int(x) for x in threshold_multiotsu(v if v.size > 100 else g, classes=k)] if k >= 2 else []
        t = [25] + t if a_.shirt == 'black' else t + [230]   # крайний класс = ткань
        print('пороги multi-Otsu:', t, '(подбери вручную через --thresholds, если пятна)')
    lab = np.digitize(g, t).astype(np.uint8)
    K = len(t) + 1
    minA = max(1, int(a_.min_island * a_.scale * a_.scale))
    for _ in range(2):                                  # острова меньше minA px → медиана соседей
        md = nd.median_filter(lab, 3)
        for c in range(K):
            m = lab == c; L, n = nd.label(m)
            if n:
                sz = nd.sum(m, L, range(1, n + 1)); small = np.isin(L, np.where(sz < minA)[0] + 1)
                lab[small] = md[small]
    return g, lab, t, K


def trace_one(args):
    import potrace
    lab_path, c, printed_darker, trap = args
    lab = np.load(lab_path); m = lab == c
    if trap and printed_darker:
        m = m | (nd.binary_dilation(m, iterations=trap) & np.isin(lab, printed_darker))
    bm = potrace.Bitmap(~m)                     # potracer инвертирует вход: передаём ~mask
    pl = bm.trace(turdsize=2, turnpolicy=potrace.POTRACE_TURNPOLICY_MINORITY, alphamax=1.0, opticurve=True, opttolerance=0.2)
    d = []
    for cv in pl:
        s = cv.start_point; p = [f'M{s.x:.2f},{s.y:.2f}']
        for seg in cv.segments:
            if seg.is_corner:
                p.append(f'L{seg.c.x:.2f},{seg.c.y:.2f}L{seg.end_point.x:.2f},{seg.end_point.y:.2f}')
            else:
                p.append(f'C{seg.c1.x:.2f},{seg.c1.y:.2f} {seg.c2.x:.2f},{seg.c2.y:.2f} {seg.end_point.x:.2f},{seg.end_point.y:.2f}')
        p.append('Z'); d.append(''.join(p))
    return c, ''.join(d)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('src'); ap.add_argument('--out', required=True)
    ap.add_argument('--shirt', choices=['black', 'white'], default='black')
    ap.add_argument('--classes', type=int, default=4, help='классов яркости вместе с тканью (по умолчанию 4 = 3 краски)')
    ap.add_argument('--thresholds', help='пороги яркости через запятую, их K-1 штук')
    ap.add_argument('--crop'); ap.add_argument('--blur', type=float, default=0.6); ap.add_argument('--scale', type=float, default=1.0)
    ap.add_argument('--min-island', type=int, default=3); ap.add_argument('--trap-px', type=int, default=1)
    ap.add_argument('--height-mm', type=float, help='высота принта в мм (по умолчанию 0,2 мм на пиксель)')
    ap.add_argument('--names', help='имена красок через запятую в порядке печати')
    ap.add_argument('--preview-only', action='store_true')
    a_ = ap.parse_args()
    os.makedirs(a_.out, exist_ok=True)
    g, lab, t, K = posterize(a_)
    H, W = lab.shape
    np.save(os.path.join(a_.out, 'lab.npy'), lab)
    med = [int(np.median(g[lab == c])) if (lab == c).any() else 0 for c in range(K)]
    pal = np.array([[v, v, v] for v in med], np.uint8)
    Image.fromarray(pal[lab]).save(os.path.join(a_.out, 'preview_posterized.png'))
    shirt_cls = 0 if a_.shirt == 'black' else K - 1
    printed = [c for c in range(K) if c != shirt_cls]
    order = printed if a_.shirt == 'black' else printed[::-1]   # чёрная: тёмные → светлые, белый последним; белая: светлые → тёмные
    print('классов %d, доли %%:' % K, [round((lab == c).mean() * 100, 1) for c in range(K)], 'ткань = класс', shirt_cls)
    if a_.preview_only:
        print('превью:', os.path.join(a_.out, 'preview_posterized.png')); return

    import cairosvg
    mm_per_px = (a_.height_mm / H) if a_.height_mm else 0.2
    names = a_.names.split(',') if a_.names else [f'Тон {i + 1}' for i in range(len(order))]
    if len(names) != len(order):
        sys.exit(f'--names: нужно {len(order)} имён')
    inks = []
    for n, (c, name) in enumerate(zip(order, names), 1):
        v = med[c] if not (a_.shirt == 'black' and c == K - 1 and med[c] > 225) else 255
        inks.append((c, f'{n}_{name.strip().replace(" ", "_")}', '#%02X%02X%02X' % (v, v, v)))
    jobs = [(os.path.join(a_.out, 'lab.npy'), c, [d for d in printed if d < c], a_.trap_px) for c, _, _ in inks]
    with Pool(min(len(jobs), os.cpu_count() or 2)) as p:
        paths = dict(p.map(trace_one, jobs))

    pad = 40; VW, VH = W + 2 * pad, H + 2 * pad
    wmm, hmm = VW * mm_per_px, VH * mm_per_px

    def reg(col):
        s = ''
        for x, y in [(VW / 2, 14), (VW / 2, VH - 14), (14, VH / 2), (VW - 14, VH / 2)]:
            s += f'<circle cx="{x}" cy="{y}" r="7" fill="none" stroke="{col}" stroke-width="1.2"/><path d="M{x - 12},{y}H{x + 12}M{x},{y - 12}V{y + 12}" stroke="{col}" stroke-width="1.2"/>'
        return s
    head = (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
            f'width="{wmm:.1f}mm" height="{hmm:.1f}mm" viewBox="0 0 {VW} {VH}">')
    layer = lambda n, inner: f'<g id="{n}" inkscape:groupmode="layer" inkscape:label="{n}">{inner}</g>'
    shirt_col = '#111111' if a_.shirt == 'black' else '#F7F7F5'
    body = layer('0_Shirt_NO_PRINT', f'<rect width="{VW}" height="{VH}" fill="{shirt_col}"/>')
    for c, n, col in inks:
        body += layer(n, f'<g transform="translate({pad},{pad})"><path d="{paths[c]}" fill="{col}" fill-rule="evenodd"/></g>')
    body += layer('Registration', reg('#FF00FF'))
    comp = os.path.join(a_.out, 'composite.svg')
    open(comp, 'w', encoding='utf-8').write(head + body + '</svg>')
    cairosvg.svg2pdf(url=comp, write_to=os.path.join(a_.out, 'composite.pdf'))
    cairosvg.svg2png(url=comp, write_to=os.path.join(a_.out, 'preview.png'), output_width=1000)
    films = []
    for i, (c, n, col) in enumerate(inks, 1):
        s = (head + f'<rect width="{VW}" height="{VH}" fill="#fff"/><g transform="translate({pad},{pad})"><path d="{paths[c]}" fill="#000" fill-rule="evenodd"/></g>'
             + reg('#000') + f'<text x="{pad}" y="{VH - 8}" font-family="DejaVu Sans, Arial" font-size="14">{n} · печать {i}/{len(inks)} · позитив 1:1</text></svg>')
        f = os.path.join(a_.out, f'sep_{n}.pdf'); cairosvg.svg2pdf(bytestring=s.encode('utf-8'), write_to=f); films.append(f)
    with zipfile.ZipFile(os.path.join(a_.out, 'separations.zip'), 'w', zipfile.ZIP_DEFLATED) as z:
        for f in [comp, os.path.join(a_.out, 'composite.pdf'), os.path.join(a_.out, 'preview.png')] + films:
            z.write(f, os.path.basename(f))
    print(f'принт ≈ {W * mm_per_px:.0f}×{H * mm_per_px:.0f} мм; красок {len(inks)}; порядок печати:', ' → '.join(n for _, n, _ in inks))
    print('готово:', os.path.join(a_.out, 'separations.zip'))


if __name__ == '__main__':
    main()
