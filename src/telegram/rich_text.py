# SPDX-License-Identifier: Apache-2.0
# Ported from axisrow/telegram-mcp@e224762 (telegram_mcp/runtime.py): the
# _RICH_TEXT_TYPES/_RICH_TEXT_FIELDS/_PAGE_TEXT_FIELDS constants and the
# rich_text_to_str / _page_lines functions, ported 2026-10-16 (#1547).
# Modifications vs upstream: extracted into a standalone module (upstream file
# is monolithic); the block-join tail of upstream's rich_message_text lifted
# into _blocks_text so both rich messages and MessageMediaWebPage cached pages
# reuse it; upstream's rich_message_text wrapper itself was not ported because
# telethon 1.45 does not surface msg.rich_message. See THIRD_PARTY_NOTICES.md.
"""Рекурсивный флэттнинг Telegram RichText-деревьев и iv/page-блоков в plain text.

Канальный пост, состоящий из ссылки, оставляет ``msg.text`` пустым, а весь
текст несёт в превью-странице (``WebPage.cached_page``) как iv-блоки — читатель,
смотрящий только на ``msg.message``, репортит пост пустым (#1350/#1352: слой
Telegram молча рапортует «успех» на пустом сообщении). Флэттнинг здесь —
источник plain-текста для таких сообщений.
"""

from __future__ import annotations

from typing import List, get_args

from telethon import types

# RichText is a recursive tree: a node either holds a plain string, wraps
# another node, or concatenates a list of them. Dispatching on the field rather
# than on the class keeps a node type Telegram adds later flattening instead of
# vanishing.
_RICH_TEXT_FIELDS = ("texts", "text", "alt", "source")

# Where a page block, list item, table row or caption keeps its words. Same walk
# covers the blocks nested inside details, collages and embedded posts.
_PAGE_TEXT_FIELDS = (
    "title",
    "subtitle",
    "author",
    "text",
    "caption",
    "credit",
    "items",
    "blocks",
    "rows",
    "articles",
)

_RICH_TEXT_TYPES = tuple(get_args(types.TypeRichText))


def rich_text_to_str(node) -> str:
    """Flatten one RichText node into plain text.

    TextCustomEmoji contributes its alt character - dropping it would silently
    eat the emoji a channel used as a bullet or a heading marker.
    """
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, (list, tuple)):
        return "".join(rich_text_to_str(item) for item in node)
    for field in _RICH_TEXT_FIELDS:
        value = getattr(node, field, None)
        if value is not None:
            return rich_text_to_str(value)
    return ""  # TextEmpty, TextImage and anything else carrying no text


def _page_lines(node) -> List[str]:
    """Text lines carried by a page block, list item, table row or caption."""
    if node is None:
        return []
    if isinstance(node, (list, tuple)):
        return [line for item in node for line in _page_lines(item)]
    if isinstance(node, _RICH_TEXT_TYPES):
        text = rich_text_to_str(node).strip()
        return [text] if text else []
    cells = getattr(node, "cells", None)
    if cells is not None:  # a table row reads as one line, not one line per cell
        row = " | ".join(line for cell in cells for line in _page_lines(cell))
        return [row] if row else []
    return [line for f in _PAGE_TEXT_FIELDS for line in _page_lines(getattr(node, f, None))]


def _blocks_text(blocks) -> str:
    """Plain text of a page-block list, "" when there is none.

    Each block becomes a paragraph and the lines within one block stay together,
    so a list reads as a list instead of one run-on line. An unknown block type
    yields nothing rather than breaking the whole message.
    """
    if not blocks:
        return ""
    paragraphs = ("\n".join(_page_lines(block)) for block in blocks)
    return "\n\n".join(p for p in paragraphs if p)


def webpage_page_text(msg) -> str:
    """Plain text of a link-preview instant-view page, "" when there is none."""
    page = getattr(getattr(getattr(msg, "media", None), "webpage", None), "cached_page", None)
    return _blocks_text(getattr(page, "blocks", None)) if page is not None else ""
