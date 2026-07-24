from __future__ import annotations

import json
import logging
import re
from html import unescape
from pathlib import Path
from typing import Any, Iterable

import requests

from core.analytics_store import AnalyticsStore, validate_platform_url


LOGGER = logging.getLogger(__name__)
METRICS = ("views", "likes", "comments", "favorites", "shares", "reposts")
ALIASES = {
    "views": {"viewcount", "views", "readcount", "readnum", "viewnum", "pv", "浏览量", "阅读量"},
    "likes": {"likecount", "likes", "likenum", "votecount", "voteupcount", "upvotecount", "diggcount", "点赞", "赞同"},
    "comments": {"commentcount", "comments", "commentnum", "评论", "评论数"},
    "favorites": {"favoritecount", "favorites", "favlistscount", "collectcount", "collectnum", "collectioncount", "收藏"},
    "shares": {"sharecount", "shares", "sharenum", "分享", "分享数"},
    "reposts": {"repostcount", "reposts", "forwardcount", "转发"},
}
LABELS = {
    "views": ("阅读量", "浏览量", "阅读", "浏览"),
    "likes": ("点赞", "赞同"),
    "comments": ("评论",),
    "favorites": ("收藏",),
    "shares": ("分享",),
    "reposts": ("转发",),
}


def parse_compact_number(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = str(value).strip().replace(",", "")
    match = re.search(r"(\d+(?:\.\d+)?)\s*([万億亿千kKmM]?)", text)
    if not match:
        return None
    multiplier = {
        "": 1,
        "千": 1_000,
        "万": 10_000,
        "億": 100_000_000,
        "亿": 100_000_000,
        "k": 1_000,
        "K": 1_000,
        "m": 1_000_000,
        "M": 1_000_000,
    }[match.group(2)]
    return int(float(match.group(1)) * multiplier)


def walk_json(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key), child
            yield from walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)


def extract_json_objects(html: str) -> list[Any]:
    objects: list[Any] = []
    patterns = (
        r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        r"<script[^>]+id=[\"']__NEXT_DATA__[\"'][^>]*>(.*?)</script>",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, html, flags=re.I | re.S):
            try:
                objects.append(json.loads(unescape(match.group(1)).strip()))
            except json.JSONDecodeError:
                continue
    for marker in ("window.__INITIAL_STATE__=", "window.__APOLLO_STATE__="):
        start = html.find(marker)
        if start < 0:
            continue
        decoder = json.JSONDecoder()
        try:
            value, _ = decoder.raw_decode(html[start + len(marker) :].lstrip())
            objects.append(value)
        except json.JSONDecodeError:
            pass
    return objects


def _scan_labeled_text(metrics: dict[str, Any], text: str) -> None:
    normalized = re.sub(r"\s+", " ", unescape(text))
    for metric, names in LABELS.items():
        if metrics[metric] is not None:
            continue
        for name in names:
            patterns = (
                rf"(?:{name})\s*[:：]?\s*(\d+(?:\.\d+)?\s*[万亿千kKmM]?)",
                rf"(\d+(?:\.\d+)?\s*[万亿千kKmM]?)\s*(?:次|个|条)?\s*(?:{name})",
            )
            for pattern in patterns:
                match = re.search(pattern, normalized, flags=re.I)
                if match:
                    metrics[metric] = parse_compact_number(match.group(1))
                    break
            if metrics[metric] is not None:
                break


