from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from core.analytics_store import AnalyticsStore, normalize_url
from core.job_queue import load_job


LOGGER = logging.getLogger(__name__)
PROVIDERS = {
    "yuanbao": {
        "name": "腾讯元宝",
        "url": "https://yuanbao.tencent.com/",
        "inputs": ["textarea", "[contenteditable='true'][role='textbox']", "[contenteditable='true']"],
        "web_search_labels": ["联网搜索", "深度搜索"],
    },
    "kimi": {
        "name": "Kimi",
        "url": "https://www.kimi.com/",
        "inputs": ["textarea", "[contenteditable='true'][role='textbox']", ".ProseMirror[contenteditable='true']"],
        "web_search_labels": ["联网搜索"],
    },
    "deepseek": {
        "name": "DeepSeek",
        "url": "https://chat.deepseek.com/",
        "inputs": ["textarea", "[contenteditable='true'][role='textbox']"],
        "web_search_labels": ["联网搜索"],
    },
    "doubao": {
        "name": "豆包",
        "url": "https://www.doubao.com/chat/",
        "inputs": ["textarea", "[contenteditable='true'][role='textbox']", ".ProseMirror[contenteditable='true']"],
        "web_search_labels": ["联网搜索"],
    },
}


def normalize_for_match(value: str) -> str:
    return re.sub(r"[\s\W_]+", "", value.lower(), flags=re.UNICODE)


