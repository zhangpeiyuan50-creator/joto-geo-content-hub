from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any


class BrowserPublisher:
    platform = "base"
    editor_url = ""
    content_file = ""

    def __init__(self, package_dir: Path, config: dict[str, Any], logger: logging.Logger) -> None:
        self.package_dir = package_dir
        self.config = config
        self.logger = logger
        self.browser = None
        self.context = None
        self.page = None

    def run(self) -> None:
        package = self.read_package()
        self.current_package = package
        self.start_browser()
        self.open_editor()
        self.login_check()
        self.fill_title(package["title"])
        self.fill_content(package["content"])
        self.upload_cover(package["cover_image"])
        self.save_draft()
        self.logger.info("%s publisher ready for manual submit", self.platform)
        print("内容已经填写完成，浏览器会停留在发布页面。请检查后由你手动点击发布。")
        if self.web_publish_mode:
            print("网页发布模式：完成检查后直接关闭自动化浏览器窗口即可。")
            self.wait_until_browser_closed()
        else:
            input("检查完成后按 Enter 关闭自动化浏览器；需要继续编辑时先不要按 Enter：")

    @property
    def web_publish_mode(self) -> bool:
        return os.getenv("FASIUM_WEB_PUBLISH") == "1"

    def wait_until_browser_closed(self, timeout_seconds: int = 7200) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                if not self.context or not self.context.pages:
                    return
                self.page.wait_for_timeout(1000)
            except Exception:
                return
        self.logger.warning("publisher browser wait timed out platform=%s", self.platform)

    def read_package(self) -> dict[str, Any]:
        content_path = self.resolve_content_path()
        if not content_path.exists():
            raise FileNotFoundError(f"Content file not found: {content_path}")
        content = content_path.read_text(encoding="utf-8", errors="replace")
        if not content.strip():
            raise ValueError(f"内容文件是空的，不能发布：{content_path}")
        publish_content = extract_article_html(content)
        title = extract_title(publish_content, fallback=self.package_dir.name)
        cover_image = self.package_dir / "assets" / "cover_image.jpg"
        if not cover_image.exists():
            cover_image = self.package_dir / "cover_image.jpg"
        clean_content = strip_publish_only_sections(strip_title(publish_content))
        rich_content = strip_publish_only_sections_html(strip_title_html(publish_content))
        return {
            "title": title,
            "content": clean_content,
            "raw_content": rich_content,
            "is_html": looks_like_html(rich_content),
            "summary": build_summary(clean_content),
            "cover_image": cover_image if cover_image.exists() else None,
        }

    def resolve_content_path(self) -> Path:
        candidates = [self.content_file, Path(self.content_file).name]
        for candidate in candidates:
            path = self.package_dir / candidate
            if path.exists():
                return path
        return self.package_dir / self.content_file

    def start_browser(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is not installed. Run: pip install playwright && python -m playwright install chromium"
            ) from exc

        self.logger.info("browser launching platform=%s", self.platform)
        self._playwright = sync_playwright().start()
        headless = bool(self.config.get("browser", {}).get("headless", False))
        user_data_dir = Path(
            self.config.get("browser", {}).get("user_data_dir", "data/browser_profile")
        ).resolve()
        user_data_dir.mkdir(parents=True, exist_ok=True)
        self.context = self._playwright.chromium.launch_persistent_context(
            str(user_data_dir),
            headless=headless,
            accept_downloads=True,
            viewport={"width": 1366, "height": 760},
            args=["--start-maximized"],
        )
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()

    def open_editor(self) -> None:
        assert self.page is not None
        self.logger.info("page opening platform=%s url=%s", self.platform, self.editor_url)
        self.page.goto(self.editor_url, wait_until="domcontentloaded", timeout=60000)
        self.page.wait_for_timeout(3000)

    def login_check(self) -> None:
        assert self.page is not None
        login_required = "login" in self.page.url.lower()
        for label in ("登录", "立即登录", "账号登录"):
            locator = self.page.get_by_text(label, exact=True)
            if locator.count() and locator.first.is_visible():
                login_required = True
                break

        self.logger.info(
            "login status check platform=%s url=%s required=%s",
            self.platform,
            self.page.url,
            login_required,
        )
        if not login_required:
            return

        print(f"检测到 {self.platform} 尚未登录。请在打开的浏览器中完成登录。")
        if self.web_publish_mode:
            self.wait_for_login_completion()
        else:
            input("登录成功后回到终端，按 Enter 继续：")
        self.logger.info("login completed by user platform=%s", self.platform)
        self.page.goto(self.editor_url, wait_until="domcontentloaded", timeout=60000)
        self.page.wait_for_timeout(4000)

    def wait_for_login_completion(self, timeout_seconds: int = 300) -> None:
        assert self.page is not None
        login_labels = ("登录", "立即登录", "账号登录")
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            url_requires_login = "login" in self.page.url.lower()
            label_visible = False
            for label in login_labels:
                locator = self.page.get_by_text(label, exact=True)
                if locator.count() and locator.first.is_visible():
                    label_visible = True
                    break
            if not url_requires_login and not label_visible:
                return
            self.page.wait_for_timeout(1000)
        raise RuntimeError(f"等待 {self.platform} 登录超时，请重新启动发布辅助")

    def fill_title(self, title: str) -> None:
        raise NotImplementedError

    def fill_content(self, content: str) -> None:
        raise NotImplementedError

    def upload_cover(self, cover_path: Path | None) -> None:
        if not cover_path:
            self.logger.warning("cover upload skipped platform=%s reason=no cover image", self.platform)
            return
        self.logger.info(
            "cover upload skipped platform=%s file=%s reason=selector not configured",
            self.platform,
            cover_path,
        )

    def save_draft(self) -> None:
        self.logger.info("draft save skipped platform=%s reason=manual review mode", self.platform)

    def find_visible(self, selectors: list[str], timeout_seconds: int = 30):
        assert self.page is not None
        for _ in range(timeout_seconds * 2):
            for selector in selectors:
                locator = self.page.locator(selector)
                count = locator.count()
                for index in range(count):
                    candidate = locator.nth(index)
                    if candidate.is_visible():
                        return candidate
            self.page.wait_for_timeout(500)
        return None

    def paste_text(self, locator, text: str) -> None:
        assert self.page is not None
        assert self.context is not None
        locator.click()
        self.context.grant_permissions(
            ["clipboard-read", "clipboard-write"],
            origin=self._current_origin(),
        )
        self.page.evaluate("text => navigator.clipboard.writeText(text)", text)
        self.page.keyboard.press("Control+A")
        self.page.keyboard.press("Control+V")
        self.page.wait_for_timeout(1000)

    def paste_rich_or_text(self, locator, html: str, plain_text: str) -> None:
        assert self.page is not None
        assert self.context is not None
        locator.click()
        self.context.grant_permissions(
            ["clipboard-read", "clipboard-write"],
            origin=self._current_origin(),
        )
        try:
            self.page.evaluate(
                """
                async ({html, text}) => {
                    const item = new ClipboardItem({
                        "text/html": new Blob([html], {type: "text/html"}),
                        "text/plain": new Blob([text], {type: "text/plain"})
                    });
                    await navigator.clipboard.write([item]);
                }
                """,
                {"html": html, "text": plain_text},
            )
            self.page.keyboard.press("Control+A")
            self.page.keyboard.press("Control+V")
            self.page.wait_for_timeout(1200)
        except Exception:
            self.logger.warning("rich clipboard paste failed platform=%s; fallback to plain text", self.platform)
            self.paste_text(locator, plain_text)

    def _current_origin(self) -> str:
        assert self.page is not None
        return self.page.evaluate("() => window.location.origin")

    def screenshot_on_error(self, error: Exception) -> Path:
        screenshot_dir = Path("data/logs/publisher_screenshots")
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = screenshot_dir / f"{self.platform}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        if self.page:
            self.page.wait_for_timeout(1000)
            self.page.screenshot(path=str(screenshot_path), full_page=True)
        self.logger.exception(
            "publisher error platform=%s screenshot=%s error=%s",
            self.platform,
            screenshot_path,
            error,
        )
        return screenshot_path

    def close(self) -> None:
        if self.context:
            self.context.close()
            self.context = None
        if getattr(self, "_playwright", None):
            self._playwright.stop()
            self._playwright = None