def parse_metrics_from_html(platform: str, html: str, rendered_text: str = "") -> dict[str, Any]:
    del platform  # Platform-specific aliases can be added without changing the storage contract.
    metrics: dict[str, Any] = {name: None for name in METRICS}
    normalized_aliases = {
        metric: {re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", alias.lower()) for alias in aliases}
        for metric, aliases in ALIASES.items()
    }
    for obj in extract_json_objects(html):
        for key, value in walk_json(obj):
            normalized_key = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", key.lower())
            for metric in METRICS:
                if metrics[metric] is None and normalized_key in normalized_aliases[metric]:
                    metrics[metric] = parse_compact_number(value)

    plain = unescape(re.sub(r"<[^>]+>", " ", html))
    attributes = " ".join(
        match.group(3)
        for match in re.finditer(r"\b(aria-label|title|data-title)\s*=\s*([\"'])(.*?)\2", html, flags=re.I | re.S)
    )
    _scan_labeled_text(metrics, f"{plain} {attributes} {rendered_text}")
    metrics["raw"] = {"parser": "public_html", "available": [name for name in METRICS if metrics[name] is not None]}
    return metrics


class EngagementMonitor:
    def __init__(self, store: AnalyticsStore, config: dict[str, Any]) -> None:
        self.store = store
        self.config = config
        self.timeout = int(config.get("collectors", {}).get("timeout_seconds", 15))
        self.headers = {
            "User-Agent": config.get("collectors", {}).get(
                "user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
        }

    def run(self, job_id: str | None = None, force: bool = False) -> dict[str, int]:
        run_id = self.store.start_run("engagement")
        articles = self.store.active_articles()
        if job_id:
            articles = [article for article in articles if article["job_id"] == job_id]
        if not force:
            articles = [article for article in articles if self.store.article_due_for_engagement(article)]
        succeeded = 0
        failed = 0
        for article in articles:
            try:
                article["published_url"] = validate_platform_url(
                    article["platform"], article["published_url"]
                )
                metrics = self._collect_metrics(article)
                if not any(metrics.get(name) is not None for name in METRICS):
                    raise RuntimeError("文章页面可访问，但暂未识别到公开热度指标")
                self.store.add_engagement_snapshot(article["id"], metrics, "success")
                self.store.resolve_alert(f"engagement:{article['id']}")
                succeeded += 1
                LOGGER.info("engagement collected platform=%s url=%s", article["platform"], article["published_url"])
            except Exception as exc:
                failed += 1
                self.store.add_engagement_snapshot(article["id"], {}, "stale", str(exc))
                self.store.add_alert(
                    "engagement_failed",
                    f"{article['platform']} 热度采集失败：{exc}",
                    job_id=article["job_id"],
                    provider=article["platform"],
                    dedupe_key=f"engagement:{article['id']}",
                )
                LOGGER.warning("engagement failed platform=%s error=%s", article["platform"], exc)
        self.store.finish_run(run_id, processed=len(articles), succeeded=succeeded, failed=failed)
        return {"processed": len(articles), "succeeded": succeeded, "failed": failed}

    def _collect_metrics(self, article: dict[str, Any]) -> dict[str, Any]:
        request_error = ""
        try:
            response = requests.get(
                article["published_url"], headers=self.headers, timeout=self.timeout, allow_redirects=True
            )
            response.raise_for_status()
            metrics = parse_metrics_from_html(article["platform"], response.text)
            if any(metrics.get(name) is not None for name in METRICS):
                return metrics
        except requests.RequestException as exc:
            # Public article pages often block or delay plain HTTP clients. Reuse
            # the persisted browser profile before declaring collection failed.
            request_error = str(exc)
            LOGGER.info(
                "public metric request failed; falling back to browser platform=%s error=%s",
                article["platform"],
                exc,
            )

        metrics = self._collect_with_browser(article)
        metrics.setdefault("raw", {})["request_error"] = request_error
        return metrics

    def _collect_with_browser(self, article: dict[str, Any]) -> dict[str, Any]:
        try:
            from playwright.sync_api import Error as PlaywrightError, sync_playwright
        except ImportError as exc:
            raise RuntimeError("页面指标需要浏览器解析，但 Playwright 未安装") from exc
        profile_dir = Path(self.config.get("browser", {}).get("user_data_dir", "data/browser_profile")).resolve()
        profile_dir.mkdir(parents=True, exist_ok=True)
        headless = bool(self.config.get("browser", {}).get("headless", False))
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(profile_dir), headless=headless, viewport={"width": 1366, "height": 800}
            )
            page = context.pages[0] if context.pages else context.new_page()
            try:
                try:
                    page.goto(article["published_url"], wait_until="domcontentloaded", timeout=60000)
                except PlaywrightError as exc:
                    # Some content sites keep analytics or ad requests open. If the
                    # article body rendered, parse it instead of discarding the page.
                    LOGGER.info(
                        "browser navigation incomplete; parsing rendered page platform=%s error=%s",
                        article["platform"], exc,
                    )
                page.wait_for_timeout(4000)
                rendered_text = page.locator("body").inner_text(timeout=5000)
                labelled_controls = page.locator("button, [aria-label], [title]").evaluate_all(
                    """nodes => nodes.map(node => [
                        node.innerText || '',
                        node.getAttribute('aria-label') || '',
                        node.getAttribute('title') || ''
                    ].join(' ')).join(' ')"""
                )
                metrics = parse_metrics_from_html(
                    article["platform"], page.content(), f"{rendered_text} {labelled_controls}"
                )
                metrics["raw"] = {
                    **metrics.get("raw", {}),
                    "parser": "playwright",
                    "page_url": page.url,
                }
                return metrics
            finally:
                context.close()


def analytics_path(project_root: Path, config: dict[str, Any]) -> Path:
    data_dir = project_root / config.get("project", {}).get("data_dir", "data")
    return data_dir / "analytics.db"
