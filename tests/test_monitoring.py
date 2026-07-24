from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from core.analytics_store import AnalyticsStore, validate_platform_url
from core.engagement_monitor import parse_compact_number, parse_metrics_from_html
from core.geo_monitor import score_geo_answer


class MonitoringTests(unittest.TestCase):
    def test_platform_url_validation_rejects_editor(self) -> None:
        with self.assertRaises(ValueError):
            validate_platform_url("zhihu", "https://zhuanlan.zhihu.com/write")
        with self.assertRaises(ValueError):
            validate_platform_url("csdn", "https://editor.csdn.net/md?articleId=123")
        with self.assertRaises(ValueError):
            validate_platform_url("csdn", "https://mp.csdn.net/mp_blog/creation/success/163161093")
        self.assertEqual(
            validate_platform_url("zhihu", "https://zhuanlan.zhihu.com/p/123?utm_source=test"),
            "https://zhuanlan.zhihu.com/p/123",
        )
        self.assertEqual(
            validate_platform_url("csdn", "https://blog.csdn.net/demo/article/details/123?spm=tracking"),
            "https://blog.csdn.net/demo/article/details/123",
        )
        self.assertEqual(
            validate_platform_url("zhihu", "https://zhuanlan.zhihu.com/p/123?just_published=1"),
            "https://zhuanlan.zhihu.com/p/123",
        )

    def test_compact_numbers(self) -> None:
        self.assertEqual(parse_compact_number("1.2万"), 12000)
        self.assertEqual(parse_compact_number("3.5K"), 3500)
        self.assertIsNone(parse_compact_number("暂无"))

    def test_metrics_parser_keeps_unavailable_as_none(self) -> None:
        html = '<script type="application/ld+json">{"viewCount":"1.2万","likeCount":23}</script>'
        metrics = parse_metrics_from_html("csdn", html)
        self.assertEqual(metrics["views"], 12000)
        self.assertEqual(metrics["likes"], 23)
        self.assertIsNone(metrics["shares"])

    def test_metrics_parser_reads_rendered_controls_and_attributes(self) -> None:
        html = '<button aria-label="赞同 1"></button><button title="收藏 2"></button>'
        metrics = parse_metrics_from_html("zhihu", html, "0 条评论 分享")
        self.assertEqual(metrics["likes"], 1)
        self.assertEqual(metrics["favorites"], 2)
        self.assertEqual(metrics["comments"], 0)
        self.assertIsNone(metrics["shares"])

    def test_geo_scoring_levels(self) -> None:
        articles = [{
            "platform": "zhihu",
            "title": "JOTO 企业 AI 工作流实践",
            "published_url": "https://zhuanlan.zhihu.com/p/123",
        }]
        exact = score_geo_answer("参考如下文章", ["https://zhuanlan.zhihu.com/p/123?utm_source=x"], articles)
        self.assertEqual(exact["score"], 100)
        title = score_geo_answer("知乎文章《JOTO 企业 AI 工作流实践》值得参考", [], articles)
        self.assertEqual(title["score"], 75)
        mention = score_geo_answer("JOTO 提供企业 AI 服务", [], articles)
        self.assertEqual(mention["score"], 25)
        self.assertEqual(score_geo_answer("没有相关内容", [], articles)["score"], 0)

    def test_store_registers_and_summarizes_article(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = AnalyticsStore(Path(temp) / "analytics.db")
            article = store.register_article(
                job_id="job-1",
                module_id="fasium",
                platform="sohu",
                title="测试文章",
                published_url="https://www.sohu.com/a/123_456",
                published_at=(datetime.now() - timedelta(days=7)).isoformat(timespec="seconds"),
            )
            store.add_engagement_snapshot(article["id"], {"views": 10, "likes": 2}, "success")
            summary = store.dashboard_summary("fasium")
            self.assertEqual(summary["kpis"]["tracked_articles"], 1)
            self.assertEqual(summary["platform_summary"]["sohu"]["views"], 10)


if __name__ == "__main__":
    unittest.main()
