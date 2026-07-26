"""Telegram HTML renderer tests. PRD §11.1: 'Bold/italic/code/links render
correctly; zero 400s' and 'Messages >4096 chars chunk cleanly without breaking
tags' are acceptance criteria this file exercises directly.
"""
import re

import pytest

from app.render.telegram import (
    CAPTION_LIMIT,
    MESSAGE_LIMIT,
    chunk_caption,
    chunk_html,
    escape_html_text,
    markdown_to_telegram_html,
    render_message,
    sanitize_telegram_html,
)


def test_escape_order_ampersand_first():
    assert escape_html_text("5 > 3 && true") == "5 &gt; 3 &amp;&amp; true"
    assert escape_html_text("<script>") == "&lt;script&gt;"


def test_markdown_bold_italic_code():
    result = markdown_to_telegram_html("**bold** and *italic* and `code`")
    assert result == "<b>bold</b> and <i>italic</i> and <code>code</code>"


def test_markdown_bullets_become_bullet_glyphs():
    result = markdown_to_telegram_html("- first item\n- second item")
    assert result == "• first item\n• second item"


def test_markdown_escapes_literal_special_chars_outside_tags():
    result = markdown_to_telegram_html("volume > 5000kg & rising")
    assert result == "volume &gt; 5000kg &amp; rising"


def test_sanitize_strips_disallowed_tags_but_keeps_text():
    result = sanitize_telegram_html("<script>alert(1)</script>")
    assert "<script>" not in result
    assert "alert(1)" in result


def test_sanitize_strips_disallowed_attributes():
    result = sanitize_telegram_html('<a href="https://x.com" onclick="evil()">link</a>')
    assert result == '<a href="https://x.com">link</a>'


def test_sanitize_repairs_unclosed_tag():
    result = sanitize_telegram_html("<b>bold text")
    assert result == "<b>bold text</b>"


def test_sanitize_repairs_crossed_tags():
    result = sanitize_telegram_html("<b><i>text</b></i>")
    assert result == "<b><i>text</i></b>"


def _tags_balanced(chunk: str) -> bool:
    opens = re.findall(r"<([a-zA-Z-]+)(?:\s[^>]*)?>", chunk)
    closes = re.findall(r"</([a-zA-Z-]+)>", chunk)
    return sorted(opens) == sorted(closes)


def test_chunk_html_under_limit_returns_single_chunk():
    html = "<b>short message</b>"
    chunks = chunk_html(html)
    assert chunks == [html]


def test_chunk_html_splits_long_plain_text_and_preserves_content():
    paragraph = "word " * 30
    long_text = (paragraph + "\n\n") * 40  # comfortably over 4096 chars, no tags
    chunks = chunk_html(long_text, limit=500)
    assert len(chunks) > 1
    assert all(len(c) <= 500 for c in chunks)
    assert "".join(chunks) == long_text


def test_chunk_html_reopens_spanning_tags_and_stays_balanced():
    html = "<blockquote>" + ("word " * 60) + "</blockquote>"
    chunks = chunk_html(html, limit=100)
    assert len(chunks) > 1
    for c in chunks:
        assert _tags_balanced(c)
        assert len(c) <= 100 + len("<blockquote></blockquote>")  # small tolerance for reopen overhead
    # second chunk must reopen the tag that was still open at the split
    assert chunks[1].startswith("<blockquote>")


def test_render_message_full_pipeline():
    markdown = "**Push Day A**\n\n- Bench: 60kg x 8\n- Row: 50kg x 10\n\nVolume > 2000kg today."
    chunks = render_message(markdown)
    assert len(chunks) == 1
    result = chunks[0]
    assert "<b>Push Day A</b>" in result
    assert "• Bench: 60kg x 8" in result
    assert "Volume &gt; 2000kg today." in result


def test_caption_limit_is_1024():
    assert CAPTION_LIMIT == 1024
    long_text = "x" * 1500
    chunks = chunk_caption(long_text)
    assert len(chunks) == 2
    assert all(len(c) <= CAPTION_LIMIT for c in chunks)


def test_message_limit_is_4096():
    assert MESSAGE_LIMIT == 4096