def extract_title(content: str, fallback: str) -> str:
    if looks_like_html(content):
        heading = first_heading_html(content)
        if heading:
            title = html_to_text(heading).strip()
            if title:
                return title[:100]
    text = html_to_text(content) if looks_like_html(content) else content
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if re.fullmatch(r"【[^】]*标题】", line.strip()):
            for candidate in lines[index + 1 :]:
                candidate = candidate.strip()
                if candidate:
                    return candidate[:100]
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        markdown_title = re.match(r"^#\s+(.+)$", stripped)
        if markdown_title:
            return markdown_title.group(1).strip()
        label_title = re.match(r"^【[^】]*标题】\s*(.+)$", stripped)
        if label_title:
            return label_title.group(1).strip()[:100]
        return stripped[:80]
    return fallback[:80]


def strip_title(content: str) -> str:
    if looks_like_html(content):
        text = html_to_text(strip_title_html(content))
        return strip_leading_platform_labels(text)
    lines = content.splitlines()
    for index, line in enumerate(lines):
        if re.fullmatch(r"【[^】]*正文】", line.strip()):
            return "\n".join(lines[index + 1 :]).strip()
    if lines and re.fullmatch(r"【[^】]*标题】", lines[0].strip()):
        title_seen = False
        remaining: list[str] = []
        for line in lines[1:]:
            if not title_seen and line.strip():
                title_seen = True
                continue
            if title_seen:
                remaining.append(line)
        return "\n".join(remaining).strip()
    if lines and re.match(r"^#\s+", lines[0].strip()):
        return "\n".join(lines[1:]).strip()
    return content.strip()


