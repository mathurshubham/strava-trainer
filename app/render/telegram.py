"""Telegram HTML rendering: escape -> markdown->HTML conversion -> whitelist
sanitize -> tag-balance repair -> length-aware chunking.

PRD §5.3 is explicit that this is the most common way this class of bot
breaks and should be built (and tested) before the LLM agents. Nothing here
ever sends unvalidated LLM output straight to Telegram — see `render_message`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MESSAGE_LIMIT = 4096
CAPTION_LIMIT = 1024

# tag -> set of attribute names allowed on it. Empty set = no attributes allowed.
ALLOWED_TAGS: dict[str, set[str]] = {
    "b": set(),
    "strong": set(),
    "i": set(),
    "em": set(),
    "u": set(),
    "ins": set(),
    "s": set(),
    "strike": set(),
    "del": set(),
    "span": {"class"},
    "tg-spoiler": set(),
    "a": {"href"},
    "code": {"class"},
    "pre": set(),
    "blockquote": {"expandable"},
    "tg-emoji": {"emoji-id"},
}

_TOKEN_RE = re.compile(r"<[^>]+>|[^<]+")
_TAG_RE = re.compile(r"^<(/?)([a-zA-Z-]+)((?:\s+[^>]*)?)>$")
_ATTR_RE = re.compile(r'([a-zA-Z-]+)\s*=\s*"([^"]*)"')


def escape_html_text(s: str) -> str:
    """Escape &, <, > in literal text. Order matters — & must go first (PRD §5.3.1)."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ── restricted-markdown -> Telegram HTML ─────────────────────────────────

_INLINE_PATTERN = re.compile(
    r"(?P<bold>\*\*(?P<bold_text>.+?)\*\*)"
    r"|(?P<italic>(?<!\*)\*(?P<italic_text>[^*]+?)\*(?!\*))"
    r"|(?P<code>`(?P<code_text>[^`]+?)`)"
)


def _render_inline(text: str) -> str:
    """Convert **bold**, *italic*, `code` within one line. Everything else is
    escaped literal text — the model never gets to emit real HTML tags."""
    out = []
    pos = 0
    for m in _INLINE_PATTERN.finditer(text):
        out.append(escape_html_text(text[pos : m.start()]))
        if m.group("bold"):
            out.append(f"<b>{escape_html_text(m.group('bold_text'))}</b>")
        elif m.group("italic"):
            out.append(f"<i>{escape_html_text(m.group('italic_text'))}</i>")
        elif m.group("code"):
            out.append(f"<code>{escape_html_text(m.group('code_text'))}</code>")
        pos = m.end()
    out.append(escape_html_text(text[pos:]))
    return "".join(out)


def markdown_to_telegram_html(markdown: str) -> str:
    """PRD §5.3: 'Prompt the LLM to emit a restricted markdown subset ... Do not
    ask the model to emit HTML directly.' This is the deterministic converter.
    Supports **bold**, *italic*, `code`, and `- ` bullets (faked with •, since
    Telegram HTML has no <ul>/<li>)."""
    lines = markdown.replace("\r\n", "\n").split("\n")
    rendered_lines = []
    for line in lines:
        stripped = line.strip()
        bullet_match = re.match(r"^[-*]\s+(.*)$", stripped)
        if bullet_match:
            rendered_lines.append(f"• {_render_inline(bullet_match.group(1))}")
        else:
            rendered_lines.append(_render_inline(line))
    return "\n".join(rendered_lines)


# ── whitelist sanitizer + tag-balance repair ─────────────────────────────


def _parse_tag(token: str) -> tuple[bool, str, dict[str, str]] | None:
    """Returns (is_closing, tag_name, attrs) or None if `token` isn't a tag."""
    m = _TAG_RE.match(token)
    if not m:
        return None
    is_closing = bool(m.group(1))
    name = m.group(2).lower()
    attrs = dict(_ATTR_RE.findall(m.group(3) or ""))
    return is_closing, name, attrs


def _filter_attrs(tag: str, attrs: dict[str, str]) -> dict[str, str]:
    allowed = ALLOWED_TAGS.get(tag, set())
    filtered = {k: v for k, v in attrs.items() if k in allowed}
    if tag == "span" and filtered.get("class") != "tg-spoiler":
        filtered.pop("class", None)
    return filtered


def _render_open_tag(tag: str, attrs: dict[str, str]) -> str:
    if not attrs:
        return f"<{tag}>"
    attr_str = " ".join(f'{k}="{v}"' for k, v in attrs.items())
    return f"<{tag} {attr_str}>"


