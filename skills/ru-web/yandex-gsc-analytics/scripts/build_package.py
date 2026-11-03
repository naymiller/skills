#!/usr/bin/env python3
"""Собирает пакет выгрузки (папка package/) из файлов, которые дал extract_blocks.py.

  python3 scripts/build_package.py <raw_dir> <out_dir> [--date 2026-11-03]
  python3 scripts/build_package.py --help

Имена входных файлов в raw_dir:
  <сайт>__<отчёт>__<вариант>.txt        текст отчёта (Метрика, Вебмастер, Search Console)
  <сайт>__visit_history__json.json      массив визитов (Вебвизор-API), по одному объекту на визит
  <сайт>__visitors__json.json           массив посетителей за год
  <сайт>__goals_config__json.json       конфиг целей счётчика
Сайт — любое имя без двойного подчёркивания: example, example_card и т.п.
Список сайтов не нужен: они берутся из имён файлов.

Выход: out_dir/<сайт>/metrika_visits_all.csv, metrika_visits_with_goals.csv и т.д.
Пример: python3 scripts/build_package.py raw package --date 2026-11-03
"""
import argparse, csv, glob, json, os, re, shutil

ap = argparse.ArgumentParser(description='Собрать пакет выгрузки Метрики, Вебмастера и Search Console.')
ap.add_argument('raw_dir', help='папка с блоками после extract_blocks.py')
ap.add_argument('out_dir', help='куда писать пакет (очищается)')
ap.add_argument('--date', default='', help='дата выгрузки для шапок файлов, например 2026-11-03')
args = ap.parse_args()

RAW, OUT = args.raw_dir, args.out_dir
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT)

# Необязательно: красивые названия сайтов для шапок файлов. Нет в словаре — будет имя файла.
SITE_TITLES = {
    'example': 'example.ru',
    'example_card': 'example.ru — карточка Яндекс Бизнес',
}

# Названия отчётов для шапок. Дополняй под свои выгрузки.
TITLES = {
    'sources': 'Метрика · Источники трафика (детально)',
    'search_engines': 'Метрика · Поисковые системы › поисковые фразы',
    'popular': 'Метрика · Популярные страницы (просмотры)',
    'titles': 'Метрика · Заголовки страниц',
    'entrance': 'Метрика · Страницы входа',
    'exit': 'Метрика · Страницы выхода',
    'tags_utm': 'Метрика · UTM-метки',
    'geo': 'Метрика · География',
    'hourly': 'Метрика · По часам суток',
    'tech_devices': 'Метрика · Устройства',
    'deepness_depth': 'Метрика · Глубина просмотра',
    'traffic_daily': 'Метрика · Посещаемость по дням',
    'traffic_monthly': 'Метрика · Посещаемость по месяцам',
    'goal_params': 'Метрика · Параметры целей',
    'wm_dashboard': 'Вебмастер · Сводка',
    'wm_queries': 'Вебмастер · Все запросы (показы/клики — СРЕДНЕЕ В ДЕНЬ, позиция, CTR)',
    'wm_queries_by_url': 'Вебмастер · Запросы по URL (среднее в день)',
    'wm_statistics': 'Вебмастер · Статистика запросов за период',
    'wm_popular_queries_full': 'Вебмастер · Популярные запросы за период (показы, клики, CTR, прогноз)',
    'wm_popular_queries_weekly': 'Вебмастер · Популярные запросы (первые 25, дубль полного списка)',
    'wm_searchable': 'Вебмастер · Страницы в поиске — последние изменения',
    'wm_indexing': 'Вебмастер · Статистика обхода',
    'wm_links_incoming': 'Вебмастер · Внешние ссылки',
    'wm_diagnostics': 'Вебмастер · Диагностика',
    'gsc_performance': 'Google Search Console · Эффективность, 3 месяца',
    'gsc_index': 'Google Search Console · Индексирование страниц',
}

# Названия целей для суффиксов файлов (вариант выгрузки = goal_<имя>). Дополняй под свои цели.
GOALNAMES = {
    'all': 'без фильтра по цели',
    'goal_messenger': 'цель «Переход в мессенджер»',
    'goal_form_sent': 'цель «Отправил контактные данные» (= заявка через форму)',
    'goal_email': 'цель «Клик по email»',
    'goal_call_click': 'цель «Клик на позвонить»',
    'goal_route': 'цель «Построить маршрут»',
    'goal_site_click': 'цель «Переход на сайт»',
    'txt': '',
}

HCOLS = ['dateTime', 'uid', 'visitSerialNumber', 'trafficSource', 'searchEngine', 'refererDomainShort',
         'UTMSource', 'UTMMedium', 'UTMCampaign', 'UTMTerm', 'UTMContent', 'socialNetwork', 'WVAdvEngine',
         'deviceCategory', 'regionAggregated', 'pageViews', 'visitDuration', 'bounce',
         'webVisorGoals', 'parsedParamsRaw', 'gridEvents', 'visitID']


