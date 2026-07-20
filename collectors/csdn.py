from __future__ import annotations

import re
from typing import Any

from .base import BaseCollector, HotItem, clean_html_text


class CSDNCollector(BaseCollector):
    source_name = "csdn"

    def collect(self) -> list[dict[str, Any]]:
        self.session.headers.update(
            {
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://blog.csdn.net/rank/list",
                "Origin": "https://blog.csdn.net",
            }
        )

        try:
            payload = self.fetch_json()
            items = self._parse_hot_rank_api(payload)
            if items:
                return [item.to_dict() for item in items[: self.max_items]]
        except Exception:
            if not self.fallback_url:
                raise

        html = self.fetch_text(self.fallback_url)
        items = self._parse_rank_page(html)
        return [item.to_dict() for item in items[: self.max_items]]

    def _parse_hot_rank_api(self, payload: dict[str, Any]) -> list[HotItem]:
        raw_items = payload.get("data", [])
        if isinstance(raw_items, dict):
            raw_items = raw_items.get("list", [])

        items: list[HotItem] = []
        for raw_item in raw_items:
            title = raw_item.get("articleTitle") or raw_item.get("title") or ""
            url = raw_item.get("articleDetailUrl") or raw_item.get("url") or ""
            summary = raw_item.get("description") or raw_item.get("summary") or ""
            score = raw_item.get("hotRankScore") or raw_item.get("viewCount")

            if title:
                items.append(
                    HotItem(
                        title=clean_html_text(title),
                        url=url,
                        source=self.source_name,
                        summary=clean_html_text(summary),
                        raw_score=score,
                    )
                )

        return items

    def _parse_rank_page(self, html: str) -> list[HotItem]:
        link_pattern = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
        seen: set[str] = set()
        items: list[HotItem] = []

        for url, title_html in link_pattern.findall(html):
            title = clean_html_text(title_html)
            if not self._looks_like_article(url, title):
                continue
            if title in seen:
                continue

            seen.add(title)
            items.append(HotItem(title=title, url=url, source=self.source_name))

        return items

    @staticmethod
    def _looks_like_article(url: str, title: str) -> bool:
        if len(title) < 6:
            return False
        if "blog.csdn.net" not in url and "/article/details/" not in url:
            return False
        return True
