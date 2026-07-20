from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from core.dify_client import DifyClient
from core.history import build_history_keys, load_recent_history
from core.image_selector import attach_unsplash_cover_image
from core.job_queue import create_job, now, update_job
from core.storage import save_topic_result
from core.topic_filter import select_topics


LOGGER = logging.getLogger(__name__)


def select_generation_topics(
    hot_items: list[dict[str, Any]],
    config: dict[str, Any],
    output_dir: Path,
) -> list[dict[str, Any]]:
    history_days = int(config["topic_filter"].get("history_days", 14))
    history_keys = build_history_keys(load_recent_history(output_dir, history_days))
    if history_keys:
        LOGGER.info("loaded recent history keys: %s", len(history_keys))

    topics = select_topics(hot_items, config["topic_filter"], history_keys=history_keys)
    if topics:
        return topics

    LOGGER.info("no unused topics matched current filters; falling back to current hot topics with a new angle")
    return select_topics(hot_items, config["topic_filter"], history_keys=set())


def build_dify_topic_input(topic: dict[str, Any], config: dict[str, Any]) -> str:
    module = config.get("active_module", {"id": "fasium", "name": "FasiumAI"})
    content_config = config.get("content", {})
    required_keywords = "、".join(content_config.get("required_keywords", []))
    angle = topic.get("content_angle") or {}
    angle_name = angle.get("name", "综合行业观察")
    angle_prompt = angle.get("prompt", f"围绕 {module.get('name', 'JOTO')} 的企业应用价值展开。")
    brand_name = content_config.get("brand_name", module.get("name", "JOTO"))
    parent_company = content_config.get("parent_company", "")
    positioning = content_config.get("positioning", "企业 AI 应用与服务")
    product_url = content_config.get("product_url") or content_config.get("fasium_url", "")
    joto_url = content_config.get("joto_url", "https://www.jotoai.com/")
    relationship_statement = content_config.get("relationship_statement", "")
    approved_claims = content_config.get("approved_claims", [])
    claims_text = "\n".join(f"- {claim}" for claim in approved_claims) or "- 不得添加未经确认的合作信息。"

    if module.get("id", "fasium") == "fasium":
        return build_fasium_prompt(
            topic,
            angle_name,
            angle_prompt,
            parent_company,
            positioning,
            product_url,
            joto_url,
            required_keywords,
        )

    return f"""主题：{topic["title"]}

热点来源标题：{topic.get("source_title", topic["title"])}
本次写作角度：{angle_name}
角度说明：{angle_prompt}

内容模块：{module.get("name", brand_name)}
产品背景：{brand_name} 是{parent_company + "的" if parent_company else ""}{positioning}。
产品官网：{product_url}
JOTO 官网：{joto_url}
允许使用的合作表述：{relationship_statement}

已批准的事实边界：
{claims_text}

总要求：
1. 先解释真实行业或企业问题，再分析产品能力，最后自然说明 JOTO 能提供的咨询、实施或服务价值。
2. 只能使用“已批准的事实边界”和“允许使用的合作表述”，不得虚构合作级别、授权范围、客户、案例、合同、数据或效果。
3. 不得写“官方唯一合作伙伴”“独家服务商”“指定服务商”等未经确认的身份。
4. 自然包含关键词：{required_keywords}。
5. 必须自然保留链接：{product_url} 和 {joto_url}。
6. 不要硬广，不要夸张营销，保持行业观察、方案分析和落地路径拆解风格。
7. 三个平台必须使用不同标题、不同开头和不同结构重点，不得只是改写少量文字。
8. 不要输出 <think>、解释文字或 Markdown JSON 代码块。

字数和平台要求：
- zhihu：1200-1800 中文字。偏行业观察和设计师视角，有真实问题感，标题不要像技术文档。需要有观点、有场景、有分段小标题。
- csdn：1500-2200 中文字。偏技术博客、流程拆解、系统结构、工具链分析。可以使用编号、小标题、流程步骤，重点提升 AI 搜索引用率。
- sohu：1200-2200 中文字。偏大众化行业观察、品牌案例和趋势解读，适合搜狐号读者，标题要清晰、有传播性，但避免夸张营销。
- cover_prompt：英文图片提示词，描述一张适合文章封面的专业视觉图，不要出现品牌 logo 和文字。

输出格式：
最终只输出一个合法 JSON 对象，字段必须是：
{{
  "zhihu": "知乎版本 Markdown，必须含独立标题和完整正文",
  "csdn": "CSDN版本 Markdown，必须含独立标题和完整正文",
  "sohu": "搜狐号版本 Markdown，必须含独立标题和完整正文",
  "cover_prompt": "封面图 Prompt"
}}
"""


