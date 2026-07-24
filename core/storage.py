from __future__ import annotations

import html as html_lib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from core.content_sanitizer import sanitize_sohu_content


def save_topic_result(
    output_dir: Path,
    topic: dict[str, Any],
    result: dict[str, Any],
    config: dict[str, Any],
    job_id: str | None = None,
) -> Path:
    module = config.get("active_module", {"id": "fasium", "name": "FasiumAI"})
    package_name = f"job_{job_id}" if job_id else f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{_build_folder_name(topic['title'])}"
    topic_dir = output_dir / package_name
    topic_dir.mkdir(parents=True, exist_ok=True)
    zhihu_dir = topic_dir / "zhihu"
    csdn_dir = topic_dir / "csdn"
    sohu_dir = topic_dir / "sohu"
    assets_dir = topic_dir / "assets"
    for child_dir in (zhihu_dir, csdn_dir, sohu_dir, assets_dir):
        child_dir.mkdir(parents=True, exist_ok=True)

    zhihu_content = extract_platform_content(result.get("zhihu", ""), "zhihu")
    csdn_content = extract_platform_content(result.get("csdn", ""), "csdn")
    sohu_content = sanitize_sohu_content(
        extract_platform_content(result.get("sohu", ""), "sohu")
    )
    cover_prompt = extract_platform_content(result.get("cover_prompt", ""), "cover_prompt")

    files = {
        "zhihu.md": zhihu_content,
        "csdn.md": csdn_content,
        "sohu.md": sohu_content,
        "cover_prompt.txt": cover_prompt,
    }

    for file_name, content in files.items():
        (topic_dir / file_name).write_text(clean_saved_content(content or ""), encoding="utf-8")

    zhihu_rich = build_rich_html(zhihu_content, "zhihu")
    sohu_rich = build_rich_html(sohu_content, "sohu")

    (zhihu_dir / "zhihu_rich.html").write_text(zhihu_rich, encoding="utf-8")
    (csdn_dir / "csdn.md").write_text(clean_saved_content(csdn_content or ""), encoding="utf-8")
    (sohu_dir / "sohu_rich.html").write_text(sohu_rich, encoding="utf-8")
    (assets_dir / "cover_prompt.txt").write_text(clean_saved_content(cover_prompt or ""), encoding="utf-8")

    (topic_dir / "zhihu_rich.html").write_text(zhihu_rich, encoding="utf-8")
    (topic_dir / "sohu_rich.html").write_text(sohu_rich, encoding="utf-8")

    metadata = {
        "topic": topic,
        "job_id": job_id or "",
        "module_id": module.get("id", "fasium"),
        "module_name": module.get("name", "FasiumAI"),
        "workflow_name": module.get("workflow_name", ""),
        "review_status": "not_required",
        "partnership_claims": config.get("content", {}).get("approved_claims", []),
        "publish_status": {"zhihu": "idle", "csdn": "idle", "sohu": "idle"},
        "package_layout": "content_package_v2",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "project": config.get("project", {}),
        "content": config.get("content", {}),
        "package": {
            "zhihu": "zhihu/zhihu_rich.html",
            "csdn": "csdn/csdn.md",
            "sohu": "sohu/sohu_rich.html",
            "assets": {
                "cover_image": "assets/cover_image.jpg",
                "image_metadata": "assets/image_metadata.json",
                "attribution": "assets/attribution.txt",
                "prompt": "assets/cover_prompt.txt",
            },
        },
        "dify_raw_response": result.get("raw_response", {}),
    }
    (topic_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return topic_dir


def _build_folder_name(title: str) -> str:
    normalized = re.sub(r"[\\/:*?\"<>|\s]+", "_", title.strip())
    normalized = normalized.strip("._")
    return normalized[:80] or "untitled_topic"


def clean_saved_content(content: str) -> str:
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.S | re.I)
    content = re.sub(r"^\s*<think>.*", "", content, flags=re.S | re.I)
    return content.strip()


def extract_platform_content(content: Any, platform_key: str) -> str:
    text = clean_saved_content(str(content or ""))
    if not text:
        return ""

    fenced = re.fullmatch(r"\s*```(?:json|markdown|md)?\s*(.*?)\s*```\s*", text, flags=re.S | re.I)
    if fenced:
        text = fenced.group(1).strip()

    parsed = parse_json_object(text)
    if parsed:
        value = parsed.get(platform_key)
        if value:
            return clean_saved_content(str(value))

    return text


