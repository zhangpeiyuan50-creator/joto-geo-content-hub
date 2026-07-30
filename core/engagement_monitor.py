from __future__ import annotations

import json
import logging
import os
import re
from datetime import date
from difflib import SequenceMatcher
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
    "likes": ("点赞", "赞同", "喜欢"),
    "comments": ("添加评论", "评论"),
    "favorites": ("收藏",),
    "shares": ("分享",),
    "reposts": ("转发",),
}
ZHIHU_CREATOR_ANALYTICS_URL = "https://www.zhihu.com/creator/analytics/work/article?page={page}&tab=single"
CSDN_CREATOR_ARTICLES_URL = "https://mp.csdn.net/mp_blog/manage/article"
SOHU_CREATOR_ANALYTICS_URL = "https://mp.sohu.com/mpfe/v4/data/analysis"


def browser_state_path(profile_dir: Path, platform: str) -> Path:
    state_dir = profile_dir.parent / "browser_states"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / f"{platform}.json"


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


def _scan_labeled_text(
    metrics: dict[str, Any],
    text: str,
    *,
    allow_empty_controls: bool = False,
    overwrite: bool = False,
) -> None:
    normalized = re.sub(r"\s+", " ", unescape(text))
    for metric, names in LABELS.items():
        if metrics[metric] is not None and not overwrite:
            continue
        for name in names:
            patterns = (
                rf"(?:^|[\s|·])(?:{name})\s*[:：]?\s*(\d+(?:\.\d+)?\s*[万亿千kKmM]?)(?=$|[\s|·])",
                rf"(?:^|[\s|·])(\d+(?:\.\d+)?\s*[万亿千kKmM]?)\s*(?:次|个|条)?\s*(?:{name})(?=$|[\s|·])",
            )
            for pattern in patterns:
                matches = list(re.finditer(pattern, normalized, flags=re.I))
                match = matches[-1] if matches else None
                if match:
                    metrics[metric] = parse_compact_number(match.group(1))
                    break
            if metrics[metric] is None and allow_empty_controls:
                if re.search(rf"(?:^|[\s|·])(?:{name})(?=$|[\s|·])", normalized, flags=re.I):
                    metrics[metric] = 0
            if metrics[metric] is not None:
                break


def parse_metrics_from_html(
    platform: str,
    html: str,
    rendered_text: str = "",
    rendered_controls: str = "",
) -> dict[str, Any]:
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

    attributes = " ".join(
        match.group(3)
        for match in re.finditer(r"\b(aria-label|title|data-title)\s*=\s*([\"'])(.*?)\2", html, flags=re.I | re.S)
    )
    # Do not scan the whole article body for labels. Dates such as 2026-07-24
    # can otherwise be mistaken for comment/share counts.
    _scan_labeled_text(
        metrics,
        f"{attributes} {rendered_controls}",
        allow_empty_controls=True,
        overwrite=True,
    )
    if rendered_text:
        compact_lines = " | ".join(
            line.strip()
            for line in rendered_text.splitlines()
            if line.strip() and len(line.strip()) <= 32
        )
        _scan_labeled_text(metrics, compact_lines)
    metrics["raw"] = {"parser": "public_html", "available": [name for name in METRICS if metrics[name] is not None]}
    return metrics


