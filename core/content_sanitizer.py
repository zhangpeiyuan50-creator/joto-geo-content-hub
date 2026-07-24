from __future__ import annotations

import re


_URL_PATTERN = re.compile(
    r"(?i)\b(?:https?://|www\.)[a-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+"
)
_DOMAIN_PATTERN = re.compile(
    r"(?i)(?<![@\w])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"(?:com|cn|net|org|ai|io|cloud|tech)"
    r"(?:/[a-z0-9._~:/?#\[\]@!$&'()*+,;=%-]*)?"
)


def sanitize_sohu_content(content: str) -> str:
    """Remove outbound links from Sohu copy while preserving readable anchor text."""
    text = str(content or "")
    text = re.sub(r"<a\b[^>]*>(.*?)</a>", r"\1", text, flags=re.I | re.S)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<(?:(?:https?://)|(?:www\.))[^>]+>", "", text, flags=re.I)
    text = _URL_PATTERN.sub("", text)
    text = _DOMAIN_PATTERN.sub("", text)
    text = re.sub(r"[ \t]+([，。！？；：、])", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
