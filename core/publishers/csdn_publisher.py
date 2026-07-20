from __future__ import annotations

from pathlib import Path

from core.publishers.base import BrowserPublisher


class CSDNPublisher(BrowserPublisher):
    platform = "csdn"
    editor_url = "https://editor.csdn.net/md/"
    content_file = "csdn/csdn.md"

    def _find_visible(self, selectors: list[str], timeout_seconds: int = 60):
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

    def _dismiss_beginner_guide(self) -> None:
        assert self.page is not None
        guide = self.page.locator(".beginnerGuide-box")
        if not guide.count() or not guide.first.is_visible():
            return
        button = guide.first.get_by_text("我知道了", exact=True)
        if button.count() and button.first.is_visible():
            button.first.click()
            self.page.wait_for_timeout(500)
            self.logger.info("beginner guide dismissed platform=csdn")

    def fill_title(self, title: str) -> None:
        assert self.page is not None
        self.logger.info("title filling platform=csdn url=%s", self.page.url)
        self._dismiss_beginner_guide()

        title_display = self.page.locator(".article-bar__title-display")
        if title_display.count() and title_display.first.is_visible():
            title_display.first.click()
            self.page.wait_for_timeout(300)

        selectors = [
            "input.article-bar__title",
            ".article-bar__input-box input[placeholder*='请输入文章标题']",
            "textarea[placeholder*='请输入文章标题']",
            "input[placeholder*='请输入文章标题']",
            "textarea[placeholder*='文章标题']",
            "input[placeholder*='文章标题']",
            "textarea[placeholder*='标题']",
            "input[placeholder*='标题']",
            "textarea.article-bar__title",
            ".article-bar__title textarea",
            ".article-bar__title input",
            "textarea.title-input",
            "input.title-input",
        ]
        locator = self._find_visible(selectors)
        if locator is None:
            if self.page.get_by_text("登录", exact=True).count():
                raise RuntimeError("CSDN 仍处于未登录状态，请登录后重新运行发布命令")
            raise RuntimeError(
                f"CSDN 编辑器没有加载出标题框，当前页面：{self.page.url}。请检查网络或页面是否停在创作中心首页"
            )
        locator.fill(title[:100])
        self.logger.info("title filled platform=csdn")

    def fill_content(self, content: str) -> None:
        assert self.page is not None
        self.logger.info("content filling platform=csdn")
        selectors = [
            "pre.editor__inner[contenteditable='true']",
            ".editor pre.editor__inner",
            ".bytemd-editor .cm-content",
            ".CodeMirror textarea",
            ".CodeMirror-code",
            "textarea[placeholder*='请输入正文']",
            "textarea[placeholder*='正文']",
            "[contenteditable='true'][role='textbox']",
            ".editor-content [contenteditable='true']",
            ".markdown-body[contenteditable='true']",
        ]
        locator = self._find_visible(selectors, timeout_seconds=30)
        if locator is None:
            raise RuntimeError("CSDN 编辑器已打开，但没有找到正文编辑区，请保存错误截图以便更新选择器")

        locator.click()
        tag_name = locator.evaluate("element => element.tagName.toLowerCase()")
        if tag_name in {"textarea", "input"} and "cledit-section" not in (
            locator.get_attribute("class") or ""
        ):
            locator.fill(content)
        else:
            assert self.context is not None
            self.context.grant_permissions(
                ["clipboard-read", "clipboard-write"],
                origin="https://editor.csdn.net",
            )
            self.page.evaluate("text => navigator.clipboard.writeText(text)", content)
            self.page.keyboard.press("Control+A")
            self.page.keyboard.press("Control+V")
            self.page.wait_for_timeout(1500)
            rendered_text = locator.inner_text().strip()
            if len(rendered_text) < 20:
                self.page.keyboard.insert_text(content)
        self.logger.info("content filled platform=csdn")

    def upload_cover(self, cover_path: Path | None) -> None:
        assert self.page is not None
        self._open_publish_dialog()
        self._fill_publish_summary()
        if not cover_path:
            super().upload_cover(cover_path)
            return
        self.logger.info("cover upload platform=csdn file=%s", cover_path)

        inputs = self._find_cover_file_inputs()
        if inputs and inputs.count():
            inputs.nth(inputs.count() - 1).set_input_files(str(cover_path))
            self.page.wait_for_timeout(2000)
            self.logger.info("cover uploaded platform=csdn")
            return

        upload_button = self.page.get_by_text("从本地上传", exact=False)
        if upload_button.count() and upload_button.first.is_visible():
            upload_button.first.click()
            self.page.wait_for_timeout(500)
            inputs = self._find_cover_file_inputs()
            if inputs and inputs.count():
                inputs.nth(inputs.count() - 1).set_input_files(str(cover_path))
                self.page.wait_for_timeout(2000)
                self.logger.info("cover uploaded platform=csdn")
                return

        self.logger.warning("cover upload skipped platform=csdn reason=file input not found")

    def save_draft(self) -> None:
        assert self.page is not None
        self.logger.info("publish dialog prepared platform=csdn reason=manual final submit")

    def _open_publish_dialog(self) -> None:
        assert self.page is not None
        if self.page.get_by_text("文章标签", exact=False).count() and self.page.get_by_text(
            "添加封面", exact=False
        ).count():
            self.logger.info("publish dialog already open platform=csdn")
            return

        self.logger.info("opening publish dialog platform=csdn")
        buttons = self.page.get_by_role("button", name="发布文章")
        for index in range(buttons.count() - 1, -1, -1):
            button = buttons.nth(index)
            if button.is_visible():
                button.click(force=True)
                self.page.wait_for_timeout(1500)
                break
        else:
            candidates = [
                "button:has-text('发布文章')",
                ".btn:has-text('发布文章')",
                "text=发布文章",
            ]
            clicked = False
            for selector in candidates:
                locator = self.page.locator(selector)
                for index in range(locator.count() - 1, -1, -1):
                    candidate = locator.nth(index)
                    if candidate.is_visible():
                        candidate.click(force=True)
                        self.page.wait_for_timeout(1500)
                        clicked = True
                        break
                if clicked:
                    break
            if not clicked:
                raise RuntimeError("CSDN 没有找到正文页的“发布文章”按钮，无法打开发布设置弹窗")

        if not self.page.get_by_text("添加封面", exact=False).count():
            self.page.wait_for_timeout(2000)
        if not self.page.get_by_text("添加封面", exact=False).count():
            raise RuntimeError("CSDN 发布设置弹窗没有打开，请检查页面是否仍停留在编辑器")
        self.logger.info("publish dialog opened platform=csdn")

    def _find_cover_file_inputs(self):
        assert self.page is not None
        selectors = [
            "div:has-text('添加封面') input[type='file']",
            "div:has-text('从本地上传') input[type='file']",
            "input[type='file']",
        ]
        for selector in selectors:
            locator = self.page.locator(selector)
            if locator.count():
                return locator
        return None

    def _fill_publish_summary(self) -> None:
        assert self.page is not None
        package = getattr(self, "current_package", {}) or {}
        summary = str(package.get("summary") or "").strip()
        if not summary:
            return

        selectors = [
            "textarea[placeholder*='摘要']",
            "textarea[maxlength='256']",
            "textarea",
        ]
        for selector in selectors:
            locator = self.page.locator(selector)
            for index in range(locator.count()):
                candidate = locator.nth(index)
                if candidate.is_visible():
                    existing = candidate.input_value().strip()
                    if not existing:
                        candidate.fill(summary[:256])
                        self.logger.info("summary filled platform=csdn")
                    return
