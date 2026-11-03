#!/usr/bin/env python3
"""Цветоделение плоского макета (вектор или растр без градиентов) по точным цветам — под шелкографию.

  python3 sep_flat.py src.pdf --out NAME_separations.pdf                # палитра найдётся сама
  python3 sep_flat.py src.pdf --inks inks.json --out NAME.pdf             # палитра задана вручную
  python3 sep_flat.py src.png --dpi 300 --out NAME.pdf                    # растр: dpi = его реальное разрешение

inks.json — список в ПОРЯДКЕ ПЕЧАТИ (от светлого к тёмному, контур последним):
  [{"hex": "#E0DBC8", "name": "Бежевый (фон)", "note": "≈ Pantone 7527 C"}, ...]

Выход: PDF (стр. 1 — превью с легендой; далее по плёнке на краску, позитив 1:1, с треппингом
и метками совмещения; белая подложка для тёмной футболки), <out>_plates.png — миниатюры плёнок,
в консоль — найденные цвета, покрытие и сколько экранов на белой/тёмной футболке.
Треппинг: каждый цвет заходит под более поздние (тёмные) на --trap-mm. Подложка — с чоком 0,1 мм.
"""
import argparse, json, os, subprocess, sys, tempfile
import numpy as np, cv2
from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib.colors import Color, black
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

FONT_DIRS = ['/usr/share/fonts/truetype/dejavu/', '/Library/Fonts/', '/System/Library/Fonts/Supplemental/']


def reg_fonts():
    for d in FONT_DIRS:
        r, b = os.path.join(d, 'DejaVuSans.ttf'), os.path.join(d, 'DejaVuSans-Bold.ttf')
        if os.path.exists(r) and os.path.exists(b):
            pdfmetrics.registerFont(TTFont('DV', r)); pdfmetrics.registerFont(TTFont('DVB', b))
            return 'DV', 'DVB'
    return 'Helvetica', 'Helvetica-Bold'   # без кириллицы — подписи станут квадратиками


def hexrgb(h):
    h = h.lstrip('#'); return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def lum(rgb):
    return (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255


def rasterize(src, dpi, page, tmp):
    if src.lower().endswith('.pdf'):
        base = os.path.join(tmp, 'pg')
        subprocess.run(['pdftoppm', '-r', str(dpi), '-png', '-aa', 'no', '-aaVector', 'no',
                        '-f', str(page), '-l', str(page), src, base], check=True)
        out = [f for f in os.listdir(tmp) if f.startswith('pg') and f.endswith('.png')]
        return os.path.join(tmp, sorted(out)[0])
    return src


def auto_palette(a, n_max, min_share):
    flat = a.reshape(-1, 3)
    cols, cnt = np.unique(flat, axis=0, return_counts=True)
    share = cnt / cnt.sum()
    keep = [(tuple(int(v) for v in c), s) for c, s in zip(cols, share) if s >= min_share and c.min() < 250]
    keep.sort(key=lambda x: -x[1]); keep = keep[:n_max]
    keep.sort(key=lambda x: -lum(x[0]))                      # светлые → тёмные
    print('найдено цветов (без белого): %d' % len(keep))
    for c, s in keep:
        print('  #%02X%02X%02X  %.2f%%' % (*c, s * 100))
    if any(s < 0.002 for _, s in keep):
        print('  ! есть цвета < 0,2% площади — возможно, это антиалиасинг/градиент, проверь глазами')
    return [{'hex': '#%02X%02X%02X' % c, 'name': 'Цвет %d' % (i + 1), 'note': 'подобрать по HEX'} for i, (c, _) in enumerate(keep)]


def masks_for(a, inks, tol):
    m = {}
    for ink in inks:
        r, g, b = hexrgb(ink['hex'])
        m[ink['hex']] = ((abs(a[..., 0] - r) <= tol) & (abs(a[..., 1] - g) <= tol) & (abs(a[..., 2] - b) <= tol)).astype(np.uint8)
    known = sum(m.values()) + (a.min(-1) >= 253).astype(np.uint8)
    unk = (known == 0).mean() * 100
    print('неопознанных пикселей: %.4f%%' % unk + ('  ← больше 0,5%: в макете есть градиенты/растр, этот скрипт не подходит, см. sep_tones.py' if unk > 0.5 else ''))
    return m


def trapped(m, inks, trap_px):
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * trap_px + 1, 2 * trap_px + 1))
    keys = [i['hex'] for i in inks]; out = {}
    for i, key in enumerate(keys):
        later = np.zeros_like(m[key])
        for k2 in keys[i + 1:]:
            later |= m[k2]
        out[key] = (m[key] | (cv2.dilate(m[key], k) & later)) if i < len(keys) - 1 else m[key]
    return out


