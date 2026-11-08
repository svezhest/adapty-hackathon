# План: агенты Level 1/Level 2 и новый контракт в `main` с поддержкой старого

## Контекст

Саша теперь отвечает за агентский уровень Resolve (репо `~/Projects/adapty-hackathon`). v1 Милоша: один вызов Claude с заранее загруженным профилем, один `Proposal`, Approve. Его надо заменить схемой, которую описал Саша:

- **Level 1, triage-агент** (главный деливерабл, для саппорта): берёт фидбэк, сам собирает данные о пользователе, находит проблему и строит тикет с 2–3 вариантами. Саппорт принимает вариант, правит его (включая смену типа действия на любое разумное) или отклоняет все.
- **Level 2, first-line агент** (для пользователей приложения, вызывается из приложения): возможности берёт из конфига, лимиты проверяет код, а то, с чем не справился, отправляет фидбэком в triage.

Решения, принятые в чате:
- агент и контракт идут в `main`;
- пушу только с согласия Саши, перед этим всё тестируем локально;
- старый контракт должен продолжать работать, а новый агентский при этом не трогаем;
- куда доставлен ответ, знать не нужно: принятый тикет просто отмечается обработанным;
- 60 синтетических тикетов плюс наша синтетика становятся входящим фидбэком;
- агенты работают и на Anthropic, и на OpenAI, провайдер выбирается переключателем; сейчас будут токены OpenAI;
- Level 2 сам выдаёт доступ в пределах лимитов;
- рефанд Level 2 сам не обрабатывает никогда;
- в `main` идёт бэкенд и JSON API; мой дизайн из 4 вкладок делаю отдельной веткой;
- APK-мок и веб-чат для отладки — страховка.

Спека: `docs/agents.md` (уже написана, этим планом уточняется).

## Переключатель: провайдер LLM

Переключатель выбирает провайдера, а не контракт. Оба агента, triage и Level 2, полностью наши, и один и тот же агентский контракт работает и на Anthropic, и на OpenAI:
- в `.env` задаётся `LLM_PROVIDER=anthropic|openai`;
- `resolve/llm.py` даёт обоим агентам один интерфейс `step(system, messages, tools) -> Step(text | tool_calls)` и две реализации:
  - `AnthropicLLM`: `messages.create` с `tools`;
  - `OpenAILLM`: Chat Completions с function tools в strict-режиме;
- промпты, инструменты, схемы (`TriageTicket` и аргументы инструментов из pydantic), лимиты и логи общие; от провайдера зависит только перевод формата сообщений и tool calls;
- модель задаётся в `ANTHROPIC_MODEL` или `OPENAI_MODEL`. Для Anthropic остаётся текущее умолчание Милоша: самый новый Opus из списка моделей.

Старый одиночный агент (`agent.py` с одним `Proposal`) заменяется triage. Старые HTML-страницы остаются рабочими до замены дизайном: страница тикета показывает варианты, Approve равен `accept(option=0)`. Тесты, привязанные к старому `Proposal` (`test_agent.py`, часть `test_workflow.py` и `test_web.py`), переписываются под новый контракт. Остальные тесты Милоша (Adapty-клиент, генератор, insights, идемпотентность) остаются.

## Файлы

Новые:
- `resolve/llm.py`: `LLM.step(system, messages, tools) -> Step(text | tool_calls)`, реализации `AnthropicLLM` и `OpenAILLM`, выбор через `LLM_PROVIDER`. `OPENAI_BASE_URL` необязателен. `FakeLLM` со сценарием шагов для тестов. Плюс маленький тест формата на каждого провайдера: перевод tool calls туда и обратно на записанных ответах, без сети.
- `resolve/policy.py`: чистые функции. `check_grant(profile, config, grants_30d, days) -> (allowed, reason)` и `load/save config`. Проверки: инструмент включён, `days ≤ max_days`, `require_paid`, не lifetime, лимит выдач за 30 дней, не больше одной выдачи за сессию.
- `resolve/actions.py`: единый исполнитель `execute(action, ticket|session, source)`. Для `grant_access` повторно использует `compensation_expiry`, `Client.grant_access_level` и штамп `update_profile` из `adapty.py`/`workflow.py`, записывает строку в `grants`. Идемпотентность через условный переход статуса, как в `db.set_status_if`.
- `resolve/frontline.py` (Level 2):
  - `chat(services, customer_user_id, session_id, message)`.
  - Инструменты: `get_profile`, `grant_access(days, reason)`, `store_instructions(kind, store)`, `handoff(reason, summary)`, `mark_resolved()`. Модели видны только включённые в конфиге.
  - Каждый вызов проходит через `policy` и логируется в `l2_events`.
  - Отказ возвращается модели результатом инструмента.
  - Не больше 4 вызовов модели на сообщение, не больше `max_turns` сообщений на сессию.
  - `handoff` создаёт тикет с `source=chat_handoff` и запускает triage в фоне (FastAPI `BackgroundTasks`).
- `resolve/triage.py` (Level 1):
  - Инструменты: `get_profile`, `find_user_by_email`, `get_user_history`, `get_agent_config`, `submit_ticket`. Аргументы `submit_ticket` описаны pydantic-моделью `TriageTicket`: user, summary, category из существующих `CATEGORIES`, evidence, root_cause, confidence и options[2..3]. Каждый option: action, params, reply, risk, risk_reason.
  - Действия: `grant_access(days)`, `reply_only`, `store_instructions(kind)`, `refund_to_human`, `ask_user(question)`, `no_reply`.
  - Не больше 6 инструментов на тикет. Невалидный `submit_ticket` получает одну попытку исправиться.
  - Учёт вызовов модели переиспользует `db.reserve_model_call`.
