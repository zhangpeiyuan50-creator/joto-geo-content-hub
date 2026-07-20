from __future__ import annotations

from typing import Any

from .base import BaseCollector, HotItem, clean_html_text


class JuejinCollector(BaseCollector):
    source_name = "juejin"

    def collect(self) -> list[dict[str, Any]]:
        self.session.headers.update(
            {
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://juejin.cn/",
                "Origin": "https://juejin.cn",
                "X-Agent": "Juejin/Web",
            }
        )
        payload = self.post_json(
            {
                "id_type": 2,
                "client_type": 2608,
                "sort_type": 200,
                "cursor": "0",
                "limit": self.max_items,
            }
        )
        raw_items = payload.get("data", [])

        items: list[HotItem] = []
        for raw_item in raw_items:
            article_info = raw_item.get("item_info", {}).get("article_info", {})
            if not article_info:
                article_info = raw_item.get("content", {}).get("content_info", {})
            if not article_info:
                article_info = raw_item.get("article_info", {})

            title = article_info.get("title", "")
            article_id = article_info.get("article_id", "")
            summary = article_info.get("brief_content", "")
            score = raw_item.get("score") or article_info.get("view_count")
            url = f"https://juejin.cn/post/{article_id}" if article_id else ""

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

        return [item.to_dict() for item in items[: self.max_items]]
