from __future__ import annotations

from pathlib import Path

from core.publishers.base import BrowserPublisher


class SohuPublisher(BrowserPublisher):
    platform = "sohu"
    editor_url = "https://mp.sohu.com/mpfe/v3/main/news/add"
    content_file = "sohu/sohu_rich.html"

    def open_editor(self) -> None:
        assert self.page is not None
        super().open_editor()
        self._enter_article_editor()

    def login_check(self) -> None:
        super().login_check()
        self._enter_article_editor()

    def fill_title(self, title: str) -> None:
        assert self.page is not None
        self.logger.info("title filling platform=sohu")
        self._enter_article_editor()
        selectors = [
            "input[placeholder*='标题']",
            "textarea[placeholder*='标题']",
            "input[placeholder*='请输入标题']",
            "textarea[placeholder*='请输入标题']",
            "input[placeholder*='请在这里输入标题']",
            "textarea[placeholder*='请在这里输入标题']",
            ".title input",
            ".title textarea",
            "input[maxlength='64']",
            "input[maxlength='100']",
            "textarea[maxlength='64']",
            "textarea[maxlength='100']",
            "[contenteditable='true'][placeholder*='标题']",
        ]
        locator = self.find_visible(selectors, timeout_seconds=45)
        if locator:
            tag_name = locator.evaluate("element => element.tagName.toLowerCase()")
            if tag_name in {"input", "textarea"}:
                locator.fill(title[:64])
            else:
                self.paste_text(locator, title[:64])
            self.logger.info("title filled platform=sohu")
            return
        raise RuntimeError("Sohu title input not found; please update selector")

    def fill_content(self, content: str) -> None:
        assert self.page is not None
        self.logger.info("content filling platform=sohu")
        selectors = [
            ".ql-editor[contenteditable='true']",
            ".ProseMirror[contenteditable='true']",
            ".editor [contenteditable='true']",
            "iframe",
            "[contenteditable='true']",
            "textarea",
        ]
        locator = self.find_visible(selectors, timeout_seconds=45)
        if locator:
            tag_name = locator.evaluate("element => element.tagName.toLowerCase()")
            package = getattr(self, "current_package", {}) or {}
            raw_content = str(package.get("raw_content") or content)
            if tag_name == "iframe":
                frame = locator.content_frame()
                if frame:
                    frame_editor = frame.locator("[contenteditable='true'], body").first
                    frame_editor.click()
                    frame.evaluate("text => document.execCommand('insertText', false, text)", content)
                    self.logger.info("content filled platform=sohu via iframe")
                    return
            if tag_name == "textarea":
                locator.fill(content)
            elif package.get("is_html"):
                self.paste_rich_or_text(locator, raw_content, content)
            else:
                self.paste_text(locator, content)
            self.logger.info("content filled platform=sohu")
            return
        raise RuntimeError("Sohu editor input not found; please update selector")

    def upload_cover(self, cover_path: Path | None) -> None:
        if not cover_path:
            super().upload_cover(cover_path)
            return
        assert self.page is not None
        self.logger.info("cover upload platform=sohu file=%s", cover_path)
        for label in ["添加封面", "上传封面", "本地上传", "上传图片"]:
            button = self.page.get_by_text(label, exact=False)
            if button.count() and button.first.is_visible():
                button.first.click()
                self.page.wait_for_timeout(800)
                break
        inputs = self.page.locator("input[type='file']")
        if inputs.count():
            inputs.nth(inputs.count() - 1).set_input_files(str(cover_path))
            self.page.wait_for_timeout(2000)
            self.logger.info("cover uploaded platform=sohu")
            return
        self.logger.warning("cover upload skipped platform=sohu reason=file input not found")

    def save_draft(self) -> None:
        assert self.page is not None
        self.logger.info("sohu ready platform=sohu reason=manual final submit")
        for label in ["保存草稿", "存草稿"]:
            button = self.page.get_by_text(label, exact=True)
            if button.count() and button.first.is_visible():
                self.logger.info("sohu draft button visible platform=sohu")
                return

    def _enter_article_editor(self) -> None:
        assert self.page is not None
        title_ready = self.find_visible(
            [
                "input[placeholder*='标题']",
                "textarea[placeholder*='标题']",
                "input[placeholder*='请输入标题']",
                "textarea[placeholder*='请输入标题']",
                ".title input",
                ".title textarea",
            ],
            timeout_seconds=5,
        )
        if title_ready:
            return

        self.logger.info("entering sohu article editor url=%s", self.page.url)
        for label in ["发布内容", "写文章", "发文章", "文章"]:
            locator = self.page.get_by_text(label, exact=True)
            if not locator.count():
                locator = self.page.get_by_text(label, exact=False)
            for index in range(locator.count()):
                candidate = locator.nth(index)
                if not candidate.is_visible():
                    continue
                candidate.click(force=True)
                self.page.wait_for_timeout(2500)
                if self._maybe_choose_article_type() or self.find_visible(
                    ["input[placeholder*='标题']", "textarea[placeholder*='标题']"],
                    timeout_seconds=5,
                ):
                    return

        if "news/add" not in self.page.url:
            self.page.goto(self.editor_url, wait_until="domcontentloaded", timeout=60000)
            self.page.wait_for_timeout(3000)
            self._maybe_choose_article_type()

    def _maybe_choose_article_type(self) -> bool:
        assert self.page is not None
        if self.find_visible(["input[placeholder*='标题']", "textarea[placeholder*='标题']"], timeout_seconds=2):
            return True
        for label in ["文章", "发布文章", "图文"]:
            locator = self.page.get_by_text(label, exact=True)
            for index in range(locator.count()):
                candidate = locator.nth(index)
                if candidate.is_visible():
                    candidate.click(force=True)
                    self.page.wait_for_timeout(3000)
                    return True
        return False
