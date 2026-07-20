from __future__ import annotations

import json
import re
from typing import Any

from .base import BaseCollector, HotItem, clean_html_text


class ZhihuCollector(BaseCollector):
    source_name = "zhihu"

    def collect(self) -> list[dict[str, Any]]:
        self.session.headers.update(
            {
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://www.zhihu.com/billboard",
                "Origin": "https://www.zhihu.com",
            }
        )

        try:
            payload = self.fetch_json()
            items = self._parse_api_payload(payload)
            if items:
                return [item.to_dict() for item in items[: self.max_items]]
        except Exception:
            if not self.fallback_url:
                raise

        html = self.fetch_text(self.fallback_url)
        items = self._parse_next_data(html)
        if not items:
            items = self._parse_links(html)
        return [item.to_dict() for item in items[: self.max_items]]

    def _parse_api_payload(self, payload: dict[str, Any]) -> list[HotItem]:
        raw_items = payload.get("data", [])
        items: list[HotItem] = []

        for raw_item in raw_items:
            target = raw_item.get("target", {})
            title = (
                target.get("title")
                or target.get("question", {}).get("title")
                or raw_item.get("target", {}).get("title_area", {}).get("text")
                or ""
            )
            url = target.get("url") or target.get("link", {}).get("url") or raw_item.get("url", "")
            if url.startswith("https://api.zhihu.com/questions/"):
                question_id = url.rstrip("/").split("/")[-1]
                url = f"https://www.zhihu.com/question/{question_id}"
            summary = target.get("excerpt") or target.get("excerpt_area", {}).get("text") or ""
            score = raw_item.get("detail_text") or target.get("metrics_area", {}).get("text")

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

    def _parse_next_data(self, html: str) -> list[HotItem]:
        match = re.search(
            r'<script id="js-initialData" type="text/json">(.*?)</script>',
            html,
            flags=re.S,
        )
        if not match:
            return []

        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            return []

        raw_items = (
            payload.get("initialState", {})
            .get("topstory", {})
            .get("hotList", [])
        )

        items: list[HotItem] = []
        for raw_item in raw_items:
            target = raw_item.get("target", {})
            title = target.get("titleArea", {}).get("text") or target.get("title", "")
            url = target.get("link", {}).get("url") or target.get("url", "")
            summary = target.get("excerptArea", {}).get("text") or target.get("excerpt", "")
            score = target.get("metricsArea", {}).get("text")
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

    def _parse_links(self, html: str) -> list[HotItem]:
        pattern = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
        seen: set[str] = set()
        items: list[HotItem] = []
        for url, title_html in pattern.findall(html):
            title = clean_html_text(title_html)
            if len(title) < 8 or title in seen:
                continue
            seen.add(title)
            items.append(HotItem(title=title, url=url, source=self.source_name))
        return items
