# CLI Internals

Внутренности CLI (`tg_agent/cli/`) — обвязка runtime (логирование, PII-редакция),
разбор аргументов и обработчики команд. Человекочитаемый справочник команд —
в [CLI Reference](../cli.md); эта страница документирует Python-API из docstring.

!!! note
    Многие обработчики команд и парсеры пока без docstring — autodoc показывает
    их сигнатуры, описания появятся по мере наполнения (issue #1072).

## Runtime

Логирование и PII-редакция вывода CLI.

::: tg_agent.cli.runtime

## Команды (Typer)

Единая точка объявления CLI — приложение Typer (`app`). Каждая команда
объявляет свои флаги/аргументы как параметры Typer и зовёт общее тело
`*_impl` из `tg_agent/cli/commands/`. argparse-каркас удалён в #1125.

::: tg_agent.cli.typer_app

::: tg_agent.cli.typer_commands

## Точка входа

::: tg_agent.cli.main

## Обработчики команд

### Каналы

::: tg_agent.cli.commands.channel

### Сбор

::: tg_agent.cli.commands.collect

### Пайплайны

::: tg_agent.cli.commands.pipeline

### Поиск

::: tg_agent.cli.commands.search
