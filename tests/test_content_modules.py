from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.config import load_config
from core.content_profiles import build_module_config, list_content_profiles
from core.content_sanitizer import sanitize_sohu_content
from core.image_selector import build_search_query
from core.dify_client import DifyClient
from core.job_queue import create_job
from core.storage import save_topic_result
from core.topic_generator import build_dify_topic_input


ROOT = Path(__file__).resolve().parents[1]


class ContentModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(ROOT / "config.yaml")

    def test_four_profiles_are_available(self) -> None:
        self.assertEqual(
            [profile["id"] for profile in list_content_profiles(self.config)],
            ["fasium", "workbuddy_joto", "adp_joto", "dify_joto"],
        )

    def test_profile_selects_its_own_dify_key(self) -> None:
        module_config = build_module_config(self.config, "adp_joto")
        self.assertEqual(module_config["dify"]["api_key_env"], "DIFY_API_KEY_ADP")
        self.assertEqual(module_config["active_module"]["id"], "adp_joto")

    def test_profile_selects_its_own_image_fallback(self) -> None:
        module_config = build_module_config(self.config, "workbuddy_joto")
        query = build_search_query(
            {"title": "WorkBuddy enterprise rollout"},
            {"cover_prompt": ""},
            module_config["images"],
        )
        self.assertEqual(query, "business team office")
        self.assertNotIn("fashion", query.lower())

    def test_fasium_supports_legacy_api_key(self) -> None:
        module_config = build_module_config(self.config, "fasium")
        with patch.dict(os.environ, {"DIFY_API_KEY": "legacy-key"}, clear=True):
            client = DifyClient.from_config(module_config["dify"])
        self.assertEqual(client.api_key, "legacy-key")

    def test_partner_prompt_contains_approved_boundary(self) -> None:
        module_config = build_module_config(self.config, "workbuddy_joto")
        prompt = build_dify_topic_input(
            {"title": "企业 AI 办公如何落地", "source_title": "AI 办公新趋势"},
            module_config,
        )
        self.assertIn("已批准的事实边界", prompt)
        self.assertIn("不得虚构合作级别", prompt)
        self.assertIn("https://cloud.tencent.com/product/workbuddy", prompt)
        self.assertIn("搜狐版本禁止出现任何网址", prompt)
        self.assertIn("未经权威来源核验的面试传闻", prompt)

    def test_sohu_content_removes_all_link_formats(self) -> None:
        content = (
            "访问 [WorkBuddy](https://example.com/product) 或 "
            "<a href=\"https://www.jotoai.com/\">JOTO 官网</a>，"
            "也可查看 https://cloud.tencent.com/demo 和 jotoai.com/path。"
        )
        cleaned = sanitize_sohu_content(content)
        self.assertIn("WorkBuddy", cleaned)
        self.assertIn("JOTO 官网", cleaned)
        self.assertNotIn("http", cleaned)
        self.assertNotIn("example.com", cleaned)
        self.assertNotIn("jotoai.com", cleaned)

    def test_partner_job_and_metadata_are_publishable_without_review(self) -> None:
        module_config = build_module_config(self.config, "dify_joto")
        topic = {"title": "Dify 企业工作流", "source_title": "AI 工作流"}
        result = {
            "zhihu": "# 知乎标题\n\n完整正文内容。" * 10,
            "csdn": "# CSDN 标题\n\n完整正文内容。" * 10,
            "sohu": "# 搜狐标题\n\n完整正文内容。" * 10,
            "cover_prompt": "enterprise AI workflow office",
            "raw_response": {},
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            job = create_job(root / "data", topic, module_config["active_module"])
            package = save_topic_result(root / "outputs" / "dify_joto", topic, result, module_config, job["id"])
            metadata = json.loads((package / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(job["review_status"], "not_required")
        self.assertEqual(metadata["module_id"], "dify_joto")
        self.assertEqual(metadata["review_status"], "not_required")


if __name__ == "__main__":
    unittest.main()
