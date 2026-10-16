# SPDX-License-Identifier: Apache-2.0
# Vendored from axisrow/telegram-mcp sanitize.py @ e224762 (2026-10-16).
# Upstream copyright holders: pathknower, Arnout van Westen, Omid Zaferi,
# Alexander Kulikov, 4erdenko. Full license text: LICENSES/apache-2.0.txt;
# notice: THIRD_PARTY_NOTICES.md.
# Modifications vs upstream (kept minimal, see git history):
#   - module docstring adapted to this project (LLM prompt boundary, not MCP);
#   - dropped MCP-specific format_tool_result/_json_default (this project
#     feeds plain-text prompts, not JSON tool-result envelopes).
"""Sanitization utilities for untrusted Telegram content entering LLM prompts.

All collected Telegram content (message text, channel titles, author names)
MUST be sanitized before it is rendered into an LLM prompt. This prevents
prompt-injection attacks where invisible Unicode in a collected message
(zero-width characters, bidi overrides, control characters) carries
instructions that the consuming model would follow.

Defence strategy:
1. Content sanitization (this module) — strips control, zero-width and bidi
   characters, collapses newline runs, truncates oversized text.
2. Storage boundary — the SQLite copy of a message is never rewritten;
   sanitization happens only on the path to an LLM/agent surface.
"""

import re
import unicodedata
from typing import Any, Optional

# Zero-width and invisible Unicode characters that can be used to hide content
_INVISIBLE_CHARS = re.compile(
    "["
    "​"  # zero width space
    "‌"  # zero width non-joiner
    "‍"  # zero width joiner
    "‎"  # left-to-right mark
    "‏"  # right-to-left mark
    " "  # line separator
    " "  # paragraph separator
    "‪-‮"  # bidi embedding/override
    "⁠"  # word joiner
    "⁡-⁤"  # invisible operators
    "﻿"  # zero width no-break space / BOM
    "￹-￻"  # interlinear annotations
    "]"
)

# Three or more consecutive newlines → collapse to two
_EXCESSIVE_NEWLINES = re.compile(r"\n{3,}")


def sanitize_user_content(
    text: Optional[str], max_length: int = 4096, *, preserve_emoji: bool = False
) -> str:
    """Sanitize user-controlled text content before returning in tool results.

    - Returns "[empty]" for None / empty input
    - Strips Unicode control characters (Cc, Cf) except newline and tab
    - Strips zero-width / invisible characters
    - Collapses excessive consecutive newlines (>2) to 2
    - Truncates to max_length with a marker
    - With preserve_emoji, retains emoji joiners and subdivision-flag tag characters

    This does NOT attempt keyword-based injection detection (too brittle).
    """
    if not text:
        return "[empty]"

    # Strip control characters except \n (0x0a) and \t (0x09)
    cleaned = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Cf"):
            if ch in ("\n", "\t"):
                cleaned.append(ch)
            elif preserve_emoji and (ch == "‍" or "\U000e0020" <= ch <= "\U000e007f"):
                cleaned.append(ch)
            # else: drop the character
        else:
            cleaned.append(ch)
    result = "".join(cleaned)

    # Strip invisible / zero-width characters
    result = _INVISIBLE_CHARS.sub(
        lambda match: match[0] if preserve_emoji and match[0] == "‍" else "", result
    )

    # Collapse excessive newlines
    result = _EXCESSIVE_NEWLINES.sub("\n\n", result)

    # Strip leading/trailing whitespace
    result = result.strip()

    if not result:
        return "[empty]"

    # Truncate
    if len(result) > max_length:
        result = result[:max_length] + "... [truncated]"

    return result


def sanitize_name(text: Optional[str], max_length: int = 256) -> str:
    """Sanitize a display name (username, chat title, sender name).

    Names should be single-line, so newlines are stripped entirely
    in addition to the standard sanitization.
    """
    result = sanitize_user_content(text, max_length=max_length)
    # Names must be single-line
    result = result.replace("\n", " ").replace("\r", " ")
    # Collapse multiple spaces that might result from newline replacement
    result = re.sub(r" {2,}", " ", result).strip()
    return result


def sanitize_dict(data: Any) -> Any:
    """Recursively sanitize all string and bytes values in a nested dict/list structure.

    Use this for raw structures where user-controlled content can appear at
    any nesting depth (e.g. raw API payloads). Bytes are decoded (UTF-8,
    replacing errors) and sanitized as text, matching how they are later
    serialized to JSON.
    """
    if isinstance(data, dict):
        return {k: sanitize_dict(v) for k, v in data.items()}
    if isinstance(data, list):
        return [sanitize_dict(item) for item in data]
    if isinstance(data, bytes):
        return sanitize_user_content(data.decode("utf-8", errors="replace"), max_length=4096)
    if isinstance(data, str):
        return sanitize_user_content(data, max_length=4096)
    return data
