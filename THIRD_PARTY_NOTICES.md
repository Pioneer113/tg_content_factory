# Third-party notices

Vendored third-party code carried in this repository, with license and provenance.
Each vendored file carries its own `SPDX-License-Identifier` header and a
provenance comment listing the source repo, commit, date, and modifications.

## telegram-mcp (Apache-2.0)

- **Source:** [axisrow/telegram-mcp](https://github.com/axisrow/telegram-mcp), commit `e224762`
- **License:** Apache-2.0 (full text: [`LICENSES/apache-2.0.txt`](LICENSES/apache-2.0.txt))

- `src/telegram/schema_drift.py` — schema-drift detection (`_is_schema_drift` and
  the schema-drift message of `log_and_format_error`) extracted from
  `telegram_mcp/runtime.py` at commit `e224762671897c1df53deadb51be84eaaaf2cea3`
  (2026-10-13); the rest of the upstream file is not ported.
- `src/telegram/rich_text.py` — RichText/page-block flattening (`rich_text_to_str`,
  `_page_lines` and the `_RICH_TEXT_FIELDS`/`_PAGE_TEXT_FIELDS`/`_RICH_TEXT_TYPES`
  constants) extracted from `telegram_mcp/runtime.py` at commit
  `e224762671897c1df53deadb51be84eaaaf2cea3` (2026-10-16, #1547). Modifications:
  the block-join tail of upstream's `rich_message_text` lifted into a shared
  `_blocks_text`; `webpage_page_text` is our own addition (same walk applied to
  `MessageMediaWebPage` cached pages); upstream's `rich_message_text` wrapper
  itself is not ported — telethon 1.45 does not surface `msg.rich_message`.
- `src/telegram/session_lock.py` — per-session cross-process lock (`SessionLock`,
  `SessionLockError` and the fcntl/msvcrt primitives) from `telegram_mcp/singleton.py`
  at commit `e224762671897c1df53deadb51be84eaaaf2cea3` (2026-10-16, #1548).
  Modifications: docstrings/error wording recontextualised for tg-agent processes and
  the lock-directory default renamed; the process-level lock registry and the
  `acquire_session_lock()` wrapper are tg-agent wiring added on top of the vendored
  code in later commits.

- `src/utils/sanitize.py` — prompt-injection sanitizer (`sanitize_user_content`,
  `sanitize_name`, `sanitize_dict`) ported from `sanitize.py` at commit
  `e224762671897c1df53deadb51be84eaaaf2cea3` (2026-10-16). Modifications:
  module docstring adapted to this project; MCP-specific
  `format_tool_result`/`_json_default` dropped (this project feeds plain-text
  prompts, not JSON tool-result envelopes).

The full Apache-2.0 license text is in `LICENSES/apache-2.0.txt`.

Per the project licensing strategy, the long-term goal is to replace this
vendored Apache-2.0 code with an original MIT implementation; when a file is
fully rewritten, its SPDX header becomes MIT and this entry is removed.
