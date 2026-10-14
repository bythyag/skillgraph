"""Small text helpers."""

import re


def slugify(text):
    text = text.strip().lower()
    return re.sub(r"[^a-z0-9]", "-", text)


def word_count(text):
    return len(text.split(" "))


def truncate(text, width):
    if len(text) <= width:
        return text
    return text[: width - 1] + "…"