def sanitize_telegram_html(html: str) -> str:
    """Whitelist-validate: strip any tag not in ALLOWED_TAGS (keeping its text
    content), strip disallowed attributes, and auto-close unbalanced tags.
    Defense in depth on top of markdown_to_telegram_html — PRD §5.3.2/5.3.3:
    'Never send unvalidated LLM output straight to Telegram' /
    'Unclosed tags -> Telegram 400 and the message is lost.'"""
    out: list[str] = []
    stack: list[str] = []

    for token in _TOKEN_RE.findall(html):
        parsed = _parse_tag(token)
        if parsed is None:
            out.append(escape_html_text(token) if _looks_unescaped(token) else token)
            continue

        is_closing, tag, attrs = parsed
        if tag not in ALLOWED_TAGS:
            continue  # strip the tag itself; inner text already emitted separately by the tokenizer

        if is_closing:
            if tag in stack:
                # close any inner tags opened after this one too, to keep nesting valid
                while stack and stack[-1] != tag:
                    out.append(f"</{stack.pop()}>")
                if stack:
                    out.append(f"</{stack.pop()}>")
        else:
            out.append(_render_open_tag(tag, _filter_attrs(tag, attrs)))
            stack.append(tag)

    while stack:
        out.append(f"</{stack.pop()}>")

    return "".join(out)


def _looks_unescaped(text: str) -> bool:
    """Our own renderer always escapes text before this point, so this only
    fires for content assembled elsewhere. `&amp;`/`&lt;`/`&gt;` pass through."""
    return bool(re.search(r"&(?!amp;|lt;|gt;|#\d+;|\w+;)|<|>", text))


# ── chunking ──────────────────────────────────────────────────────────────


@dataclass
class Chunk:
    text: str


def chunk_html(html: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Split at paragraph boundaries first, never mid-tag, re-opening any tags
    that were open at the split point (PRD §5.3.4)."""
    if len(html) <= limit:
        return [html] if html else []

    tokens = _TOKEN_RE.findall(html)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    stack: list[tuple[str, dict[str, str]]] = []

    def open_tags_overhead() -> int:
        return sum(len(_render_open_tag(t, a)) for t, a in stack) + sum(len(f"</{t}>") for t, _ in stack)

    def flush():
        nonlocal current, current_len
        for tag, _ in reversed(stack):
            current.append(f"</{tag}>")
        chunks.append("".join(current))
        current = [_render_open_tag(tag, attrs) for tag, attrs in stack]
        current_len = sum(len(c) for c in current)

    for token in tokens:
        parsed = _parse_tag(token)
        if parsed is not None:
            is_closing, tag, attrs = parsed
            if current_len + len(token) > limit:
                flush()
            current.append(token)
            current_len += len(token)
            if is_closing:
                if stack and stack[-1][0] == tag:
                    stack.pop()
            else:
                stack.append((tag, attrs))
            continue

        # text token: split on paragraph boundaries if it doesn't fit
        remaining = token
        while remaining:
            budget = limit - current_len
            if len(remaining) <= budget:
                current.append(remaining)
                current_len += len(remaining)
                remaining = ""
                break

            split_at = _find_split_point(remaining, budget)
            if split_at <= 0:
                flush()
                continue
            current.append(remaining[:split_at])
            current_len += split_at
            remaining = remaining[split_at:]
            flush()

    if current or not chunks:
        for tag, _ in reversed(stack):
            current.append(f"</{tag}>")
        chunks.append("".join(current))

    return [c for c in chunks if c]


def _find_split_point(text: str, budget: int) -> int:
    if budget <= 0:
        return 0
    window = text[:budget]
    para = window.rfind("\n\n")
    if para > 0:
        return para + 2
    newline = window.rfind("\n")
    if newline > 0:
        return newline + 1
    space = window.rfind(" ")
    if space > 0:
        return space + 1
    return budget


def chunk_caption(html: str, limit: int = CAPTION_LIMIT) -> list[str]:
    return chunk_html(html, limit=limit)


def strip_all_tags(html: str) -> str:
    """PRD §5.3.8: 'Fallback: if a send returns 400, retry once with
    parse_mode=None and tags stripped, so the user always gets the content
    even if formatting fails.'"""
    return "".join(token for token in _TOKEN_RE.findall(html) if _parse_tag(token) is None)


def render_message(markdown: str) -> list[str]:
    """The one function the Telegram client should call: markdown -> sanitized,
    balanced, chunked HTML ready for `sendMessage`."""
    html = markdown_to_telegram_html(markdown)
    safe = sanitize_telegram_html(html)
    return chunk_html(safe)
