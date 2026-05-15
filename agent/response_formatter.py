"""Format the agent's free-form answer for Telegram delivery.

We deliberately keep this simple: the agent already returns prose itinerary
text in Bahasa Indonesia. We just:
- Split into chunks that fit Telegram's 4096-char message limit.
- Strip common Markdown noise that doesn't render in plain text mode (Telegram
  auto-detects URLs, so we don't need Markdown link syntax).
- Trim trailing whitespace.
"""

from __future__ import annotations

import re
from typing import List

TELEGRAM_MAX_LEN = 4000


def _strip_md_links(text: str) -> str:
    return re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 \2", text)


def _strip_bold_italic(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    return text


def _normalize(text: str) -> str:
    text = _strip_md_links(text)
    text = _strip_bold_italic(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_for_telegram(text: str, max_len: int = TELEGRAM_MAX_LEN) -> List[str]:
    """Split on paragraph boundaries; fall back to hard split if a paragraph is huge."""
    text = _normalize(text)
    if len(text) <= max_len:
        return [text] if text else []

    chunks: List[str] = []
    current: List[str] = []
    current_len = 0
    for paragraph in text.split("\n\n"):
        para_len = len(paragraph) + 2
        if current_len + para_len > max_len and current:
            chunks.append("\n\n".join(current).strip())
            current = [paragraph]
            current_len = para_len
        else:
            current.append(paragraph)
            current_len += para_len
    if current:
        chunks.append("\n\n".join(current).strip())

    final: List[str] = []
    for chunk in chunks:
        if len(chunk) <= max_len:
            final.append(chunk)
            continue
        for i in range(0, len(chunk), max_len):
            final.append(chunk[i : i + max_len])
    return final


def format_for_telegram(text: str) -> List[str]:
    """Public entry point — returns a list of messages ready to send."""
    return split_for_telegram(text)
