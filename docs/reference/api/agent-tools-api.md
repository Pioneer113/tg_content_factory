# Agent Tools API

Инструменты AI-агента (`tg_agent/agent/tools/`) — модульные файлы, каждый из которых
регистрирует группу `@tool()`-функций через `register()`. Декларации категорий
(`TOOL_GROUPS`) — единый источник истины для прав доступа (READ/WRITE/DELETE),
из которого `permissions.py` выводит ACL.

!!! note
    Описания самих инструментов задаются строковыми аргументами декоратора
    `@tool("name", "description", ...)`, а не Python docstring, поэтому autodoc
    показывает структуру модуля (`register`, `TOOL_GROUPS`), а полный
    человекочитаемый каталог инструментов — в
    [Agent Tools Reference](../agent-tools.md).

## Каналы

::: tg_agent.agent.tools.channels

## Поиск

::: tg_agent.agent.tools.search

## Контент-пайплайны

::: tg_agent.agent.tools.pipelines

## Изображения

::: tg_agent.agent.tools.images

## Сообщения

::: tg_agent.agent.tools.messaging

## Коллекция

::: tg_agent.agent.tools.collection

## Аналитика

::: tg_agent.agent.tools.analytics

## Модерация

::: tg_agent.agent.tools.moderation

## Фильтры

::: tg_agent.agent.tools.filters

## Права доступа

::: tg_agent.agent.tools.permissions
