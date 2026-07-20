from __future__ import annotations

import time
from pathlib import Path

from core.publishers.base import BrowserPublisher


class ZhihuPublisher(BrowserPublisher):
    platform = "zhihu"
    editor_url = "https://zhuanlan.zhihu.com/write"
    content_file = "zhihu/zhihu_rich.html"

    title_selectors = [
        "[placeholder*='请输入标题']",
        "[aria-label*='标题']",
        "textarea[placeholder*='标题']",
        "input[placeholder*='标题']",
        ".Editable-title [contenteditable='true']",
        ".Post-Title [contenteditable='true']",
        ".WriteIndex-title [contenteditable='true']",
        ".Editable-title textarea",
        ".WriteIndex-titleInput textarea",
        "[contenteditable='true'][placeholder*='标题']",
        "[contenteditable='true']",
    ]

    def login_check(self) -> None:
        assert self.page is not None
        title_ready = self.find_visible(self.title_selectors, timeout_seconds=8)
        if title_ready:
            self.logger.info("login status check platform=zhihu required=False")
            return

        login_required = any(
            marker in self.page.url.lower()
            for marker in ("signin", "login", "account", "oauth")
        )
        for label in ("登录", "登录/注册", "验证码登录", "密码登录", "打开知乎 App"):
            locator = self.page.get_by_text(label, exact=False)
            if locator.count() and locator.first.is_visible():
                login_required = True
                break

        self.logger.info(
            "login status check platform=zhihu url=%s required=%s",
            self.page.url,
            login_required,
        )
        if not login_required:
            return

        print("检测到知乎尚未登录。请在打开的浏览器中完成扫码或验证码登录。")
        if self.web_publish_mode:
            self._wait_for_web_login()
        else:
            input("知乎登录完成后，回到这里按 Enter 继续：")
        self._return_to_editor_after_login()

        if not self.find_visible(self.title_selectors, timeout_seconds=20):
            raise RuntimeError("知乎登录后仍没有进入写作页，请确认登录成功后重新运行发布命令")

    def _wait_for_web_login(self, timeout_seconds: int = 300) -> None:
        assert self.page is not None
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self.find_visible(self.title_selectors, timeout_seconds=2):
                return
            login_url = any(marker in self.page.url.lower() for marker in ("signin", "login", "account", "oauth"))
            if not login_url:
                try:
                    self._return_to_editor_after_login()
                except Exception:
                    self.page.wait_for_timeout(1000)
                    continue
                if self.find_visible(self.title_selectors, timeout_seconds=5):
                    return
            self.page.wait_for_timeout(1000)
        raise RuntimeError("等待知乎登录超时，请重新启动发布辅助")

    def _return_to_editor_after_login(self) -> None:
        assert self.page is not None
        if "zhuanlan.zhihu.com/write" in self.page.url and self.find_visible(
            self.title_selectors, timeout_seconds=5
        ):
            self.logger.info("zhihu editor already open after login")
            return

        try:
            self.page.goto(self.editor_url, wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:
            message = str(exc)
            if "interrupted by another navigation" not in message:
                raise
            self.logger.warning("zhihu editor navigation interrupted; waiting for final page url=%s", self.page.url)
        self.page.wait_for_load_state("domcontentloaded", timeout=30000)
        self.page.wait_for_timeout(3000)

    def fill_title(self, title: str) -> None:
        assert self.page is not None
        self.logger.info("title filling platform=zhihu")
        locator = self.find_visible(self.title_selectors, timeout_seconds=45)
        if locator:
            tag_name = locator.evaluate("element => element.tagName.toLowerCase()")
            if tag_name in {"textarea", "input"}:
                locator.fill(title[:100])
            else:
                self.paste_text(locator, title[:100])
            self.logger.info("title filled platform=zhihu")
            return
        raise RuntimeError("Zhihu title input not found; please update selector")

    def fill_content(self, content: str) -> None:
        assert self.page is not None
        self.logger.info("content filling platform=zhihu")
        selectors = [
            ".Editable-content [contenteditable='true']",
            ".RichText [contenteditable='true']",
            ".DraftEditor-editorContainer [contenteditable='true']",
            "[contenteditable='true'][data-slate-editor='true']",
            "[contenteditable='true']",
        ]
        editors = []
        for selector in selectors:
            locator = self.page.locator(selector)
            for index in range(locator.count()):
                candidate = locator.nth(index)
                if candidate.is_visible():
                    editors.append(candidate)
            if editors:
                break
        if editors:
            target = editors[-1] if len(editors) > 1 else editors[0]
            package = getattr(self, "current_package", {}) or {}
            raw_content = str(package.get("raw_content") or content)
            if package.get("is_html"):
                self.paste_rich_or_text(target, raw_content, content)
            else:
                self.paste_text(target, content)
            self.logger.info("content filled platform=zhihu")
            return
        raise RuntimeError("Zhihu editor input not found; please update selector")

    def upload_cover(self, cover_path: Path | None) -> None:
        if not cover_path:
            super().upload_cover(cover_path)
            return
        assert self.page is not None
        self.logger.info("cover upload platform=zhihu file=%s", cover_path)

        self._open_publish_settings()
        self._wait_until_draft_ready(extra_wait_ms=8000)
        for attempt in range(1, 4):
            self.logger.info("zhihu cover upload attempt=%s", attempt)
            self._dismiss_draft_loading_dialog()
            if self._upload_cover_via_file_chooser(cover_path):
                return
            self._wait_until_draft_ready()
            if self._upload_cover_via_file_input(cover_path):
                return
            self._wait_until_draft_ready(extra_wait_ms=3000)
        self.logger.warning("cover upload skipped platform=zhihu reason=file input not found")

    def save_draft(self) -> None:
        assert self.page is not None
        self.logger.info("zhihu ready platform=zhihu reason=manual final submit")
        button = self.page.get_by_text("发布", exact=True)
        if button.count():
            self.logger.info("zhihu publish button visible platform=zhihu")

    def _open_publish_settings(self) -> None:
        assert self.page is not None
        self.page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        self.page.wait_for_timeout(800)
        for label in ["发布设置", "添加封面", "添加文章封面"]:
            locator = self.page.get_by_text(label, exact=False)
            for index in range(locator.count()):
                candidate = locator.nth(index)
                if candidate.is_visible():
                    candidate.scroll_into_view_if_needed()
                    self.page.wait_for_timeout(300)
                    if label == "发布设置":
                        candidate.click()
                        self.page.wait_for_timeout(800)
                    return

    def _upload_cover_via_file_chooser(self, cover_path: Path) -> bool:
        assert self.page is not None
        labels = ["添加文章封面", "添加封面", "上传封面", "重新上传", "更换封面"]
        for label in labels:
            self._dismiss_draft_loading_dialog()
            locator = self.page.get_by_text(label, exact=False)
            for index in range(locator.count()):
                candidate = locator.nth(index)
                if not candidate.is_visible():
                    continue
                candidate.scroll_into_view_if_needed()
                self.page.wait_for_timeout(300)
                try:
                    with self.page.expect_file_chooser(timeout=3000) as chooser_info:
                        candidate.click(force=True, timeout=3000)
                    chooser_info.value.set_files(str(cover_path))
                    self.page.wait_for_timeout(2500)
                    if self._dismiss_draft_loading_dialog():
                        return False
                    self.logger.info("cover uploaded platform=zhihu method=file_chooser label=%s", label)
                    return True
                except Exception as exc:
                    self._dismiss_draft_loading_dialog()
                    self.logger.info("zhihu cover chooser not available label=%s error=%s", label, exc)
        return False

    def _upload_cover_via_file_input(self, cover_path: Path) -> bool:
        assert self.page is not None
        selectors = [
            "input[type='file'][accept*='image']",
            "input[type='file']",
        ]
        for selector in selectors:
            inputs = self.page.locator(selector)
            for index in range(inputs.count() - 1, -1, -1):
                input_locator = inputs.nth(index)
                try:
                    input_locator.set_input_files(str(cover_path))
                    self.page.wait_for_timeout(2500)
                    if self._dismiss_draft_loading_dialog():
                        return False
                    self.logger.info("cover uploaded platform=zhihu method=file_input selector=%s index=%s", selector, index)
                    return True
                except Exception as exc:
                    self._dismiss_draft_loading_dialog()
                    self.logger.info("zhihu cover input skipped selector=%s index=%s error=%s", selector, index, exc)
        return False

    def _wait_until_draft_ready(self, extra_wait_ms: int = 5000) -> None:
        assert self.page is not None
        self.page.wait_for_timeout(extra_wait_ms)
        for _ in range(10):
            if not self._dismiss_draft_loading_dialog():
                return
            self.page.wait_for_timeout(3000)

    def _dismiss_draft_loading_dialog(self) -> bool:
        assert self.page is not None
        messages = ["草稿加载中", "请等待加载完成后再次修改", "加载完成后再次修改"]
        visible = False
        for message in messages:
            locator = self.page.get_by_text(message, exact=False)
            if locator.count() and locator.first.is_visible():
                visible = True
                break
        if not visible:
            return False

        self.logger.info("zhihu draft loading dialog detected; dismissing")
        selectors = [
            ".Modal button:has-text('确定')",
            ".Modal button:has-text('我知道了')",
            "button:has-text('确定')",
            "button:has-text('我知道了')",
            "text=确定",
        ]
        for selector in selectors:
            locator = self.page.locator(selector)
            for index in range(locator.count()):
                button = locator.nth(index)
                if not button.is_visible():
                    continue
                try:
                    button.click(force=True, timeout=2000)
                    self._wait_for_modal_to_close()
                    return True
                except Exception as exc:
                    self.logger.info("zhihu modal confirm click failed selector=%s error=%s", selector, exc)

        clicked = self.page.evaluate(
            """
            () => {
                const elements = [...document.querySelectorAll('button, div, span')];
                const target = elements.find((el) => el.innerText && el.innerText.trim() === '确定');
                if (!target) return false;
                target.click();
                return true;
            }
            """
        )
        if clicked:
            self._wait_for_modal_to_close()
            return True
        self.page.keyboard.press("Enter")
        self._wait_for_modal_to_close()
        return True

    def _wait_for_modal_to_close(self) -> None:
        assert self.page is not None
        self.page.wait_for_timeout(800)
        for _ in range(10):
            backdrop = self.page.locator(".Modal-backdrop")
            if not backdrop.count() or not backdrop.first.is_visible():
                return
            self.page.wait_for_timeout(500)