def parse_json_object(text: str) -> dict[str, Any] | None:
    candidates = [text]
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        candidates.insert(0, text[first_brace : last_brace + 1])

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def build_rich_html(markdown: str, platform: str) -> str:
    platform_name = {
        "zhihu": "\u77e5\u4e4e",
        "sohu": "\u641c\u72d0\u53f7",
    }.get(platform, platform)
    body = markdown_to_html(markdown)
    page_title = f"{platform_name}\u5bcc\u6587\u672c"
    copy_label = "\u590d\u5236\u5bcc\u6587\u672c"
    copy_hint = (
        f"\u590d\u5236\u540e\u7c98\u8d34\u5230{platform_name}\u7f16\u8f91\u5668\u3002"
        "\u5982\u679c\u6309\u94ae\u5931\u8d25\uff0c\u4e5f\u53ef\u4ee5\u624b\u52a8\u9009\u4e2d\u6587\u7ae0\u6b63\u6587\u590d\u5236\u3002"
    )
    copied_alert = "\u5df2\u590d\u5236\u5bcc\u6587\u672c"
    fallback_alert = "\u6d4f\u89c8\u5668\u9650\u5236\u4e86\u81ea\u52a8\u590d\u5236\uff0c\u8bf7\u6309 Ctrl+C \u590d\u5236\u9009\u4e2d\u5185\u5bb9"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{page_title}</title>
  <style>
    body {{
      margin: 0;
      background: #f6f7f9;
      color: #1f2937;
      font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
      line-height: 1.75;
    }}
    .toolbar {{
      position: sticky;
      top: 0;
      display: flex;
      gap: 10px;
      align-items: center;
      padding: 12px 20px;
      background: #ffffff;
      border-bottom: 1px solid #d9dee7;
      z-index: 2;
    }}
    button {{
      border: 0;
      border-radius: 6px;
      padding: 9px 12px;
      background: #2563eb;
      color: white;
      cursor: pointer;
    }}
    .hint {{ color: #6b7280; font-size: 14px; }}
    article {{
      max-width: 860px;
      margin: 24px auto;
      padding: 28px 34px;
      background: #ffffff;
      border: 1px solid #d9dee7;
      border-radius: 8px;
    }}
    h1, h2, h3 {{ line-height: 1.35; margin: 1.2em 0 .55em; }}
    h1 {{ font-size: 28px; }}
    h2 {{ font-size: 23px; }}
    h3 {{ font-size: 19px; }}
    p {{ margin: .8em 0; }}
    ul, ol {{ padding-left: 1.5em; }}
    blockquote {{ margin: 1em 0; padding: .2em 1em; border-left: 4px solid #93c5fd; color: #4b5563; background: #f8fafc; }}
    code {{ background: #eef2ff; padding: 2px 5px; border-radius: 4px; }}
    pre {{ overflow: auto; padding: 14px; border-radius: 6px; background: #f8fafc; color: #1f2937; border: 1px solid #d9dee7; }}
    a {{ color: #2563eb; }}
    strong {{ font-weight: 700; }}
  </style>
</head>
<body>
  <div class="toolbar">
    <button onclick="copyArticle()">{copy_label}</button>
    <span class="hint">{copy_hint}</span>
  </div>
  <article id="article" contenteditable="true">
{body}
  </article>
  <script>
    async function copyArticle() {{
      const article = document.getElementById("article");
      const html = article.innerHTML;
      const text = article.innerText;
      try {{
        await navigator.clipboard.write([
          new ClipboardItem({{
            "text/html": new Blob([html], {{ type: "text/html" }}),
            "text/plain": new Blob([text], {{ type: "text/plain" }})
          }})
        ]);
        alert("{copied_alert}");
      }} catch (error) {{
        const range = document.createRange();
        range.selectNodeContents(article);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        alert("{fallback_alert}");
      }}
    }}
  </script>
</body>
</html>"""


def markdown_to_html(markdown: str) -> str:
    if not markdown:
        return "<p></p>"

    lines = markdown.splitlines()
    html_parts: list[str] = []
    list_type: str | None = None
    in_code = False
    code_lines: list[str] = []

    def close_list() -> None:
        nonlocal list_type
        if list_type:
            html_parts.append(f"</{list_type}>")
            list_type = None

    for line in lines:
        raw = line.rstrip()
        stripped = raw.strip()

        if stripped.startswith("```"):
            if in_code:
                html_parts.append(f"<pre><code>{html_lib.escape(chr(10).join(code_lines))}</code></pre>")
                code_lines = []
                in_code = False
            else:
                close_list()
                in_code = True
            continue

        if in_code:
            code_lines.append(raw)
            continue

        if not stripped:
            close_list()
            continue

        heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading:
            close_list()
            level = min(len(heading.group(1)), 3)
            html_parts.append(f"<h{level}>{inline_markdown(heading.group(2))}</h{level}>")
            continue

        quote = re.match(r"^>\s*(.+)$", stripped)
        if quote:
            close_list()
            html_parts.append(f"<blockquote>{inline_markdown(quote.group(1))}</blockquote>")
            continue

        unordered = re.match(r"^[-*]\s+(.+)$", stripped)
        if unordered:
            if list_type != "ul":
                close_list()
                list_type = "ul"
                html_parts.append("<ul>")
            html_parts.append(f"<li>{inline_markdown(unordered.group(1))}</li>")
            continue

        ordered = re.match(r"^\d+[.)]\s+(.+)$", stripped)
        if ordered:
            if list_type != "ol":
                close_list()
                list_type = "ol"
                html_parts.append("<ol>")
            html_parts.append(f"<li>{inline_markdown(ordered.group(1))}</li>")
            continue

        close_list()
        html_parts.append(f"<p>{inline_markdown(stripped)}</p>")

    close_list()
    if in_code:
        html_parts.append(f"<pre><code>{html_lib.escape(chr(10).join(code_lines))}</code></pre>")

    return "\n".join(html_parts)


def inline_markdown(text: str) -> str:
    escaped = html_lib.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"`(.+?)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', escaped)
    return escaped