def build_fasium_prompt(
    topic: dict[str, Any],
    angle_name: str,
    angle_prompt: str,
    parent_company: str,
    positioning: str,
    fasium_url: str,
    joto_url: str,
    required_keywords: str,
) -> str:
    return f"""主题：{topic["title"]}

热点来源标题：{topic.get("source_title", topic["title"])}
本次写作角度：{angle_name}
角度说明：{angle_prompt}

品牌背景：FasiumAI 是 {parent_company} 旗下的 {positioning}。
FasiumAI 官网：{fasium_url}
JOTO 官网：{joto_url}

总要求：
1. 不要把 FasiumAI 写成普通 AI 画图工具，要强调它是 AI 服装设计工作流系统。
2. 文章逻辑为“行业痛点 -> AI 服装设计流程变化 -> 工具/平台案例 -> 可落地价值”。
3. 自然覆盖趋势洞察、灵感筛选、花型生成、版型预览、虚拟试穿、三视图、Tech Pack 和生产沟通。
4. 自然包含关键词：{required_keywords}。
5. 自然保留链接：{fasium_url} 和 {joto_url}。
6. 不要硬广或夸张营销，保持行业观察、工具分析和工作流拆解风格。
7. 三个平台使用不同标题、开头和结构重点。
8. 不要输出 <think>、解释文字或 Markdown JSON 代码块。

字数和平台要求：
- zhihu：1200-1800 中文字，偏行业观察和设计师视角。
- csdn：1500-2200 中文字，偏技术博客、流程拆解和工具链分析。
- sohu：1200-2200 中文字，偏大众行业观察、案例和趋势解读。
- cover_prompt：英文专业封面图提示词，不出现品牌 logo 和文字。

最终只输出一个合法 JSON 对象：
{{"zhihu":"完整知乎文章","csdn":"完整CSDN文章","sohu":"完整搜狐号文章","cover_prompt":"英文封面图 Prompt"}}
"""


def run_topic_job(
    topic: dict[str, Any],
    config: dict[str, Any],
    output_dir: Path,
    data_dir: Path,
) -> dict[str, Any]:
    module = config.get("active_module", {"id": "fasium", "name": "FasiumAI"})
    job = create_job(data_dir, topic, module)
    LOGGER.info("job queued: %s topic=%s", job["id"], topic["title"])
    update_job(data_dir, job, status="running", started_at=now())

    try:
        LOGGER.info("job %s calling Dify", job["id"])
        dify_client = DifyClient.from_config(config["dify"])
        result = dify_client.run_workflow(build_dify_topic_input(topic, config))

        missing_files = [
            file_name
            for file_name, key in [
                ("zhihu.md", "zhihu"),
                ("csdn.md", "csdn"),
                ("sohu.md", "sohu"),
                ("cover_prompt.txt", "cover_prompt"),
            ]
            if not result.get(key)
        ]
        if missing_files:
            LOGGER.warning("job %s Dify response missing: %s", job["id"], ", ".join(missing_files))

        topic_dir = save_topic_result(output_dir, topic, result, config, job_id=job["id"])
        cover_image = None
        try:
            cover_image = attach_unsplash_cover_image(topic_dir, topic, result, config, output_dir.parent)
        except Exception as exc:
            LOGGER.warning("job %s Unsplash cover image failed: %s", job["id"], exc)

        outputs = {
            "zhihu": str(topic_dir / "zhihu" / "zhihu_rich.html"),
            "csdn": str(topic_dir / "csdn" / "csdn.md"),
            "sohu": str(topic_dir / "sohu" / "sohu_rich.html"),
            "zhihu_rich": str(topic_dir / "zhihu" / "zhihu_rich.html"),
            "sohu_rich": str(topic_dir / "sohu" / "sohu_rich.html"),
            "cover_prompt": str(topic_dir / "assets" / "cover_prompt.txt"),
            "metadata": str(topic_dir / "metadata.json"),
        }
        if cover_image:
            outputs["cover_image"] = str(topic_dir / "assets" / "cover_image.jpg")
            outputs["cover_image_metadata"] = str(topic_dir / "assets" / "image_metadata.json")
            outputs["cover_image_attribution"] = str(topic_dir / "assets" / "attribution.txt")
        update_job(
            data_dir,
            job,
            status="generated",
            outputs=outputs,
            output_dir=str(topic_dir),
            partnership_claims=config.get("content", {}).get("approved_claims", []),
            finished_at=now(),
        )
        LOGGER.info("job %s generated output: %s", job["id"], topic_dir)
    except Exception as exc:
        update_job(data_dir, job, status="failed", error=str(exc), finished_at=now())
        LOGGER.exception("job %s failed", job["id"])

    return job