def _normalized_title(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", value.lower())


def _title_similarity(target: str, candidate: str) -> float:
    target_key = _normalized_title(target)
    candidate_key = _normalized_title(candidate)
    if not target_key or not candidate_key:
        return 0.0
    if target_key in candidate_key or candidate_key in target_key:
        return min(len(target_key), len(candidate_key)) / max(len(target_key), len(candidate_key))
    return SequenceMatcher(None, target_key, candidate_key).ratio()


def resolve_platform_article_title(
    article: dict[str, Any],
    config: dict[str, Any],
    project_root: Path | None = None,
) -> str:
    """Read the platform-specific title from the generated content package."""
    fallback = str(article.get("title", "")).strip()
    platform = str(article.get("platform", "")).strip().lower()
    module_id = str(article.get("module_id", "")).strip()
    raw_job_id = str(article.get("job_id", "")).strip()
    if not platform or not raw_job_id:
        return fallback

    root = (project_root or Path.cwd()).resolve()
    output_dir = Path(config.get("project", {}).get("output_dir", "outputs"))
    output_root = output_dir if output_dir.is_absolute() else root / output_dir
    job_names = [raw_job_id]
    if raw_job_id.startswith("job_"):
        job_names.append(raw_job_id.removeprefix("job_"))
    else:
        job_names.append(f"job_{raw_job_id}")

    filenames = {
        "zhihu": ("zhihu/zhihu_rich.html", "zhihu_rich.html", "zhihu.md"),
        "csdn": ("csdn/csdn.md", "csdn.md"),
        "sohu": ("sohu/sohu_rich.html", "sohu_rich.html", "sohu.md"),
    }.get(platform, ())
    package_roots: list[Path] = []
    for job_name in dict.fromkeys(job_names):
        if module_id:
            package_roots.append(output_root / module_id / job_name)
        package_roots.append(output_root / job_name)

    for package_root in package_roots:
        for filename in filenames:
            content_path = package_root / filename
            if not content_path.is_file():
                continue
            try:
                content = content_path.read_text(encoding="utf-8")
            except OSError:
                continue
            heading = re.search(r"<h1\b[^>]*>(.*?)</h1>", content, flags=re.I | re.S)
            if heading:
                title = re.sub(r"<[^>]+>", "", unescape(heading.group(1)))
                title = re.sub(r"\s+", " ", title).strip()
                if title:
                    return title[:200]
            for line in content.splitlines():
                markdown_title = re.match(r"^\s*#\s+(.+?)\s*$", line)
                if markdown_title:
                    return markdown_title.group(1).strip()[:200]
    return fallback


def parse_zhihu_creator_rows(
    rows: list[dict[str, Any]],
    target_title: str,
    target_url: str = "",
    target_published_at: str = "",
) -> dict[str, Any] | None:
    """Match one Zhihu creator-center row and extract only that row's metrics."""
    del target_published_at
    best: tuple[float, dict[str, Any], str] | None = None
    article_id_match = re.search(r"/(?:p|article)/(\d+)(?:\D|$)", target_url)
    target_article_id = article_id_match.group(1) if article_id_match else ""
    for row in rows:
        cells = [str(value).strip() for value in row.get("cells", []) if str(value).strip()]
        text = str(row.get("text", "")).strip()
        links = [
            str(link.get("href", "") if isinstance(link, dict) else link)
            for link in row.get("links", [])
        ]
        candidates: list[str] = []
        for cell in cells or [text]:
            for line in cell.splitlines():
                line = line.strip()
                if (
                    len(_normalized_title(line)) >= 4
                    and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", line)
                    and "详细分析" not in line
                    and not re.search(r"(赞同|评论|喜欢|收藏|分享|阅读量)", line)
                ):
                    candidates.append(line)
        if target_article_id and any(target_article_id in link for link in links):
            matched_title = candidates[0] if candidates else text.splitlines()[0].strip()
            best = (2.0, row, matched_title)
            break
        for candidate in candidates:
            score = _title_similarity(target_title, candidate)
            if best is None or score > best[0]:
                best = (score, row, candidate)

    if best is None or best[0] < 0.52:
        return None

    _, row, matched_title = best
    cells = [str(value).strip() for value in row.get("cells", []) if str(value).strip()]
    row_text = str(row.get("text", "")).strip()
    interaction_text = " | ".join(cells) if cells else row_text
    metrics: dict[str, Any] = {name: None for name in METRICS}

    interaction_patterns = {
        "likes": r"(\d+(?:\.\d+)?\s*[万亿千kKmM]?)\s*(?:赞同|点赞)",
        "comments": r"(\d+(?:\.\d+)?\s*[万亿千kKmM]?)\s*(?:条)?评论",
        "favorites": r"(\d+(?:\.\d+)?\s*[万亿千kKmM]?)\s*(?:次)?收藏",
        "shares": r"(\d+(?:\.\d+)?\s*[万亿千kKmM]?)\s*(?:次)?分享",
    }
    for metric, pattern in interaction_patterns.items():
        match = re.search(pattern, interaction_text, flags=re.I)
        if match:
            metrics[metric] = parse_compact_number(match.group(1))

    labelled_views = re.search(
        r"(?:阅读量|阅读)\s*[:：]?\s*(\d+(?:\.\d+)?\s*[万亿千kKmM]?)",
        interaction_text,
        flags=re.I,
    )
    if labelled_views:
        metrics["views"] = parse_compact_number(labelled_views.group(1))
    else:
        # The creator-center table puts the read count in its own cell after
        # the title/date cell. Only accept an entirely numeric cell so dates
        # and numbers inside titles cannot become engagement data.
        for cell in cells[1:]:
            if re.fullmatch(r"\d+(?:\.\d+)?\s*[万亿千kKmM]?", cell):
                metrics["views"] = parse_compact_number(cell)
                break

    metrics["raw"] = {
        "parser": "zhihu_creator_analytics",
        "matched_title": matched_title,
        "match_score": round(min(best[0], 1.0), 4),
        "match_method": "article_id" if best[0] > 1 else "title",
        "article_id": target_article_id or None,
        "available": [name for name in METRICS if metrics[name] is not None],
    }
    return metrics


def _article_id_candidates(value: str) -> set[str]:
    return set(re.findall(r"(?<!\d)\d{6,}(?!\d)", value or ""))


def _match_creator_row(
    rows: list[dict[str, Any]],
    target_title: str,
    target_url: str,
    target_published_at: str = "",
) -> tuple[dict[str, Any], str, str, float] | None:
    target_ids = _article_id_candidates(target_url)
    best: tuple[float, dict[str, Any], str, str] | None = None
    for row in rows:
        title = str(row.get("title", "")).strip()
        identity_text = " ".join(
            [
                *(str(link) for link in row.get("links", [])),
                str(row.get("identity", "")),
            ]
        )
        if target_ids and target_ids.intersection(_article_id_candidates(identity_text)):
            return row, title, "article_id", 1.0
        score = _title_similarity(target_title, title)
        if best is None or score > best[0]:
            best = (score, row, title, "title")
    if best is None or best[0] < 0.52:
        target_date = str(target_published_at)[:10]
        same_date = [row for row in rows if str(row.get("published_date", "")) == target_date]
        if target_date and len(same_date) == 1:
            row = same_date[0]
            return row, str(row.get("title", "")).strip(), "published_date", 1.0
        return None
    return best[1], best[2], best[3], best[0]


def parse_csdn_creator_rows(
    rows: list[dict[str, Any]],
    target_title: str,
    target_url: str = "",
    target_published_at: str = "",
) -> dict[str, Any] | None:
    matched = _match_creator_row(rows, target_title, target_url, target_published_at)
    if not matched:
        return None
    row, matched_title, method, score = matched
    values = list(row.get("values", []))
    metrics: dict[str, Any] = {name: None for name in METRICS}
    for index, metric in enumerate(("views", "likes", "comments", "favorites")):
        if index < len(values):
            metrics[metric] = parse_compact_number(values[index])
    metrics["raw"] = {
        "parser": "csdn_creator_articles",
        "matched_title": matched_title,
        "match_method": method,
        "match_score": round(score, 4),
        "available": [name for name in METRICS if metrics[name] is not None],
    }
    return metrics


def parse_sohu_creator_rows(
    rows: list[dict[str, Any]],
    target_title: str,
    target_url: str = "",
    target_published_at: str = "",
) -> dict[str, Any] | None:
    matched = _match_creator_row(rows, target_title, target_url, target_published_at)
    if not matched:
        return None
    row, matched_title, method, score = matched
    values = list(row.get("values", []))
    metrics: dict[str, Any] = {name: None for name in METRICS}
    # Sohu columns: reads, visits/plays, likes, comments, shares, votes.
    column_map = {"views": 0, "likes": 2, "comments": 3, "shares": 4}
    for metric, index in column_map.items():
        if index < len(values):
            metrics[metric] = parse_compact_number(values[index])
    metrics["raw"] = {
        "parser": "sohu_creator_analytics",
        "matched_title": matched_title,
        "match_method": method,
        "match_score": round(score, 4),
        "visits_or_plays": parse_compact_number(values[1]) if len(values) > 1 else None,
        "votes": parse_compact_number(values[5]) if len(values) > 5 else None,
        "available": [name for name in METRICS if metrics[name] is not None],
    }
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

    def _headers_for(self, platform: str) -> dict[str, str]:
        headers = dict(self.headers)
        cookie = os.getenv(f"{platform.upper()}_COOKIE", "").strip()
        if cookie:
            headers["Cookie"] = cookie
        return headers

    @staticmethod
    def _cookie_domain(platform: str) -> str:
        return {
            "zhihu": ".zhihu.com",
            "csdn": ".csdn.net",
            "sohu": ".sohu.com",
        }.get(platform, "")

    def _browser_cookies(self, platform: str) -> list[dict[str, Any]]:
        raw = os.getenv(f"{platform.upper()}_COOKIE", "").strip()
        domain = self._cookie_domain(platform)
        if not raw or not domain:
            return []
        cookies: list[dict[str, Any]] = []
        for part in raw.split(";"):
            if "=" not in part:
                continue
            name, value = part.strip().split("=", 1)
            if name:
                cookies.append({"name": name, "value": value, "domain": domain, "path": "/"})
        return cookies

    def run(self, job_id: str | None = None, force: bool = False) -> dict[str, int]:
        run_id = self.store.start_run("engagement")
        articles = self.store.active_articles()
        if job_id:
            articles = [article for article in articles if article["job_id"] == job_id]
        if not force:
            articles = [article for article in articles if self.store.article_due_for_engagement(article)]
        succeeded = 0
        failed = 0
        grouped: dict[str, list[dict[str, Any]]] = {}
        for article in articles:
            grouped.setdefault(str(article["platform"]), []).append(article)

        for platform, platform_articles in grouped.items():
            try:
                for article in platform_articles:
                    article["published_url"] = validate_platform_url(platform, article["published_url"])
                results = self._collect_creator_center_batch(platform, platform_articles)
            except Exception as exc:
                results = {int(article["id"]): exc for article in platform_articles}

            for article in platform_articles:
                result = results.get(int(article["id"]), RuntimeError("创作中心未返回该文章数据"))
                if isinstance(result, Exception):
                    failed += 1
                    self.store.add_engagement_snapshot(article["id"], {}, "stale", str(result))
                    self.store.add_alert(
                        "engagement_failed",
                        f"{platform} 创作中心采集失败：{result}",
                        job_id=article["job_id"],
                        provider=platform,
                        dedupe_key=f"engagement:{article['id']}",
                    )
                    LOGGER.warning("creator engagement failed platform=%s error=%s", platform, result)
                    continue
                if not any(result.get(name) is not None for name in METRICS):
                    failed += 1
                    error = RuntimeError("创作中心已找到文章，但暂未识别到热度指标")
                    self.store.add_engagement_snapshot(article["id"], {}, "stale", str(error))
                    continue
                matched_title = str(result.get("raw", {}).get("matched_title", "")).strip()
                if matched_title:
                    self.store.update_article_title(int(article["id"]), matched_title)
                self.store.add_engagement_snapshot(article["id"], result, "success")
                self.store.resolve_alert(f"engagement:{article['id']}")
                succeeded += 1
                LOGGER.info("creator engagement collected platform=%s article_id=%s", platform, article["id"])
        self.store.finish_run(run_id, processed=len(articles), succeeded=succeeded, failed=failed)
        return {"processed": len(articles), "succeeded": succeeded, "failed": failed}

    def _collect_creator_center_batch(
        self,
        platform: str,
        articles: list[dict[str, Any]],
    ) -> dict[int, dict[str, Any] | Exception]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("创作中心采集需要 Playwright，请先安装浏览器依赖") from exc

        profile_dir = Path(self.config.get("browser", {}).get("user_data_dir", "data/browser_profile")).resolve()
        profile_dir.mkdir(parents=True, exist_ok=True)
        state_path = browser_state_path(profile_dir, platform)
        monitor_config = self.config.get("monitoring", {}).get("engagement", {})
        headless = bool(monitor_config.get("headless", True))

        with sync_playwright() as playwright:
            browser = None
            # Sohu keeps part of its creator-center session outside the cookie/
            # localStorage data exported by Playwright storage_state. Reuse the
            # persistent project profile for Sohu; Zhihu and CSDN can use the
            # lighter per-platform state files.
            persistent = platform == "sohu" or not state_path.exists()
            if persistent:
                context = playwright.chromium.launch_persistent_context(
                    str(profile_dir), headless=headless, viewport={"width": 1440, "height": 900}
                )
            else:
                browser = playwright.chromium.launch(headless=headless)
                context = browser.new_context(storage_state=str(state_path), viewport={"width": 1440, "height": 900})
            page = context.pages[0] if context.pages else context.new_page()
            try:
                rows = self._creator_rows(page, platform, articles)
                context.storage_state(path=str(state_path))
                results: dict[int, dict[str, Any] | Exception] = {}
                parser = {
                    "zhihu": parse_zhihu_creator_rows,
                    "csdn": parse_csdn_creator_rows,
                    "sohu": parse_sohu_creator_rows,
                }.get(platform)
                if parser is None:
                    raise RuntimeError(f"暂不支持 {platform} 创作中心采集")
                for article in articles:
                    title = resolve_platform_article_title(article, self.config)
                    metrics = parser(
                        rows,
                        title,
                        article.get("published_url", ""),
                        article.get("published_at", ""),
                    )
                    if metrics is None:
                        results[int(article["id"])] = RuntimeError(
                            f"创作中心未找到登记文章：{title[:60]}"
                        )
                    else:
                        metrics.setdefault("raw", {})["creator_url"] = page.url
                        results[int(article["id"])] = metrics
                return results
            finally:
                if persistent:
                    try:
                        context.storage_state(path=str(state_path))
                    except Exception:
                        pass
                context.close()
                if browser is not None:
                    browser.close()

    def _creator_rows(
        self,
        page: Any,
        platform: str,
        articles: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if platform == "zhihu":
            return self._zhihu_creator_rows(page)
        if platform == "csdn":
            return self._csdn_creator_rows(page)
        if platform == "sohu":
            return self._sohu_creator_rows(page, articles)
        raise RuntimeError(f"暂不支持 {platform} 创作中心采集")

    def _creator_page_limit(self, platform: str) -> int:
        engagement = self.config.get("monitoring", {}).get("engagement", {})
        creator_pages = engagement.get("creator_pages", {})
        legacy = engagement.get("zhihu_creator_pages", 5)
        return max(1, int(creator_pages.get(platform, legacy if platform == "zhihu" else 10)))

    @staticmethod
    def _extract_table_rows(page: Any) -> list[dict[str, Any]]:
        return page.evaluate(
            """() => Array.from(document.querySelectorAll('table tbody tr, [role="row"]')).map(row => ({
                text: (row.innerText || '').trim(),
                cells: Array.from(row.querySelectorAll(':scope > td, :scope > [role="cell"], td, [role="cell"]'))
                    .map(cell => (cell.innerText || '').trim()).filter(Boolean),
                links: Array.from(row.querySelectorAll('a')).map(link => link.href || ''),
                identity: (row.outerHTML || '').slice(0, 12000)
            })).filter(row => row.text)"""
        )

    def _zhihu_creator_rows(self, page: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for page_number in range(1, self._creator_page_limit("zhihu") + 1):
            page.goto(ZHIHU_CREATOR_ANALYTICS_URL.format(page=page_number), wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3500)
            body = page.locator("body").inner_text(timeout=5000)
            if "40362" in body or "暂时限制本次访问" in body:
                raise RuntimeError("知乎创作中心触发访问限制，请重新登录知乎监测后再试")
            if "signin" in page.url:
                raise RuntimeError("知乎监测登录已失效，请重新登录知乎监测")
            page_rows = self._extract_table_rows(page)
            rows.extend(page_rows)
            if not page_rows:
                break
        return rows

    def _csdn_creator_rows(self, page: Any) -> list[dict[str, Any]]:
        page.goto(CSDN_CREATOR_ARTICLES_URL, wait_until="domcontentloaded", timeout=60000)
        if "passport.csdn.net" in page.url:
            raise RuntimeError("CSDN 监测登录已失效，请先登录 CSDN 监测")
        try:
            page.locator(".article-list-item-mp").first.wait_for(state="visible", timeout=25000)
        except Exception as exc:
            body = page.locator("body").inner_text(timeout=5000)
            if "登录" in body and "内容管理" not in body:
                raise RuntimeError("CSDN 监测登录已失效，请先登录 CSDN 监测") from exc
            raise RuntimeError("CSDN 内容管理页加载超时，请稍后重新刷新热度") from exc
        rows: list[dict[str, Any]] = []
        for _ in range(self._creator_page_limit("csdn")):
            rows.extend(page.locator(".article-list-item-mp").evaluate_all(
                r"""items => items.map(item => {
                    const titleLink = item.querySelector('.article-list-item-txt a');
                    const lines = (item.innerText || '').split(/\n+/).map(v => v.trim()).filter(Boolean);
                    const dateIndex = lines.findIndex(v => /^\d{4}-\d{2}-\d{2}/.test(v));
                    const values = dateIndex >= 0
                        ? lines.slice(dateIndex + 1).filter(v => /^\d+(?:\.\d+)?(?:万|千|[kKmM])?$/.test(v)).slice(0, 4)
                        : [];
                    return {
                        title: (titleLink?.innerText || '').trim(),
                        published_date: dateIndex >= 0 ? lines[dateIndex].slice(0, 10) : '',
                        values,
                        links: Array.from(item.querySelectorAll('a')).map(a => a.href || ''),
                        text: (item.innerText || '').trim()
                    };
                })"""
            ))
            next_button = page.locator("button.btn-next:not([disabled]), .el-pagination .btn-next:not([disabled])")
            if next_button.count() == 0:
                break
            next_button.first.click()
            page.wait_for_timeout(2500)
        return rows

    def _sohu_creator_rows(self, page: Any, articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        page.goto(SOHU_CREATOR_ANALYTICS_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4500)
        if "login" in page.url.lower():
            raise RuntimeError("搜狐号监测登录已失效，请先登录搜狐号监测")
        single = page.get_by_text("单篇", exact=True)
        if single.count():
            single.first.click()
            page.wait_for_timeout(4500)
        published_dates = [str(article.get("published_at", ""))[:10] for article in articles]
        published_dates = [value for value in published_dates if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)]
        date_inputs = page.locator("input.el-range-input")
        if published_dates and date_inputs.count() >= 2:
            start_input = date_inputs.nth(date_inputs.count() - 2)
            end_input = date_inputs.nth(date_inputs.count() - 1)
            start_input.fill(min(published_dates))
            end_input.fill(date.today().isoformat())
            end_input.press("Enter")
            page.wait_for_timeout(4500)
        rows: list[dict[str, Any]] = []
        for _ in range(self._creator_page_limit("sohu")):
            for row in self._extract_table_rows(page):
                cells = row.get("cells", [])
                if len(cells) < 7 or "作品名称" in str(cells[0]):
                    continue
                title = str(cells[0]).splitlines()[0].strip()
                row["title"] = title
                published_match = re.search(r"\d{4}-\d{2}-\d{2}", str(cells[0]))
                row["published_date"] = published_match.group(0) if published_match else ""
                row["values"] = [str(value).strip() for value in cells[1:7]]
                rows.append(row)
            next_button = page.locator("button.btn-next:not([disabled]), .el-pagination .btn-next:not([disabled])")
            if next_button.count() == 0:
                break
            next_button.first.click()
            page.wait_for_timeout(2500)
        if not rows and "暂无数据" not in page.locator("body").inner_text():
            raise RuntimeError("搜狐号内容分析页未加载文章数据，请重新登录搜狐号监测")
        return rows

    def _collect_metrics(self, article: dict[str, Any]) -> dict[str, Any]:
        if article["platform"] == "zhihu":
            return self._collect_with_browser(article)

        request_error = ""
        try:
            response = requests.get(
                article["published_url"],
                headers=self._headers_for(article["platform"]),
                timeout=self.timeout,
                allow_redirects=True,
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
            browser = None
            if article["platform"] == "zhihu":
                state_path = browser_state_path(profile_dir, "zhihu")
                if not state_path.exists():
                    raise RuntimeError(
                        "尚未保存知乎监测登录状态；请先点击‘登录知乎监测’，登录成功后等待几秒再刷新"
                    )
                browser = playwright.chromium.launch(headless=headless)
                context = browser.new_context(
                    storage_state=str(state_path),
                    viewport={"width": 1366, "height": 800},
                )
            else:
                context = playwright.chromium.launch_persistent_context(
                    str(profile_dir), headless=headless, viewport={"width": 1366, "height": 800}
                )
            cookies = self._browser_cookies(article["platform"])
            existing = context.cookies([article["published_url"]])
            if cookies and not existing:
                context.add_cookies(cookies)
            page = context.pages[0] if context.pages else context.new_page()
            try:
                if article["platform"] == "zhihu":
                    return self._collect_zhihu_creator_metrics(page, article)
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
                if article["platform"] == "zhihu" and (
                    '"code":40362' in rendered_text.replace(" ", "")
                    or "暂时限制本次访问" in rendered_text
                ):
                    raise RuntimeError(
                        "知乎返回 40362 风控限制；请更新 ZHIHU_COOKIE，或先在监测浏览器中登录知乎后重试"
                    )
                if article["platform"] == "csdn":
                    action_counts = page.evaluate(
                        """() => {
                            const text = selector => (document.querySelector(selector)?.textContent || '').trim();
                            return {
                                likes: text('#toolBarBox #blog-digg-num') || text('#toolBarBox #is-like'),
                                favorites: text('#toolBarBox #get-collection'),
                                comments: text('#toolBarBox .tool-item-comment')
                            };
                        }"""
                    )
                    labelled_controls = " | ".join(
                        [
                            f"点赞 {action_counts.get('likes', '')}",
                            f"收藏 {action_counts.get('favorites', '')}",
                            f"评论 {action_counts.get('comments', '')}",
                            "分享",
                        ]
                    )
                else:
                    labelled_controls = page.locator("body *").evaluate_all(
                    """(nodes, platform) => nodes.filter(node => {
                        if (platform !== 'csdn') return true;
                        return !node.closest([
                            'aside', '.blog_container_aside', '.aside-box',
                            '.user-info', '.blog-user', '.blog-user-info',
                            '.aside-profile', '.profile-intro-name-box'
                        ].join(','));
                    }).map(node => [
                        (node.innerText || '').trim(),
                        node.getAttribute('aria-label') || '',
                        node.getAttribute('title') || ''
                    ].join(' ').trim()).filter(text =>
                        text.length <= 32 &&
                        /(阅读|浏览|点赞|赞同|喜欢|添加评论|评论|收藏|分享|转发)/.test(text)
                    ).join(' | ')""",
                        article["platform"],
                    )
                metrics = parse_metrics_from_html(
                    article["platform"],
                    page.content(),
                    rendered_text,
                    labelled_controls,
                )
                metrics["raw"] = {
                    **metrics.get("raw", {}),
                    "parser": "playwright",
                    "page_url": page.url,
                }
                return metrics
            finally:
                context.close()
                if browser is not None:
                    browser.close()

    def _collect_zhihu_creator_metrics(self, page: Any, article: dict[str, Any]) -> dict[str, Any]:
        page_limit = int(
            self.config.get("monitoring", {})
            .get("engagement", {})
            .get("zhihu_creator_pages", 5)
        )
        last_page_text = ""
        target_title = resolve_platform_article_title(article, self.config)
        LOGGER.info(
            "zhihu creator matching job_id=%s package_title=%r registered_title=%r",
            article.get("job_id"),
            target_title,
            article.get("title"),
        )
        for page_number in range(1, max(1, page_limit) + 1):
            url = ZHIHU_CREATOR_ANALYTICS_URL.format(page=page_number)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception as exc:
                LOGGER.info("zhihu creator navigation incomplete page=%s error=%s", page_number, exc)
            page.wait_for_timeout(3500)
            last_page_text = page.locator("body").inner_text(timeout=5000)
            compact_text = last_page_text.replace(" ", "")
            if '"code":40362' in compact_text or "暂时限制本次访问" in last_page_text:
                raise RuntimeError(
                    "知乎创作中心返回 40362；请点击“登录知乎监测”，在专用监测浏览器登录后重试"
                )
            if "signin" in page.url or ("登录" in last_page_text and "创作中心" not in last_page_text):
                raise RuntimeError(
                    "知乎监测浏览器尚未登录；请点击“登录知乎监测”完成登录后重试"
                )

            rows = page.evaluate(
                """() => {
                    const seen = new Set();
                    const result = [];
                    const add = node => {
                        if (!node || seen.has(node)) return;
                        seen.add(node);
                        const cells = Array.from(
                            node.querySelectorAll(':scope > td, :scope > [role="cell"], td, [role="cell"]')
                        ).map(cell => (cell.innerText || '').trim()).filter(Boolean);
                        const text = (node.innerText || '').trim();
                        const links = Array.from(node.querySelectorAll('a')).map(link => ({
                            text: (link.innerText || '').trim(),
                            href: link.href || ''
                        }));
                        if (text) result.push({ text, cells, links });
                    };
                    document.querySelectorAll('table tbody tr, [role="row"]').forEach(add);
                    document.querySelectorAll('a').forEach(link => {
                        if ((link.innerText || '').includes('详细分析')) {
                            let node = link.closest('tr, [role="row"]');
                            if (!node) {
                                node = link.parentElement;
                                for (let i = 0; node && i < 4 && !(node.innerText || '').match(/赞同|阅读量/); i++) {
                                    node = node.parentElement;
                                }
                            }
                            add(node);
                        }
                    });
                    return result;
                }"""
            )
            metrics = parse_zhihu_creator_rows(rows, target_title, article.get("published_url", ""))
            if metrics:
                metrics["raw"] = {
                    **metrics.get("raw", {}),
                    "page_url": page.url,
                    "creator_page": page_number,
                    "target_title": target_title,
                }
                return metrics

        if "内容分析" not in last_page_text:
            raise RuntimeError(
                "未进入知乎创作中心内容分析页；请点击“登录知乎监测”确认账号登录状态"
            )
        raise RuntimeError(
            f"知乎创作中心前 {page_limit} 页未找到已登记文章，请确认登记标题和当前登录账号"
        )


def analytics_path(project_root: Path, config: dict[str, Any]) -> Path:
    data_dir = project_root / config.get("project", {}).get("data_dir", "data")
    return data_dir / "analytics.db"


def login_engagement_platform(project_root: Path, config: dict[str, Any], platform: str) -> None:
    login_urls = {
        "zhihu": ZHIHU_CREATOR_ANALYTICS_URL.format(page=1),
        "csdn": CSDN_CREATOR_ARTICLES_URL,
        "sohu": SOHU_CREATOR_ANALYTICS_URL,
    }
    if platform not in login_urls:
        raise ValueError(f"不支持的平台: {platform}")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright 未安装") from exc
    profile_dir = project_root / config.get("browser", {}).get("user_data_dir", "data/browser_profile")
    profile_dir.mkdir(parents=True, exist_ok=True)
    state_path = browser_state_path(profile_dir.resolve(), platform)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile_dir.resolve()), headless=False, viewport={"width": 1366, "height": 800}
        )
        page = context.pages[0] if context.pages else context.new_page()
        context.storage_state(path=str(state_path))
        try:
            page.goto(login_urls[platform], wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:
            LOGGER.info("platform login navigation incomplete platform=%s error=%s", platform, exc)
        print(f"请在浏览器中登录 {platform}；登录状态会自动保存，完成后可保留或关闭窗口。")
        while context.pages:
            try:
                context.storage_state(path=str(state_path))
                context.pages[-1].wait_for_timeout(1000)
            except Exception:
                break
        try:
            context.storage_state(path=str(state_path))
        except Exception:
            pass
        try:
            context.close()
        except Exception:
            pass