def rd(name):
    p = os.path.join(RAW, name)
    return open(p, encoding='utf-8').read() if os.path.exists(p) else None


def sites_from_files(pattern_suffix):
    names = set()
    for fn in glob.glob(os.path.join(RAW, f'*__{pattern_suffix}*')):
        names.add(os.path.basename(fn).split('__')[0])
    return sorted(names)


def sanitize(s):
    # Частично замаскированные телефон и e-mail, введённые в формы: оставляем факт, без значения.
    s = re.sub(r'MASKED_PHONE_EVENT:\{[^}]*\}', 'CONTACT_ENTERED:phone', s)
    s = re.sub(r'MASKED_EMAIl_EVENT:\{[^}]*\}', 'CONTACT_ENTERED:email', s)
    return s


def is_goal_visit(r):
    return bool(r.get('webVisorGoals', ''))


# ---------- история визитов: по строке на визит ----------
for site in sites_from_files('visit_history'):
    rows = []
    for fn in sorted(glob.glob(os.path.join(RAW, f'{site}__visit_history*__json.json'))):
        rows += json.loads(open(fn, encoding='utf-8').read())
    rows.sort(key=lambda r: r.get('dateTime', ''))
    d = os.path.join(OUT, site)
    os.makedirs(d, exist_ok=True)
    with open(f'{d}/metrika_visits_all.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(HCOLS)
        for r in rows:
            w.writerow([sanitize(str(r.get(c, ''))) for c in HCOLS])
    with open(f'{d}/metrika_visits_with_goals.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(HCOLS)
        for r in rows:
            if is_goal_visit(r):
                w.writerow([sanitize(str(r.get(c, ''))) for c in HCOLS])

# ---------- посетители за год ----------
for site in sites_from_files('visitors'):
    t = rd(f'{site}__visitors__json.json')
    if not t:
        continue
    rows = json.loads(t)
    os.makedirs(os.path.join(OUT, site), exist_ok=True)
    with open(f'{OUT}/{site}/metrika_visitors.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        cols = ['uid', 'firstVisit', 'lastVisit', 'visits', 'totalDurationSec', 'device', 'os', 'country', 'goals']
        w.writerow(cols)
        for r in rows:
            w.writerow([r.get(c, '') for c in cols])

# ---------- конфиг целей (нужны id для &goal=) ----------
for site in sites_from_files('goals_config'):
    t = rd(f'{site}__goals_config__json.json')
    if t:
        os.makedirs(f'{OUT}/{site}', exist_ok=True)
        with open(f'{OUT}/{site}/metrika_goals_config.json', 'w', encoding='utf-8') as f:
            json.dump(json.loads(t), f, ensure_ascii=False, indent=1)

# ---------- текстовые отчёты (Метрика, Вебмастер, Search Console) ----------
for fn in sorted(glob.glob(os.path.join(RAW, '*.txt'))):
    base = os.path.basename(fn)[:-4]
    parts = base.split('__')
    if len(parts) != 3:
        print('пропущен (не сайт__отчёт__вариант):', base)
        continue
    site, report, variant = parts
    body = open(fn, encoding='utf-8').read()
    # Служебный шум интерфейса Вебмастера: промо-блок и подвал, названия иконок.
    body = re.sub(r'Поделитесь мнением.*?Информация о сайте', 'Информация о сайте', body, flags=re.S)
    body = re.sub(r'\nБлог\nПользовательское соглашение.*$', '', body, flags=re.S)
    body = re.sub(r'\n(search|keyboard_arrow_up|file_copy|account_tree|visibility_off|speed|layers|'
                  r'keyboard_arrow_down|link_2|trophy|settings|feedback|info|home|lightbulb_2|trending_up|'
                  r'help_outline|manage_accounts|notifications|close|add|filter_list_spark|arrow_drop_down|'
                  r'info_filled)\n', '\n', body)
    title = TITLES.get(report, report)
    if GOALNAMES.get(variant):
        title += f' — {GOALNAMES[variant]}'
    os.makedirs(f'{OUT}/{site}', exist_ok=True)
    if report.startswith('wm_'):
        prefix, name = 'webmaster', report[3:]
    elif report.startswith('gsc_'):
        prefix, name = 'google_sc', report[4:]
    else:
        prefix, name = 'metrika', report
    suffix = '' if variant in ('all', 'txt') else f'_{variant}'
    out = f'{OUT}/{site}/{prefix}_{name}{suffix}.txt'
    with open(out, 'w', encoding='utf-8') as f:
        f.write(f'# {title}\n# Сайт: {SITE_TITLES.get(site, site)}\n# Выгружено: {args.date}\n\n')
        f.write(sanitize(body).strip() + '\n')

print('готово. Файлы пакета:')
for p in sorted(glob.glob(f'{OUT}/**/*', recursive=True)):
    if os.path.isfile(p):
        print(os.path.relpath(p, OUT), os.path.getsize(p))
