# Third-party notices

This project incorporates third-party code covered by the licenses below.

## Apache-2.0

Portions of this project are derived from
[telegram-mcp](https://github.com/axisrow/telegram-mcp) (Apache-2.0). Ported code:

- `src/telegram/schema_drift.py` — schema-drift detection (`_is_schema_drift` and
  the schema-drift message of `log_and_format_error`) extracted from
  `telegram_mcp/runtime.py` at commit `e224762671897c1df53deadb51be84eaaaf2cea3`
  (2026-10-13); the rest of the upstream file is not ported.

The full Apache-2.0 license text is in `LICENSES/apache-2.0.txt`.
