#!/usr/bin/env python3
"""Текст в кривые для печатных макетов (reportlab + fontTools): надпись заданной ширины по краске,
каждая буква своим цветом, PDF 1:1 без шрифтов внутри. 
Как модуль:
    from text_curves import load_font, draw_text
    f = load_font('montserrat-cyrillic-800-normal.woff')
    cap_h = draw_text(c, f, 'НАДПИСЬ', x, y, ink_width=10*cm, colors=[WHITE]*5 + [RED]*3)

CLI (быстрый макет одной надписи):
    python3 text_curves.py --font F.woff --text "НАДПИСЬ" --width-cm 10 --out надпись_1к1.pdf [--color "#E4002B"]

Шрифт: с кириллицей. Montserrat и др. берём из npm (реестр доступен из песочницы):
    npm pack @fontsource/montserrat && tar xzf fontsource-montserrat-*.tgz
    → package/files/montserrat-cyrillic-800-normal.woff (800 = ExtraBold), латиница — montserrat-latin-800-normal.woff
woff читается fontTools напрямую; для woff2 нужен pip-пакет brotli.
Ширина считается по ЧЕРНИЛАМ (bounds букв, без боковых полей), высота букв = capHeight × масштаб.
"""
import argparse
from fontTools.ttLib import TTFont
from fontTools.pens.basePen import BasePen
from fontTools.pens.boundsPen import BoundsPen


class _Pen(BasePen):
    def __init__(s, gs, path, x, y, k):
        super().__init__(gs); s.p, s.x, s.y, s.k = path, x, y, k
    def T(s, pt): return (s.x + pt[0] * s.k, s.y + pt[1] * s.k)
    def _moveTo(s, p): s.p.moveTo(*s.T(p))
    def _lineTo(s, p): s.p.lineTo(*s.T(p))
    def _curveToOne(s, a, b, c): s.p.curveTo(*s.T(a), *s.T(b), *s.T(c))
    def _closePath(s): s.p.close()


def load_font(path):
    f = TTFont(path); gs, cmap = f.getGlyphSet(), f.getBestCmap()
    cap = getattr(f['OS/2'], 'sCapHeight', 0) if 'OS/2' in f else 0
    if not cap:                                   # старые шрифты: меряем высоту буквы H / Н
        for ch in ('H', 'Н'):
            if ord(ch) in cmap:
                bp = BoundsPen(gs); gs[cmap[ord(ch)]].draw(bp); cap = bp.bounds[3]; break
    return {'gs': gs, 'cmap': cmap, 'cap': cap or 700}


def ink_bounds(font, text):
    gs, cmap = font['gs'], font['cmap']; xs, adv, names = [], 0, []
    for ch in text:
        if ord(ch) not in cmap:
            raise SystemExit(f'В шрифте нет символа {ch!r} — возьми файл с нужным набором (cyrillic/latin)')
        n = cmap[ord(ch)]; names.append(n)
        bp = BoundsPen(gs); gs[n].draw(bp)
        if bp.bounds:
            xs += [adv + bp.bounds[0], adv + bp.bounds[2]]
        adv += gs[n].width
    return names, min(xs), max(xs)


def draw_text(c, font, text, x, y, ink_width=None, cap_height=None, colors=None):
    """Рисует text кривыми с левого края чернил x и базовой линией y. Задай ink_width ИЛИ cap_height.
    Возвращает (ширина по чернилам, высота заглавных) в единицах reportlab (pt)."""
    gs = font['gs']; names, x0, x1 = ink_bounds(font, text)
    k = ink_width / (x1 - x0) if ink_width else cap_height / font['cap']
    adv = 0; colors = colors or [None] * len(names)
    for n, col in zip(names, colors):
        path = c.beginPath(); gs[n].draw(_Pen(gs, path, x - x0 * k + adv * k, y, k))
        if col is not None:
            c.setFillColor(col)
        c.drawPath(path, stroke=0, fill=1, fillMode=0); adv += gs[n].width
    return (x1 - x0) * k, font['cap'] * k


if __name__ == '__main__':
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import cm
    from reportlab.lib.colors import HexColor
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--font', required=True); ap.add_argument('--text', required=True)
    ap.add_argument('--width-cm', type=float, required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--color', default='#000000'); ap.add_argument('--margin-cm', type=float, default=1.0)
    a = ap.parse_args()
    f = load_font(a.font); M = a.margin_cm * cm
    names, x0, x1 = ink_bounds(f, a.text); k = a.width_cm * cm / (x1 - x0); capH = f['cap'] * k
    c = canvas.Canvas(a.out, pagesize=(a.width_cm * cm + 2 * M, capH + 2 * M))
    w, h = draw_text(c, f, a.text, M, M, ink_width=a.width_cm * cm, colors=[HexColor(a.color)] * len(a.text))
    c.save()
    print(f'{a.out}: ширина {w / cm:.2f} см, высота заглавных {h / cm:.2f} см, текст в кривых')