- `resolve/api.py`: JSON-роуты из спеки: `/chat`, `/api/chat/{id}`, `/feedback`, `/api/tickets*` (triage/accept/reject), `/api/config`, `/api/analytics`, `/api/users`. Подключается в `web.create_app`.
- `resolve/analytics.py`: SQL-подсчёты для Analyze по списку из спеки.
- `scripts/import_feedback.py`: 60 тикетов Милоша и наши синтетические кейсы по мотивам `kb/E-real-cases.md`, только на засеянных профилях с подходящим состоянием, с source/email.
- `scripts/seed_emails.py`: `updateProfile` email на нескольких засеянных профилях плюс наш индекс email. Запуск только с согласия, это запись в Adapty.
- `mobile-mock/`: Android-приложение (Kotlin, один экран чата плюс отладочные кнопки: сменить пользователя, сбросить диалог, показать действия, поле base URL), ходит в `/chat`.

Меняются:
- `resolve/db.py`:
  - новые таблицы `conversations`, `messages`, `l2_events`, `grants`, `agent_config`, `email_index`;
  - в `tickets` добавляются колонки `source`, `email`, `session_id`, `triage_json`, `triage_error`, `decision_json` через `ALTER TABLE ADD COLUMN`, если их нет: старый `demo.sqlite` тоже мигрирует;
  - тикет без пользователя получает `customer_user_id=''`.
- `resolve/workflow.py`: `Services.llm` вместо `Services.anthropic`; `propose` запускает triage; `approve` равен `accept(option=0)` через `actions.execute`.
- `resolve/agent.py`: удаляется. Полезное (`profile_summary`, правила из промпта) переезжает в `triage.py`.
- `resolve/insights.py`: `analyse` идёт через `llm` (`submit` с моделью `Insight`).
- `resolve/config.py`, `.env.example`: `LLM_PROVIDER`, `ANTHROPIC_API_KEY`/`ANTHROPIC_MODEL`, `OPENAI_API_KEY`/`OPENAI_MODEL`/`OPENAI_BASE_URL`.
- Triage стартует сам в фоне (FastAPI `BackgroundTasks`) на handoff и на `/feedback`. Импортированные 60 тикетов запускаются только по кнопке или скриптом.
- `requirements.txt`: пакет `openai` с точной версией, при установке беру актуальную стабильную.
- `data/eval.json`: переразметка t-017 (профиль показывает рефанд, значит `reply_only` или `explain`) и других спорных случаев, с `note`. Сложные кейсы: профиль не найден, расхождение тикета с профилем, lifetime, уже вернули деньги, доступ уже выдан нами, prompt injection.
- `scripts/eval.py`: оценивает triage (категорию и действие `options[0]`) плюс сценарии Level 2 (разрешил ли код правильное действие).
- `README.md`, `AGENTS.md`:
  - уровни переименовываются под новую схему;
  - правило «любая запись по клику» становится «Level 2 пишет в пределах лимитов конфига, остальное по клику»;
  - в AI_LOG записываю по ходу работы.

## Порядок

1. `llm.py` (оба провайдера), `FakeLLM`, `policy.py`, миграция `db.py`.
2. `actions.py`, `workflow` на triage, старые страницы на вариантах. Тесты Adapty-клиента, генератора и идемпотентности остаются зелёными.
3. `frontline.py` и `/chat`, `/api/chat`.
4. `triage.py`, `/feedback`, `/api/tickets*`, accept и reject с правкой.
5. `/api/config`, `/api/analytics`, `/api/users`.
6. Импорт фидбэка, переразметка eval, новый eval.
7. Слепые тесты: **один** субагент пишет их только по `docs/agents.md`, реализацию не видит. Падения разбираю с Сашей.
8. README, AGENTS.md, AI_LOG. Локальная проверка, показываю diff, пушу после «да».
9. Отдельно в ветке `alex/design`: мои 4 вкладки (Incoming, Analyze, Agent settings, Chat) в стиле Adapty поверх JSON API.
10. `mobile-mock/` APK. Если Gradle захочет скачать зависимости, спрошу.

## Проверка

- `python -m pytest`: тесты Милоша, не привязанные к старому `Proposal`, зелёные без изменений, плюс новые на фейках, без сети и ключей. Список переписанных тестов покажу.
- Живой прогон на обоих провайдерах, если найдутся оба ключа: один и тот же сценарий в Chat должен дать одинаковые решения по политике.
- `DEMO_MODE=1 python -m resolve`: старый UI работает на мигрированном `demo.sqlite`.
- После того как появятся токены OpenAI и `ADAPTY_SECRET_KEY` (Саша сам впишет их в `.env`):
  - сценарий в Chat: «оплатил, а закрыто», получаем grant в пределах лимита, проверяем `getProfile`;
  - повторная жалоба: блокировка лимитом и handoff;
  - «верните деньги»: handoff, тикет в Incoming с 2–3 вариантами, accept с правкой дней, проверяем `getProfile`;
  - prompt injection: блок;
  - `scripts/eval.py` один раз с согласия, потому что он платный.
- APK: собрать, поставить на эмулятор или телефон в той же сети, пройти тот же сценарий.

## Объявленные заглушки

- Входные каналы, кроме чата и `/feedback`, не делаем: почты и App Store нет.
- Индекс email в демо заполняется скриптом, а не вебхуками.
- Отправка ответа — только статус.
