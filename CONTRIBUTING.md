# Contributing to tg-agent

Telegram automation for coding agents — shipped as a **Claude Code plugin** (skill + CLI). The CLI is the contract; the skill drives it.

## Current status: stabilization

The project is in a **bug-fixing phase** — new features are paused. The most
valuable contributions right now are **bug reports with repro steps**. If you
want to write code, pick from the open bug issues or ask first before starting
a feature.

## Reporting bugs

Include:

- `tg-agent --version` (or install commit)
- the exact command and its full output (add `--debug` if available)
- how many accounts are configured and whether a `worker` is running
- what you expected vs what happened

Never paste session strings, phone numbers, or `.env` contents — those are
credentials, not logs.

## Architecture map

```
entrypoints (src/cli, src/web, src/agent)   ← no telethon imports allowed here
        ↓
services + repositories (src/services, src/database)
        ↓
telegram backends (src/telegram)            ← all raw Telethon lives here
```

- **Layering is enforced**, not conventional: `import-linter` (`.importlinter`)
  fails CI if an entrypoint imports `telethon` directly. Raw calls belong in
  `src/telegram` behind `TelegramTransportSession` and the backend router.
- **Client pool** (`src/telegram/client_pool.py` + `pool_lifecycle.py` /
  `pool_dialogs.py` / `pool_flood.py`): per-account lifecycle, lease
  acquisition/release, flood-wait rotation. Read the module docstrings first —
  they document the invariants (lock order, pool↔DB ordering, lease teardown).
- **Flood control** is `telethon-floodgate` (circuit breaker + rate gate);
  security-warning recovery lives in `mtproto_watchdog.py`.
- Broader overview: [docs/architecture.md](docs/architecture.md).

## Development setup

```bash
python 3.11+
pip install -e '.[dev]'
```

Create `.env` in the working directory (never commit it — session strings are
full account access):

```
TG_API_ID=...
TG_API_HASH=...
```

## Tests

The suite runs **offline by default** — no real Telegram connection is needed
or made.

| Marker | Meaning |
|---|---|
| `unit` / `integration` / `smoke` | auto-applied by the conftest heuristic |
| `real_tg_safe` | opt-in real-API test, no Telegram-visible mutations |
| `real_tg_mutation_safe` | opt-in, bounded operator-approved mutations |
| `real_tg_manual` | opt-in, manual/high-risk scenarios |
| `slow` | excluded from the fast loop with `-m 'not slow'` |

```bash
pytest -m 'not slow'          # fast feedback loop
ruff check .
lint-imports                  # import-linter contract
```

Advisory tools wired into CI: `bandit`, `pip-audit`. Optional: `mutmut`
(mutation testing — see `pyproject.toml` notes before running it).

## Where to start

**Lower-bar surfaces** — good first contributions:

- skills and recipes (`skills/`), documentation (`docs/`)
- individual CLI commands (the CLI is the contract — new capabilities land here)
- web dashboard fixes (`src/web`, frozen but accepting bug fixes)

**Higher-bar core** — read first, then propose:

- `src/telegram/` (pool, flood, watchdog) — invariants are documented in
  module docstrings and issue references; changes here deserve an issue first

## PR expectations

- one logical change per PR
- tests with the correct level markers; bug fixes need a failing-test-first commit
- docstrings on public functions (interrogate ratchet is a soft floor, don't lower it)
- `ruff check .` and `lint-imports` clean; CI green