def contours(mask):
    cs, _ = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    return [cv2.approxPolyDP(c, 0.6, True) for c in cs if len(c) >= 3]


def draw(c, cs, H, px, ox, oy, color):
    p = c.beginPath()
    for cnt in cs:
        pts = cnt[:, 0, :]
        if len(pts) < 3:
            continue
        p.moveTo(ox + pts[0][0] * px, oy + (H - pts[0][1]) * px)
        for x, y in pts[1:]:
            p.lineTo(ox + x * px, oy + (H - y) * px)
        p.close()
    c.setFillColor(color); c.drawPath(p, stroke=0, fill=1, fillMode=0)   # 0 = even-odd: дырки остаются дырками


def regmarks(c, PW, PH, M):
    c.setStrokeColor(black); c.setLineWidth(0.4); r = 3 * mm
    for x, y in [(M / 2, M / 2), (PW - M / 2, M / 2), (M / 2, PH - M), (PW - M / 2, PH - M), (PW / 2, M / 2), (PW / 2, PH - M)]:
        c.circle(x, y, r); c.line(x - r * 1.6, y, x + r * 1.6, y); c.line(x, y - r * 1.6, x, y + r * 1.6)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('src'); ap.add_argument('--out', required=True)
    ap.add_argument('--inks', help='JSON с палитрой в порядке печати; без него — автопоиск')
    ap.add_argument('--max-colors', type=int, default=8); ap.add_argument('--min-share', type=float, default=0.001)
    ap.add_argument('--dpi', type=int, default=400); ap.add_argument('--page', type=int, default=1)
    ap.add_argument('--trap-mm', type=float, default=0.25); ap.add_argument('--tol', type=int, default=2)
    ap.add_argument('--underbase-exclude', nargs='*', help='HEX цветов, под которыми подложка не нужна (по умолчанию самый тёмный, если он темнее 20%)')
    ap.add_argument('--title', default='Цветоделение')
    a_ = ap.parse_args()
    F, FB = reg_fonts()
    with tempfile.TemporaryDirectory() as tmp:
        png = rasterize(a_.src, a_.dpi, a_.page, tmp)
        a = np.asarray(Image.open(png).convert('RGB')).astype(int)
    H, W = a.shape[:2]
    inks = json.load(open(a_.inks, encoding='utf-8')) if a_.inks else auto_palette(a, a_.max_colors, a_.min_share)
    if not inks:
        sys.exit('Цветов не найдено')
    m = masks_for(a, inks, a_.tol)
    trap_px = max(1, int(round(a_.trap_mm / 25.4 * a_.dpi)))
    t = trapped(m, inks, trap_px)
    darkest = inks[-1]['hex']
    excl = set(h.upper() for h in a_.underbase_exclude) if a_.underbase_exclude is not None else ({darkest.upper()} if lum(hexrgb(darkest)) < 0.2 else set())
    under = np.zeros((H, W), np.uint8)
    for ink in inks:
        if ink['hex'].upper() not in excl:
            under |= m[ink['hex']]
    under = cv2.erode(under, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))

    px = 25.4 / a_.dpi * mm; M = 18 * mm
    PW, PH = W * px + 2 * M, H * px + 2 * M + 10 * mm
    c = canvas.Canvas(a_.out, pagesize=(PW, PH), pageCompression=1); c.setTitle(a_.title)
    for ink in inks:
        draw(c, contours(m[ink['hex']]), H, px, M, M, Color(*[v / 255 for v in hexrgb(ink['hex'])]))
    c.setFillColor(black); c.setFont(FB, 11); c.drawString(M, PH - 10 * mm, f'{a_.title} — {len(inks)} цв.')
    c.setFont(F, 8); c.drawString(M, PH - 14 * mm, f'Порядок печати слева направо. Треппинг {a_.trap_mm} мм под более тёмные цвета. Размер {W * 25.4 / a_.dpi:.0f}×{H * 25.4 / a_.dpi:.0f} мм.')
    cols = min(len(inks), 5)
    for i, ink in enumerate(inks):
        x = M + (i % cols) * (PW - 2 * M) / cols; y = 6 * mm - (i // cols) * 7 * mm + (7 * mm if len(inks) > 5 else 0)
        c.setFillColor(Color(*[v / 255 for v in hexrgb(ink['hex'])])); c.rect(x, y, 5 * mm, 5 * mm, stroke=1, fill=1)
        c.setFillColor(black); c.setFont(F, 6.5); c.drawString(x + 6.5 * mm, y + 3 * mm, f"{i + 1}. {ink['name']}")
        c.drawString(x + 6.5 * mm, y + 0.3 * mm, f"{ink['hex']} · {ink.get('note', '')}")
    c.showPage()
    for i, ink in enumerate(inks, 1):
        draw(c, contours(t[ink['hex']]), H, px, M, M, black); regmarks(c, PW, PH, M)
        c.setFillColor(black); c.setFont(FB, 11); c.drawString(M, PH - 10 * mm, f"{i}. {ink['name']} — {ink['hex']}")
        c.setFont(F, 8); c.drawString(M, PH - 14 * mm, f"{ink.get('note', '')} · позитив 1:1 · треппинг {a_.trap_mm} мм")
        c.showPage()
    if under.any():
        draw(c, contours(under), H, px, M, M, black); regmarks(c, PW, PH, M)
        c.setFont(FB, 11); c.drawString(M, PH - 10 * mm, 'Белая подложка — только для тёмной футболки, печатается первой')
        c.setFont(F, 8); c.drawString(M, PH - 14 * mm, 'Чок 0,1 мм. Цвета ' + (', '.join(sorted(excl)) or '—') + ' на тёмной ткани не печатаются (их заменяет ткань)')
        c.showPage()
    c.save()

    thumbs = [Image.fromarray(((1 - t[i['hex']]) * 255).astype(np.uint8)) for i in inks] + [Image.fromarray(((1 - under) * 255).astype(np.uint8))]
    sc = max(1, W // 300); thumbs = [x.resize((max(1, W // sc), max(1, H // sc))) for x in thumbs]
    tw, th = thumbs[0].size; n = len(thumbs); colsn = min(n, 4); rows = (n + colsn - 1) // colsn
    sheet = Image.new('L', (colsn * (tw + 10) + 10, rows * (th + 10) + 10), 200)
    for i, im in enumerate(thumbs):
        sheet.paste(im, (10 + (i % colsn) * (tw + 10), 10 + (i // colsn) * (th + 10)))
    sheet.save(os.path.splitext(a_.out)[0] + '_plates.png')

    print('размер, мм: %.0f × %.0f' % (W * 25.4 / a_.dpi, H * 25.4 / a_.dpi))
    for ink in inks:
        print('  %s %-28s покрытие %.1f%%' % (ink['hex'], ink['name'], m[ink['hex']].mean() * 100))
    near_white = [i['hex'] for i in inks if lum(hexrgb(i['hex'])) > 0.9]
    print('экранов: белая футболка — %d%s; тёмная — %d (цвета) + подложка' % (
        len(inks), (' (можно %d: %s почти как ткань)' % (len(inks) - len(near_white), ', '.join(near_white))) if near_white else '',
        len([i for i in inks if i['hex'].upper() not in excl])))
    print('готово:', a_.out)


if __name__ == '__main__':
    main()
