from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from core.analytics_store import AnalyticsStore, validate_platform_url
from core.engagement_monitor import (
    parse_csdn_creator_rows,
    parse_compact_number,
    parse_metrics_from_html,
    parse_sohu_creator_rows,
    parse_zhihu_creator_rows,
    resolve_platform_article_title,
)
from core.geo_monitor import score_geo_answer


class MonitoringTests(unittest.TestCase):
    def test_platform_url_validation_rejects_editor(self) -> None:
        with self.assertRaises(ValueError):
            validate_platform_url("zhihu", "https://zhuanlan.zhihu.com/write")
        with self.assertRaises(ValueError):
            validate_platform_url("csdn", "https://editor.csdn.net/md?articleId=123")
        with self.assertRaises(ValueError):
            validate_platform_url("csdn", "https://mp.csdn.net/mp_blog/creation/success/163161093")
        with self.assertRaises(ValueError):
            validate_platform_url("csdn", "https://blog.csdn.net/rank/list")
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
        metrics = parse_metrics_from_html("zhihu", html, "", "0 条评论 | 分享")
        self.assertEqual(metrics["likes"], 1)
        self.assertEqual(metrics["favorites"], 2)
        self.assertEqual(metrics["comments"], 0)
        self.assertEqual(metrics["shares"], 0)

    def test_zhihu_empty_interaction_controls_are_zero(self) -> None:
        controls = "赞同 | 添加评论 | 收藏 | 喜欢 | 分享"
        metrics = parse_metrics_from_html("zhihu", "<html></html>", "", controls)
        self.assertIsNone(metrics["views"])
        self.assertEqual(metrics["likes"], 0)
        self.assertEqual(metrics["comments"], 0)
        self.assertEqual(metrics["favorites"], 0)
        self.assertEqual(metrics["shares"], 0)

    def test_article_date_is_not_an_interaction_count(self) -> None:
        body = "编辑于 2026-07-24\n这篇文章讨论如何分享经验与处理评论。"
        metrics = parse_metrics_from_html("csdn", "<html></html>", body, "")
        self.assertIsNone(metrics["comments"])
        self.assertIsNone(metrics["shares"])

    def test_csdn_article_controls_override_author_profile_totals(self) -> None:
        html = '<script type="application/ld+json">{"viewCount":308,"likeCount":98}</script>'
        controls = "原创 23 | 点赞 176 | 收藏 98 | 粉丝 0 | 点赞 2 | 收藏 1 | 评论 0 | 分享"
        metrics = parse_metrics_from_html("csdn", html, "", controls)
        self.assertEqual(metrics["views"], 308)
        self.assertEqual(metrics["likes"], 2)
        self.assertEqual(metrics["favorites"], 1)
        self.assertEqual(metrics["comments"], 0)
        self.assertEqual(metrics["shares"], 0)

    def test_zhihu_creator_row_matches_title_and_metrics(self) -> None:
        rows = [
            {
                "text": "另一篇文章\n2026-07-24\n13\n1 赞同 · 0 评论 · 0 喜欢 · 1 收藏 · 0 分享\n详细分析",
                "cells": [
                    "另一篇文章\n2026-07-24",
                    "13",
                    "1 赞同 · 0 评论 · 0 喜欢 · 1 收藏 · 0 分享",
                    "详细分析",
                ],
            },
            {
                "text": "阿里面试官被问 Agent 项目时擦汗，戳中了企业 AI 落地的哪块软肋？\n2026-07-24\n5\n0 赞同 · 0 评论 · 0 喜欢 · 0 收藏 · 0 分享\n详细分析",
                "cells": [
                    "阿里面试官被问 Agent 项目时擦汗，戳中了企业 AI 落地的哪块软肋？\n2026-07-24",
                    "5",
                    "0 赞同 · 0 评论 · 0 喜欢 · 0 收藏 · 0 分享",
                    "详细分析",
                ],
            },
        ]
        metrics = parse_zhihu_creator_rows(
            rows,
            "阿里面试官被问 Agent 项目时擦汗，戳中了企业 AI 落地的哪块软肋？",
        )
        self.assertIsNotNone(metrics)
        self.assertEqual(metrics["views"], 5)
        self.assertEqual(metrics["likes"], 0)
        self.assertEqual(metrics["comments"], 0)
        self.assertEqual(metrics["favorites"], 0)
        self.assertEqual(metrics["shares"], 0)
        self.assertEqual(metrics["raw"]["parser"], "zhihu_creator_analytics")

    def test_zhihu_creator_row_prefers_article_id_when_published_title_changed(self) -> None:
        rows = [{
            "text": "发布后人工修改过的标题\n2026-07-24\n5\n0 赞同 · 0 评论 · 0 喜欢 · 0 收藏 · 0 分享",
            "cells": [
                "发布后人工修改过的标题\n2026-07-24",
                "5",
                "0 赞同 · 0 评论 · 0 喜欢 · 0 收藏 · 0 分享",
            ],
            "links": [{"href": "https://zhuanlan.zhihu.com/p/2063991086327112992"}],
        }]
        metrics = parse_zhihu_creator_rows(
            rows,
            "本地生成时完全不同的标题",
            "https://zhuanlan.zhihu.com/p/2063991086327112992",
        )
        self.assertIsNotNone(metrics)
        self.assertEqual(metrics["views"], 5)
        self.assertEqual(metrics["likes"], 0)
        self.assertEqual(metrics["raw"]["match_method"], "article_id")

    def test_zhihu_creator_row_does_not_treat_date_as_views(self) -> None:
        rows = [{
            "text": "测试文章\n2026-07-24\n0 赞同 · 0 评论 · 0 喜欢 · 0 收藏 · 0 分享",
            "cells": [
                "测试文章\n2026-07-24",
                "0 赞同 · 0 评论 · 0 喜欢 · 0 收藏 · 0 分享",
            ],
        }]
        metrics = parse_zhihu_creator_rows(rows, "测试文章")
        self.assertIsNotNone(metrics)
        self.assertIsNone(metrics["views"])

    def test_csdn_creator_row_uses_article_columns(self) -> None:
        rows = [{
            "title": "企业 AI 办公落地：从 WorkBuddy 到实施服务",
            "values": ["453", "6", "0", "4"],
            "links": ["https://editor.csdn.net/md/?articleId=163161093"],
        }]
        metrics = parse_csdn_creator_rows(
            rows,
            "本地标题可以不同",
            "https://blog.csdn.net/demo/article/details/163161093",
        )
        self.assertIsNotNone(metrics)
        self.assertEqual(metrics["views"], 453)
        self.assertEqual(metrics["likes"], 6)
        self.assertEqual(metrics["comments"], 0)
        self.assertEqual(metrics["favorites"], 4)
        self.assertIsNone(metrics["shares"])

    def test_sohu_creator_row_maps_single_article_columns(self) -> None:
        rows = [{
            "title": "AI 办公进入深水区：企业如何让智能助手真正干活",
            "values": ["27", "12", "2", "1", "3", "0"],
            "links": [],
        }]
        metrics = parse_sohu_creator_rows(
            rows,
            "AI 办公进入深水区：企业如何让智能助手真正干活",
        )
        self.assertIsNotNone(metrics)
        self.assertEqual(metrics["views"], 27)
        self.assertEqual(metrics["likes"], 2)
        self.assertEqual(metrics["comments"], 1)
        self.assertEqual(metrics["shares"], 3)
        self.assertIsNone(metrics["favorites"])
        self.assertEqual(metrics["raw"]["visits_or_plays"], 12)

    def test_sohu_creator_row_falls_back_to_unique_publish_date(self) -> None:
        rows = [{
            "title": "发布后人工修改过的搜狐标题",
            "published_date": "2026-07-29",
            "values": ["30", "13", "0", "0", "0", "0"],
            "links": [],
        }]
        metrics = parse_sohu_creator_rows(
            rows,
            "本地生成标题完全不同",
            "https://www.sohu.com/a/1056243518_122938899",
            "2026-07-29T16:37:02",
        )
        self.assertIsNotNone(metrics)
        self.assertEqual(metrics["views"], 30)
        self.assertEqual(metrics["raw"]["match_method"], "published_date")

    def test_monitor_uses_platform_title_from_content_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            article_dir = root / "outputs" / "workbuddy_joto" / "job_job-1" / "zhihu"
            article_dir.mkdir(parents=True)
            (article_dir / "zhihu_rich.html").write_text(
                "<html><head><title>知乎富文本</title></head>"
                "<body><h1>知乎平台的真实文章标题</h1></body></html>",
                encoding="utf-8",
            )
            title = resolve_platform_article_title(
                {
                    "job_id": "job-1",
                    "module_id": "workbuddy_joto",
                    "platform": "zhihu",
                    "title": "本地 Job 小标题",
                },
                {"project": {"output_dir": "outputs"}},
                root,
            )
            self.assertEqual(title, "知乎平台的真实文章标题")

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
            self.assertIsNotNone(summary["platform_summary"]["sohu"]["last_captured_at"])
            self.assertIsNone(summary["platform_summary"]["zhihu"]["last_captured_at"])

    def test_store_deletes_article_and_resolves_alert(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = AnalyticsStore(Path(temp) / "analytics.db")
            article = store.register_article(
                job_id="job-delete",
                module_id="fasium",
                platform="zhihu",
                title="测试文章",
                published_url="https://zhuanlan.zhihu.com/p/123",
            )
            store.add_engagement_snapshot(article["id"], {"likes": 0}, "success")
            store.add_alert(
                "engagement_failed",
                "采集失败",
                job_id="job-delete",
                provider="zhihu",
                dedupe_key=f"engagement:{article['id']}",
            )
            self.assertTrue(store.delete_article("job-delete", "zhihu"))
            self.assertEqual(store.dashboard_summary()["kpis"]["tracked_articles"], 0)

    def test_manual_engagement_preserves_views_and_updates_interactions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = AnalyticsStore(Path(temp) / "analytics.db")
            article = store.register_article(
                job_id="job-manual",
                module_id="fasium",
                platform="csdn",
                title="测试文章",
                published_url="https://blog.csdn.net/demo/article/details/123",
            )
            store.add_engagement_snapshot(
                article["id"],
                {"views": 308, "likes": 0, "comments": 0, "favorites": 0, "shares": 0},
                "success",
            )
            saved = store.add_manual_engagement_snapshot(
                "job-manual",
                "csdn",
                {"likes": 2, "favorites": 1, "comments": 0, "shares": 0},
            )
            self.assertEqual(saved["views"], 308)
            self.assertEqual(saved["likes"], 2)
            self.assertEqual(saved["favorites"], 1)
            summary = store.dashboard_summary("fasium")["platform_summary"]["csdn"]
            self.assertEqual(summary["views"], 308)
            self.assertEqual(summary["likes"], 2)
            self.assertEqual(summary["favorites"], 1)


if __name__ == "__main__":
    unittest.main()
