from __future__ import annotations

import re
import os
from dataclasses import dataclass
from html import unescape
from typing import Any

import requests


@dataclass
class HotItem:
    title: str
    url: str
    source: str
    summary: str = ""
    raw_score: str | int | float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title.strip(),
            "url": self.url,
            "source": self.source,
            "summary": self.summary.strip(),
            "raw_score": self.raw_score,
        }


class BaseCollector:
    source_name = "base"

    def __init__(
        self,
        url: str,
        timeout: int,
        user_agent: str,
        max_items: int,
        fallback_url: str | None = None,
    ) -> None:
        self.url = url
        self.fallback_url = fallback_url
        self.timeout = timeout
        self.max_items = max_items
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Connection": "keep-alive",
            }
        )
        cookie = os.getenv(f"{self.source_name.upper()}_COOKIE")
        if cookie:
            self.session.headers.update({"Cookie": cookie})

    def fetch_text(self, url: str | None = None) -> str:
        response = self.session.get(url or self.url, timeout=self.timeout)
        response.raise_for_status()
        return response.text

    def fetch_json(self, url: str | None = None) -> dict[str, Any]:
        response = self.session.get(url or self.url, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def post_json(self, payload: dict[str, Any] | None = None, url: str | None = None) -> dict[str, Any]:
        response = self.session.post(url or self.url, json=payload or {}, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def collect(self) -> list[dict[str, Any]]:
        raise NotImplementedError


def clean_html_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()