def html_to_text(content: str) -> str:
    content = re.sub(r"<script.*?</script>", "", content, flags=re.I | re.S)
    content = re.sub(r"<style.*?</style>", "", content, flags=re.I | re.S)
    content = re.sub(r"</h[1-6]>", "\n", content, flags=re.I)
    content = re.sub(r"</p>|<br\s*/?>|</li>", "\n", content, flags=re.I)
    content = re.sub(r"<[^>]+>", "", content)
    content = content.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\n{3,}", "\n\n", content).strip()


def looks_like_html(content: str) -> bool:
    return bool(re.search(r"<(?:html|body|article|h1|h2|p|div|br|ul|ol|li)\b", content, flags=re.I))


def extract_article_html(content: str) -> str:
    if not looks_like_html(content):
        return content
    match = re.search(
        r"<article\b[^>]*\bid=[\"']article[\"'][^>]*>(.*?)</article>",
        content,
        flags=re.I | re.S,
    )
    if match:
        return match.group(1).strip()
    body_match = re.search(r"<body\b[^>]*>(.*?)</body>", content, flags=re.I | re.S)
    return body_match.group(1).strip() if body_match else content


def first_heading_html(content: str) -> str:
    match = re.search(r"<h1\b[^>]*>(.*?)</h1>", content, flags=re.I | re.S)
    return match.group(1).strip() if match else ""


def strip_title_html(content: str) -> str:
    if not looks_like_html(content):
        return content
    stripped = re.sub(r"^\s*<h1\b[^>]*>.*?</h1>\s*", "", content, count=1, flags=re.I | re.S).strip()
    if stripped != content.strip():
        return strip_leading_platform_label_html(stripped)
    first_block = first_html_block(content)
    first_text = html_to_text(first_block).strip()
    if re.fullmatch(r"【[^】]*标题】", first_text):
        stripped = remove_first_html_block(content)
        stripped = remove_first_html_block(stripped)
        return strip_leading_platform_label_html(stripped)
    stripped = remove_first_html_block(content)
    return strip_leading_platform_label_html(stripped)


def strip_leading_platform_labels(text: str) -> str:
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and re.fullmatch(r"【[^】]*(?:正文|版本)[^】]*】", lines[0].strip()):
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
    return "\n".join(lines).strip()


PUBLISH_ONLY_MARKERS = (
    "推荐标签",
    "推荐Tag",
    "推荐 tag",
    "推荐TAG",
    "知乎推荐Tag",
    "知乎推荐 Tag",
    "CSDN关键词",
    "CSDN 关键词",
    "推荐分类",
    "CSDN推荐分类",
    "CSDN 推荐分类",
)


def strip_publish_only_sections(text: str) -> str:
    lines = text.splitlines()
    kept: list[str] = []
    skip = False
    for line in lines:
        stripped = line.strip()
        marker = section_marker_text(stripped)
        if marker:
            skip = True
            continue
        if skip:
            if is_probable_new_article_section(stripped):
                skip = False
            else:
                continue
        kept.append(line)
    return "\n".join(kept).strip()


def section_marker_text(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text).lower()
    return any(re.sub(r"\s+", "", marker).lower() in normalized for marker in PUBLISH_ONLY_MARKERS)


def is_probable_new_article_section(text: str) -> bool:
    if not text:
        return False
    if re.match(r"^#{1,4}\s+", text):
        return True
    if re.match(r"^[一二三四五六七八九十]+[、.．]\s*", text):
        return True
    return bool(re.match(r"^\d+[.、]\s*", text))


def strip_leading_platform_label_html(content: str) -> str:
    label_pattern = r"^\s*<(?:p|div|h2|h3)\b[^>]*>\s*【[^】]*(?:正文|版本)[^】]*】\s*</(?:p|div|h2|h3)>\s*"
    previous = None
    stripped = content.strip()
    while previous != stripped:
        previous = stripped
        stripped = re.sub(label_pattern, "", stripped, count=1, flags=re.I | re.S).strip()
    return stripped


def strip_publish_only_sections_html(content: str) -> str:
    if not looks_like_html(content):
        return strip_publish_only_sections(content)
    marker_pattern = "|".join(re.escape(marker) for marker in PUBLISH_ONLY_MARKERS)
    block_pattern = (
        rf"\s*<(?:h1|h2|h3|h4|p|div)\b[^>]*>[^<]*(?:{marker_pattern})[^<]*</(?:h1|h2|h3|h4|p|div)>"
        rf".*?\Z"
    )
    return re.sub(block_pattern, "", content, flags=re.I | re.S).strip()


def first_html_block(content: str) -> str:
    match = re.match(r"^\s*(<([a-z0-9]+)\b[^>]*>.*?</\2>)", content, flags=re.I | re.S)
    return match.group(1) if match else ""


def remove_first_html_block(content: str) -> str:
    return re.sub(r"^\s*<([a-z0-9]+)\b[^>]*>.*?</\1>\s*", "", content, count=1, flags=re.I | re.S).strip()


def build_summary(content: str, max_length: int = 180) -> str:
    text = html_to_text(content) if "<html" in content.lower() else content
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.M)
    text = re.sub(r"[*_>`#\[\]【】]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_length]