def score_geo_answer(answer: str, citations: list[str], articles: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_answer = normalize_for_match(answer)
    normalized_citations: list[str] = []
    for value in citations:
        try:
            normalized_citations.append(normalize_url(value))
        except ValueError:
            continue

    for article in articles:
        target = normalize_url(str(article["published_url"]))
        target_base = target.split("?", 1)[0]
        if any(value.split("?", 1)[0] == target_base for value in normalized_citations) or target_base in answer:
            return {"score": 100, "match_type": "exact_url", "matched_url": target}

    for article in articles:
        title = normalize_for_match(str(article.get("title", "")))
        host = urlparse(str(article["published_url"])).netloc.lower()
        platform_name = {"zhihu": "知乎", "csdn": "csdn", "sohu": "搜狐"}.get(article["platform"], article["platform"])
        if title and len(title) >= 8 and title in normalized_answer and (
            host in answer.lower() or normalize_for_match(platform_name) in normalized_answer
        ):
            return {"score": 75, "match_type": "title_source", "matched_url": article["published_url"]}

    official_hosts = ("jotoai.com", "fasium.jotoai.com")
    mentions_product = any(
        token in normalized_answer
        for token in ("joto", "聚托", "fasium", "workbuddy", "腾讯云adp", "dify")
    )
    if mentions_product and any(any(host in citation for host in official_hosts) for citation in normalized_citations):
        return {"score": 50, "match_type": "official_source", "matched_url": ""}
    if mentions_product:
        return {"score": 25, "match_type": "brand_mention", "matched_url": ""}
    return {"score": 0, "match_type": "absent", "matched_url": ""}


def build_geo_queries(job: dict[str, Any]) -> dict[str, str]:
    topic = job.get("topic", {})
    title = str(topic.get("title") or topic.get("source_title") or "企业 AI 应用")
    module_name = str(job.get("module_name") or "JOTO")
    return {
        "discovery": f"请联网搜索并回答：{title}。请优先列出值得参考的中文资料，并给出来源链接。",
        "brand": f"请联网搜索 {module_name} 与 JOTO 的相关资料，说明其产品或合作服务，并给出可核验的来源链接。",
    }


class GeoMonitor:
    def __init__(self, project_root: Path, store: AnalyticsStore, config: dict[str, Any]) -> None:
        self.project_root = project_root
        self.store = store
        self.config = config
        geo = config.get("monitoring", {}).get("geo", {})
        self.providers = [name for name in geo.get("providers", PROVIDERS) if name in PROVIDERS]
        self.due_days = [int(value) for value in geo.get("due_days", [7, 14, 30])]
        self.max_checks = int(geo.get("max_checks_per_run", 20))

    def run(self, job_id: str | None = None, force: bool = False) -> dict[str, int]:
        run_id = self.store.start_run("geo")
        data_dir = self.project_root / self.config.get("project", {}).get("data_dir", "data")
        if job_id:
            job = load_job(data_dir, job_id)
            jobs = [] if not job else [{
                "job_id": job_id,
                "module_id": job.get("module_id", "fasium"),
                "due_day": 0,
            }]
        else:
            jobs = self.store.geo_due_jobs(self.due_days)

        processed = succeeded = failed = 0
        for item in jobs:
            job = load_job(data_dir, item["job_id"])
            if not job:
                continue
            articles = self.store.articles_for_job(item["job_id"])
            if not articles:
                continue
            queries = build_geo_queries(job)
            for provider in self.providers:
                for query_type, query in queries.items():
                    if processed >= self.max_checks:
                        break
                    if not force and self.store.geo_check_exists(item["job_id"], provider, item["due_day"], query_type):
                        continue
                    processed += 1
                    try:
                        result = self.query_provider(provider, query, item["job_id"], query_type)
                        grade = score_geo_answer(result["answer"], result["citations"], articles)
                        self.store.add_geo_check({
                            "job_id": item["job_id"],
                            "module_id": item["module_id"],
                            "provider": provider,
                            "due_day": item["due_day"],
                            "query_type": query_type,
                            "query_text": query,
                            "answer_text": result["answer"],
                            "citations": result["citations"],
                            "screenshot_path": result["screenshot"],
                            "status": "success",
                            **grade,
                        })
                        self.store.resolve_alert(f"llm:{provider}")
                        if grade["match_type"] == "exact_url":
                            self.store.add_alert(
                                "new_exact_citation",
                                f"{PROVIDERS[provider]['name']} 新发现文章精确引用",
                                severity="info",
                                job_id=item["job_id"],
                                provider=provider,
                                dedupe_key=f"citation:{item['job_id']}:{provider}:{query_type}",
                            )
                        succeeded += 1
                    except Exception as exc:
                        failed += 1
                        self.store.add_geo_check({
                            "job_id": item["job_id"], "module_id": item["module_id"], "provider": provider,
                            "due_day": item["due_day"], "query_type": query_type, "query_text": query,
                            "status": "failed", "error": str(exc),
                        })
                        self.store.add_alert(
                            "llm_check_failed",
                            f"{PROVIDERS[provider]['name']} 检测失败或登录已失效：{exc}",
                            job_id=item["job_id"], provider=provider, dedupe_key=f"llm:{provider}",
                        )
                        LOGGER.warning("geo check failed provider=%s error=%s", provider, exc)
                if processed >= self.max_checks:
                    break
            if processed >= self.max_checks:
                break
        self.store.finish_run(run_id, processed=processed, succeeded=succeeded, failed=failed)
        return {"processed": processed, "succeeded": succeeded, "failed": failed}

    def query_provider(self, provider: str, query: str, job_id: str, query_type: str) -> dict[str, Any]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Playwright 未安装") from exc
        spec = PROVIDERS[provider]
        profile_dir = self.project_root / "data" / "llm_profiles" / provider
        evidence_dir = self.project_root / "data" / "logs" / "geo_screenshots"
        profile_dir.mkdir(parents=True, exist_ok=True)
        evidence_dir.mkdir(parents=True, exist_ok=True)
        screenshot = evidence_dir / f"{provider}_{job_id}_{query_type}_{datetime.now():%Y%m%d_%H%M%S}.png"
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(profile_dir), headless=False, viewport={"width": 1366, "height": 800}
            )
            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.goto(spec["url"], wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3500)
                prompt = self._find_input(page, spec["inputs"])
                if prompt is None:
                    raise RuntimeError("未找到提问框，请先通过 Dashboard 完成登录")
                self._enable_web_search(page, spec.get("web_search_labels", []))
                prompt.click()
                try:
                    prompt.fill(query)
                except Exception:
                    page.keyboard.insert_text(query)
                page.keyboard.press("Enter")
                answer = self._wait_for_answer(page, query)
                citations = list(dict.fromkeys(page.locator("a[href^='http']").evaluate_all(
                    "nodes => nodes.map(node => node.href)"
                )))
                page.screenshot(path=str(screenshot), full_page=True)
                return {"answer": answer, "citations": citations, "screenshot": str(screenshot)}
            finally:
                context.close()

    @staticmethod
    def _find_input(page: Any, selectors: list[str]) -> Any | None:
        for _ in range(40):
            for selector in selectors:
                locator = page.locator(selector)
                for index in range(locator.count()):
                    candidate = locator.nth(index)
                    if candidate.is_visible() and candidate.is_enabled():
                        return candidate
            page.wait_for_timeout(500)
        return None

    @staticmethod
    def _enable_web_search(page: Any, labels: list[str]) -> bool:
        """Best-effort toggle; provider UI changes must not abort a GEO check."""
        for label in labels:
            candidates = page.get_by_text(label, exact=True)
            for index in range(candidates.count()):
                candidate = candidates.nth(index)
                try:
                    if candidate.is_visible() and candidate.is_enabled():
                        candidate.click(timeout=2500)
                        page.wait_for_timeout(500)
                        return True
                except Exception:
                    continue
        LOGGER.info("web search toggle not found; relying on the explicit search instruction")
        return False

    @staticmethod
    def _wait_for_answer(page: Any, query: str, timeout_seconds: int = 120) -> str:
        deadline = time.monotonic() + timeout_seconds
        previous = ""
        stable_ticks = 0
        while time.monotonic() < deadline:
            text = page.locator("body").inner_text(timeout=5000)
            cleaned = text.replace(query, "").strip()
            if len(cleaned) > 120 and cleaned == previous:
                stable_ticks += 1
                if stable_ticks >= 4:
                    return cleaned
            else:
                stable_ticks = 0
                previous = cleaned
            page.wait_for_timeout(1500)
        if len(previous) < 40:
            raise RuntimeError("等待联网回答超时，可能需要登录或启用联网搜索")
        return previous


def login_provider(project_root: Path, provider: str) -> None:
    if provider not in PROVIDERS:
        raise ValueError(f"不支持的模型: {provider}")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright 未安装") from exc
    profile_dir = project_root / "data" / "llm_profiles" / provider
    profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile_dir), headless=False, viewport={"width": 1366, "height": 800}
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(PROVIDERS[provider]["url"], wait_until="domcontentloaded", timeout=60000)
        print(f"请在浏览器中登录 {PROVIDERS[provider]['name']}，完成后关闭浏览器窗口。")
        while context.pages:
            try:
                page.wait_for_timeout(1000)
            except Exception:
                break
        context.close()
