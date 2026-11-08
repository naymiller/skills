# Скиллы для агентов на русском

[![GitHub stars](https://img.shields.io/github/stars/naymiller/skills?style=flat)](https://github.com/naymiller/skills/stargazers)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-d97757)](#установка)
[![Agent Skills](https://img.shields.io/badge/Agent%20Skills-SKILL.md-555)](https://github.com/anthropics/skills)
![Skills: 8](https://img.shields.io/badge/skills-8-brightgreen)

> **English summary.** Agent skills (Claude Code / Cowork / claude.ai, any `SKILL.md`-aware agent) built from a real garment and print production business. **Merch & print:** merch costing and quotes (sewing + screen printing by colors and run size + DTF + embroidery), screen-print color separation to 1:1 films with trapping and underbase, apparel reference maps before AI product photos. **Russian web:** Yandex demand research before building a site (Wordstat, suggestions, SERP, Alice), Yandex Metrica / Webmaster / Google Search Console analysis via a logged-in browser without API tokens, B2B outreach via Yandex 360 from a Mac (Keychain, warm-up, launchd), a budget-capped Higgsfield API MCP server (USD limits, estimate before submit, idempotent retries). **Meta:** how to write skills (Russian adaptation of Matt Pocock's writing-for-agents). Skills are written in Russian. Install: `claude plugin marketplace add naymiller/skills` → `claude plugin install naymiller-skills@naymiller`. MIT.

Скиллы для Claude (Claude Code, Cowork, claude.ai) и других агентов, которые читают `SKILL.md`. Собраны из реальной работы швейного и печатного производства и его сайтов: каждый скилл — процедура, которую уже прогоняли на живых заказах, с порогами, проверками и граблями.

Пишутся по правилам [skill-writing-ru](skills/meta/skill-writing-ru/SKILL.md): короткое описание, ветки, «Готово, когда» у каждого шага, жёсткие ограничения на деньги, отправку и личные данные.

## Мерч и печать

| Скилл | Что делает | Чем отличается от того, что уже есть на GitHub |
|---|---|---|
| [merch-quote](skills/merch/merch-quote/SKILL.md) | Себестоимость партии мерча и КП клиенту | Сводит в одну партию пошив, шелкографию по цветам и тиражам, DTF и вышивку; сверяет итог по размерам и принтам с общим количеством изделий. Цены — ваши, из шаблона |
| [screenprint-separation](skills/merch/screenprint-separation/SKILL.md) | Подготовка принта к шелкографии | Вся цепочка от макета до плёнок: цветоделение вектора и тонового арта, плёнки 1:1 с треппингом и подложкой, подсчёт экранов, текст в кривые, ТЗ с размерами относительно изделия. Скрипты на Python |
| [apparel-references](skills/merch/apparel-references/SKILL.md) | Карта референсов одежды перед ИИ-фото | Решает до генерации, что сохранить и что можно менять (крой, ткань, HEX, швы, положение и размер принта), и как поймать искажение логотипа и мелкого принта |

## Яндекс, сайты, продажи

| Скилл | Что делает | Чем отличается |
|---|---|---|
| [yandex-demand-research](skills/ru-web/yandex-demand-research/SKILL.md) | Исследование спроса до сборки сайта | Вордстат, подсказки, выдача, ответы Алисы и ChatGPT, Карты → карта страниц с title/description, план статей, факты для llms.txt и robots. Через браузер, без API-ключей |
| [yandex-gsc-analytics](skills/ru-web/yandex-gsc-analytics/SKILL.md) | Разбор Метрики, Вебмастера, Search Console и Яндекс Бизнеса | Агент сам выгружает кабинеты через залогиненный браузер, находит, откуда заявки и где показы без кликов, пишет и выполняет план «больше клиентов», ставит контрольную точку |
| [yandex360-outreach](skills/ru-web/yandex360-outreach/SKILL.md) | Холодный B2B-аутрич с Мака | Яндекс 360 по SMTP/IMAP, пароль в Связке ключей macOS, прогрев 10→15→20→30 (потолок 60), автопауза при bounce > 8 %, стоп при ответе, launchd, CSV-CRM |
| [higgsfield-budget-mcp](skills/ru-web/higgsfield-budget-mcp/SKILL.md) | Фото и видео через Higgsfield API | Локальный MCP-сервер: пакеты с лимитом в USD, оценка до отправки, защита от двойного списания, ключ в Связке ключей, файлы только в рабочей папке. Тесты на фейковом API |

## Про скиллы

| Скилл | Что делает |
|---|---|
| [skill-writing-ru](skills/meta/skill-writing-ru/SKILL.md) | Как писать скиллы и брифы агентам: указатели, ветки, критерии готовности, позитивные формулировки, чистка. Адаптация [writing-for-agents](https://github.com/mattpocock/skills) Мэтта Покока |

## Как пользоваться

После установки агент сам подхватывает нужный скилл по смыслу запроса. Примеры:

- «Посчитай себестоимость: 300 худи, принт на груди в 3 цвета, вышивка на рукаве» → `merch-quote`
- «Разложи этот макет на цвета под шелкографию и сделай плёнки 1:1» → `screenprint-separation`
- «Собери спрос по „футболки с логотипом“ перед сайтом» → `yandex-demand-research`
- «Почему с сайта нет заявок? Разбери Метрику и Вебмастер» → `yandex-gsc-analytics`
- «Запусти холодную рассылку по агентствам из CSV» → `yandex360-outreach`
- «Сгенерируй 10 предметных фото, бюджет не больше $5» → `higgsfield-budget-mcp`

## Установка

**Claude Code — весь набор как плагин:**
```bash
claude plugin marketplace add naymiller/skills
claude plugin install naymiller-skills@naymiller
```

**Один скилл в claude.ai / приложении Claude:** скачайте папку скилла, упакуйте её в zip и загрузите в настройках скиллов.

**Вручную:** скопируйте папку скилла в `~/.claude/skills/`.

MCP-сервер Higgsfield подключается отдельно — инструкция в [его SKILL.md](skills/ru-web/higgsfield-budget-mcp/SKILL.md).

## Ответственность

Скиллы помогают агенту, но решения и ответственность остаются за вами: цены в КП, отправка писем (в РФ — с учётом закона «О рекламе» и 152-ФЗ), публикация на сайте, расходы на генерации. Скиллы, которые тратят деньги или отправляют что-то наружу, требуют явного подтверждения перед каждым таким действием.

## Лицензия

MIT — см. [LICENSE](LICENSE). `skill-writing-ru` основан на материалах mattpocock/skills (MIT), текст их лицензии лежит в папке скилла.

Если скилл сэкономил вам время — поставьте ⭐, так набор быстрее найдут другие.

<details>
<summary><b>English: skill list</b></summary>

Skills are written in Russian; agents follow them fine whatever language you prompt in.

| Skill | What it does |
|---|---|
| [merch-quote](skills/merch/merch-quote/SKILL.md) | Merch batch costing and client quote: sewing, screen printing by colors and run size, DTF, embroidery; checks size/print totals against the order quantity. Your prices via a CSV template |
| [screenprint-separation](skills/merch/screenprint-separation/SKILL.md) | Artwork → screen-print films: vector and halftone color separation, 1:1 films with trapping and underbase, screen count, text to outlines, print spec relative to the garment. Python scripts |
| [apparel-references](skills/merch/apparel-references/SKILL.md) | Reference map before AI product photos: what must stay fixed (cut, fabric, HEX, seams, print size and position) and how to catch distorted logos |
| [yandex-demand-research](skills/ru-web/yandex-demand-research/SKILL.md) | Demand research before building a site: Wordstat, suggestions, SERP, Alice/ChatGPT answers, Maps → page map with titles, article plan, llms.txt and robots facts. Browser-based, no API keys |
| [yandex-gsc-analytics](skills/ru-web/yandex-gsc-analytics/SKILL.md) | Yandex Metrica, Webmaster, Google Search Console and Yandex Business analysis via a logged-in browser → "more leads" action plan with a checkpoint |
| [yandex360-outreach](skills/ru-web/yandex360-outreach/SKILL.md) | Cold B2B email from a Mac via Yandex 360 SMTP/IMAP: macOS Keychain, warm-up 10→15→20→30, auto-pause on bounce > 8 %, stop on reply, launchd, CSV CRM |
| [higgsfield-budget-mcp](skills/ru-web/higgsfield-budget-mcp/SKILL.md) | Local MCP server for the Higgsfield API: USD-capped batches, estimate before submit, double-charge protection, key in Keychain. Tested against a fake API |
| [skill-writing-ru](skills/meta/skill-writing-ru/SKILL.md) | How to write skills and agent briefs (Russian adaptation of Matt Pocock's writing-for-agents) |

</details>
