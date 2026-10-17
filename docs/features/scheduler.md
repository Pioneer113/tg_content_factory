# Планировщик

Автоматический периодический сбор сообщений и проверка поисковых запросов.

## Управление

=== "CLI"
    ```bash
    python -m tg_agent.main scheduler start          # включить автостарт (+ reconcile живого воркера)
    python -m tg_agent.main scheduler stop           # отключить автостарт
    python -m tg_agent.main scheduler trigger        # разовый запуск коллекции
    python -m tg_agent.main scheduler status         # статус и конфигурация
    python -m tg_agent.main scheduler job-toggle collection_job
    python -m tg_agent.main scheduler set-interval collection_job 60
    python -m tg_agent.main scheduler task-cancel 42
    python -m tg_agent.main scheduler clear-pending
    ```

=== "Web"
    `GET /scheduler/` · `POST /scheduler/start` · `POST /scheduler/stop`
    `POST /scheduler/trigger` · `POST /scheduler/jobs/{job_id}/toggle`
    `POST /scheduler/jobs/{job_id}/set-interval`

## Jobs

| Job | Описание |
|-----|----------|
| `collection_job` | Инкрементальный сбор всех каналов |
| `search_query_job` | Проверка поисковых запросов и отправка уведомлений |

## Уведомления

Тестирование:

=== "CLI"
    ```bash
    python -m tg_agent.main notification dry-run    # превью совпадений без отправки
    python -m tg_agent.main notification test       # тестовое уведомление
    ```

=== "Web"
    `POST /scheduler/dry-run-notifications` · `POST /scheduler/test-notification`

## Задачи коллекции

Статус задач (`pending/running/completed/failed/cancelled`) отслеживается в БД через `CollectionQueue`.
