# Auto SEOsprint

AI-ассистент для **полного цикла** работы с [SEOsprint](https://seosprint.net):

**разведка → выполнение → взять задание → сдать отчёт → аналитика**

На базе **Gemini 2.5 Flash (free)** через [OpenRouter](https://openrouter.ai).

---

## Установка

```bash
cd auto_SEOsprint
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

**Браузер Playwright** (если CDN доступен):

```bash
playwright install chromium
# или: .\scripts\install_playwright.ps1
```

**Если `playwright install` не скачивает Chromium** — установите Google Chrome и в `.env`:

```env
BROWSER_CHANNEL=chrome
```

Скопируйте настройки из `.env.example` в `.env` и заполните ключи.

---

## Быстрый старт

```bash
python main.py login              # вход в SEOsprint
python main.py login-sessions     # вход в VK, YouTube, Telegram и др.
python main.py pipeline earn-task/1 --auto-only   # полный цикл по всем страницам
python main.py review             # аналитика отказов и доработок
```

---

## Команды CLI (`main.py`)

| Команда | Описание |
|---------|----------|
| `login` | Вход в SEOsprint через браузер, сохранение cookies |
| `login-sessions` | Вход в соцсети (YouTube, VK, Telegram и др.) в том же профиле |
| `fetch [source]` | Скачать HTML списка заданий с SEOsprint |
| `parse file.html` | Парсинг HTML без AI |
| `analyze file.html` | AI-анализ заданий (категория, автоматизация, риски) |
| `pipeline [source]` | **Полный цикл**: разведка → выполнить → взять → сдать |
| `review` | Аналитика отклонённых (`earn-task-fail`) и доработок (`earn-task-fix`) |
| `inwork` | Список заданий «взяты в работу» + нужные сессии |
| `take ID` | Взять одно задание |
| `submit ID -r report.txt` | Отправить отчёт по взятым заданию |
| `run file.html` | Локальное выполнение без SEOsprint |
| `report ID --from file.html` | Сгенерировать черновик отчёта |
| `battle-test` | Проверка API, парсера, сессий |

Флаги pipeline: `--auto-only`, `--no-submit`, `--dry-run`, `--id 3058462`

---

## Структура проекта

```
auto_SEOsprint/
├── main.py                 # Точка входа CLI (Typer)
├── requirements.txt        # Python-зависимости
├── .env                    # Секреты и настройки (API-ключ, профиль исполнителя)
├── README.md               # Этот файл
│
├── scripts/
│   └── install_playwright.ps1   # Установка Chromium с увеличенным таймаутом / зеркалом
│
├── samples/
│   └── tasks_sample.html        # Пример HTML списка заданий для тестов парсера
│
├── src/                    # Исходный код
│   ├── config.py           # Загрузка настроек из .env (Settings)
│   ├── models.py           # Pydantic-модели: SeoTask, TaskAnalysis, ExecutionResult
│   ├── openrouter.py       # Клиент OpenRouter API (chat, chat_json)
│   ├── parser.py           # Парсинг HTML SEOsprint (списки, ТЗ, fail/fix, пагинация)
│   ├── analyzer.py         # AI-анализ заданий (категория, уровень автоматизации)
│   ├── runner.py           # TaskRunner: загрузка, анализ, batch-выполнение
│   ├── pipeline.py         # SeosprintPipeline: полный цикл + аналитика + fix-очередь
│   ├── recon.py            # ReconRunner: разведка ТЗ до взятия задания
│   ├── task_context.py     # TaskTzContext: контекст ТЗ, verify_action, action_log
│   ├── task_source.py      # load_tasks_from_source (файл / URL SEOsprint)
│   ├── report_builder.py   # Сбор полного payload отчёта + генерация текста через AI
│   ├── battle_test.py      # Интеграционные проверки
│   │
│   ├── analytics/          # Аналитический модуль
│   │   ├── journal.py      # TaskJournal: журнал выполнения каждого задания
│   │   └── reviewer.py     # AnalyticsReviewer: анализ fail/fix + рекомендации AI
│   │
│   ├── executors/          # Исполнители заданий по типу
│   │   ├── registry.py     # Маршрутизация task → executor
│   │   ├── base.py         # BaseExecutor: скриншоты, generate_ai_report
│   │   ├── visit_urls.py   # Посещение URL + скриншоты
│   │   ├── social.py       # Задания через сессии соцсетей
│   │   ├── browser_task.py # Общие браузерные задания
│   │   ├── ai_browser.py   # AI-driven браузерные сценарии
│   │   └── manual.py       # Ручные задания (инструкции)
│   │
│   └── seosprint/          # Работа с сайтом SEOsprint
│       ├── browser.py      # Playwright persistent context, keeper-tab, cookies
│       ├── browser_log.py  # Подробное логирование кликов, goto, redirect, dialog
│       ├── client.py       # API браузера: fetch, take, submit, pagination, fail/fix
│       ├── platforms.py    # Проверка сессий VK/YouTube/Telegram и др.
│       ├── selectors.py    # CSS-селекторы форм отчёта, кнопок
│       └── sources.py      # resolve_task_source (earn, URL, файл)
│
└── data/                   # Рабочие данные (коммитятся в репозиторий)
    ├── cookies.json        # Cookies SEOsprint (сессия входа)
    ├── browser_profile/    # Persistent-профиль Chromium/Chrome (SEOsprint + соцсети)
    │
    ├── logs/
    │   └── browser_YYYYMMDD.log   # Подробные логи браузера: GOTO, CLICK, SUBMIT, …
    │
    ├── reports/
    │   └── {task_id}.txt          # Текст отчёта, отправленного/сгенерированного
    │
    ├── screenshots/
    │   └── {task_id}/             # Скриншоты выполнения (visit_1.png, …)
    │
    ├── analytics/
    │   ├── timeline.jsonl         # Общая лента всех выполнений
    │   ├── executions/
    │   │   └── {task_id}.json     # Полный журнал: действия, payload, отчёт, скрины
    │   └── reviews/
    │       └── {task_id}_fail.json / _fix.json   # AI-анализ отказов и доработок
    │
    └── debug/
        └── {task_id}_*.html       # HTML-дамп страницы при ошибках (нет кнопки «Начать» и т.п.)
```

---

## Назначение модулей

### Ядро

- **`config.py`** — читает `.env`: ключ OpenRouter, модель, Telegram/email/ID исполнителя, пути к data, headless, `BROWSER_CHANNEL`.
- **`pipeline.py`** — оркестратор: при старте проверяет fail/fix, обходит страницы earn-task, для каждого задания: разведка → сессии → выполнение → взять → отчёт → журнал.
- **`recon.py`** — до нажатия «Начать» читает полное ТЗ и строит план; может отклонить задание до выполнения.
- **`task_context.py`** — держит ТЗ в памяти на время задания; `verify_action()` сверяет шаги с ТЗ через AI; `action_log` попадает в отчёт и analytics.

### SEOsprint (браузер)

- **`browser.py`** — один persistent-профиль на все сайты; keeper-вкладка `about:blank` предотвращает закрытие контекста между заданиями.
- **`client.py`** — `fetch_tasks_paginated()` (страницы 1,2,3…), `take_task()`, `submit_report()` с загрузкой скриншотов, `fetch_review_tasks('fail'|'fix')`.
- **`browser_log.py`** — детальный лог в `data/logs/browser_*.log`.

### Исполнители (`executors/`)

Маршрутизация по `TaskAnalysis.category`:

| Executor | Когда |
|----------|-------|
| `visit_urls` | «Перейти на сайт», surfing |
| `social` | Подписки VK/YouTube/Telegram через сохранённые сессии |
| `ai_browser` | Сложные сценарии через AI-план |
| `browser_task` | Общие браузерные действия |
| `manual` | Невозможно автоматизировать — инструкция |

### Аналитика (`analytics/`)

- **`journal.py`** — после каждого задания сохраняет JSON с действиями, recon, collected_data, payload отчёта, путями скриншотов.
- **`reviewer.py`** — при старте pipeline парсит `earn-task-fail` и `earn-task-fix`, сравнивает с журналом и ТЗ, генерирует рекомендации AI; fix-задания ставятся в очередь на повтор.

### Отчёты

- **`report_builder.py`** — собирает **все** данные исполнителя (ID, дата регистрации, сессии, URL, скрины) и генерирует текст отчёта по ТЗ.

---

## Переменные `.env`

| Переменная | Назначение |
|------------|------------|
| `OPENROUTER_API_KEY` | Ключ OpenRouter |
| `OPENROUTER_MODEL` | Модель (по умолчанию `google/gemini-2.5-flash`) |
| `EXECUTOR_TELEGRAM` | Telegram для отчётов |
| `EXECUTOR_EMAIL` | Email для отчётов |
| `EXECUTOR_ACCOUNT_ID` | ID аккаунта SEOsprint |
| `EXECUTOR_REGISTRATION_DATE` | Дата регистрации |
| `SEOSPRINT_BASE_URL` | Базовый URL (https://seosprint.net) |
| `SEOSPRINT_COOKIES_FILE` | Путь к cookies (data/cookies.json) |
| `BROWSER_PROFILE_DIR` | Профиль браузера (data/browser_profile) |
| `HEADLESS` | false = видимый браузер |
| `BROWSER_SLOW_MO` | Задержка между действиями (мс) |
| `BROWSER_DEBUG` | Подробные логи браузера |
| `BROWSER_CHANNEL` | `chrome` / `msedge` — системный браузер вместо bundled Chromium |

---

## Pipeline: порядок работы

```
1. Аналитика (earn-task-fail, earn-task-fix) → reviews + fix-очередь
2. Загрузка заданий (earn-task/1, /2, /3 … до пустой страницы)
3. Для каждого задания:
   a. Разведка (read-task без «Начать»)
   b. Проверка сессий (VK, YouTube, …)
   c. Выполнение (executor)
   d. Генерация полного отчёта (report_builder)
   e. Взять задание (если ещё не в работе)
   f. Submit + скриншоты
   g. Сохранение в data/analytics/executions/
4. Повтор fix-заданий из очереди доработки
```

---

## Что автоматизируется

| Тип | Pipeline |
|-----|----------|
| «Перейти на N сайтов» + скрин | ✓ полностью |
| Подписки VK/YouTube/Telegram | ✓ при наличии сессии |
| Регистрация, сложные формы | инструкция + отчёт |
| Казино / депозиты | пропуск (`--auto-only`) |

---

## Важно

- Репозиторий содержит **cookies, browser profile и .env** — не публикуйте публично без смены ключей.
- Первый запуск: `python main.py login`
- Для соцсетей: `python main.py login-sessions`
- Софт не обходит капчу и SMS
- Используйте в рамках правил SEOsprint
