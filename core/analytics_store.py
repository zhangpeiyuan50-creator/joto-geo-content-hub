from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


PLATFORM_HOSTS = {
    "zhihu": ("zhihu.com",),
    "csdn": ("csdn.net",),
    "sohu": ("sohu.com",),
}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def normalize_url(value: str) -> str:
    value = value.strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("文章链接必须是完整的 http 或 https URL")
    tracking_keys = {
        "spm", "from", "source", "sourcefrom", "just_published", "share_token",
        "share_source", "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    }
    query = [
        (key, val)
        for key, val in parse_qsl(parsed.query)
        if key.lower() not in tracking_keys and not key.lower().startswith("utm_")
    ]
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", urlencode(query), ""))


def validate_platform_url(platform: str, value: str) -> str:
    if platform not in PLATFORM_HOSTS:
        raise ValueError(f"不支持的平台: {platform}")
    normalized = normalize_url(value)
    host = urlparse(normalized).netloc.split(":", 1)[0]
    if not any(host == allowed or host.endswith(f".{allowed}") for allowed in PLATFORM_HOSTS[platform]):
        raise ValueError(f"{platform} 文章链接域名不正确")
    lowered = normalized.lower()
    editor_markers = (
        "/write",
        "/editor",
        "articleid=",
        "/news/add",
        "/mpfe/",
        "/mp_blog/creation/",
    )
    if any(marker in lowered for marker in editor_markers):
        raise ValueError("检测到编辑器地址，请填写发布后的公开文章链接")
    return normalized


def extract_platform_article_id(platform: str, value: str) -> str:
    path = urlparse(value).path
    patterns = {
        "zhihu": (r"/p/(\d+)",),
        "csdn": (r"/article/details/(\d+)",),
        "sohu": (r"/a/(\d+)_?\d*", r"/(\d+)\.shtml"),
    }
    import re

    for pattern in patterns.get(platform, ()):
        match = re.search(pattern, path, flags=re.I)
        if match:
            return match.group(1)
    return path.rstrip("/").split("/")[-1]


class AnalyticsStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connection() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS published_articles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    module_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    title TEXT NOT NULL,
                    published_url TEXT NOT NULL,
                    platform_article_id TEXT,
                    published_at TEXT NOT NULL,
                    capture_method TEXT NOT NULL,
                    monitoring_status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(job_id, platform)
                );
                CREATE TABLE IF NOT EXISTS engagement_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    article_id INTEGER NOT NULL,
                    captured_at TEXT NOT NULL,
                    views INTEGER,
                    likes INTEGER,
                    comments INTEGER,
                    favorites INTEGER,
                    shares INTEGER,
                    reposts INTEGER,
                    status TEXT NOT NULL,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(article_id) REFERENCES published_articles(id)
                );
                CREATE INDEX IF NOT EXISTS idx_engagement_article_time
                    ON engagement_snapshots(article_id, captured_at DESC);
                CREATE TABLE IF NOT EXISTS geo_checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    module_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    due_day INTEGER NOT NULL DEFAULT 0,
                    query_type TEXT NOT NULL,
                    query_text TEXT NOT NULL,
                    checked_at TEXT NOT NULL,
                    answer_text TEXT NOT NULL DEFAULT '',
                    citations_json TEXT NOT NULL DEFAULT '[]',
                    matched_url TEXT NOT NULL DEFAULT '',
                    match_type TEXT NOT NULL DEFAULT 'absent',
                    score INTEGER NOT NULL DEFAULT 0,
                    screenshot_path TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    UNIQUE(job_id, provider, due_day, query_type)
                );
                CREATE TABLE IF NOT EXISTS monitoring_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_type TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    processed INTEGER NOT NULL DEFAULT 0,
                    succeeded INTEGER NOT NULL DEFAULT 0,
                    failed INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS monitoring_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alert_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    job_id TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL DEFAULT '',
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    dedupe_key TEXT NOT NULL UNIQUE
                );
                """
            )

    def register_article(
        self,
        *,
        job_id: str,
        module_id: str,
        platform: str,
        title: str,
        published_url: str,
        published_at: str | None = None,
        capture_method: str = "manual",
    ) -> dict[str, Any]:
        url = validate_platform_url(platform, published_url)
        timestamp = now()
        with self.connection() as db:
            db.execute(
                """
                INSERT INTO published_articles (
                    job_id, module_id, platform, title, published_url, platform_article_id,
                    published_at, capture_method, monitoring_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                ON CONFLICT(job_id, platform) DO UPDATE SET
                    title=excluded.title,
                    published_url=excluded.published_url,
                    platform_article_id=excluded.platform_article_id,
                    published_at=excluded.published_at,
                    capture_method=excluded.capture_method,
                    monitoring_status='active',
                    updated_at=excluded.updated_at
                """,
                (
                    job_id,
                    module_id,
                    platform,
                    title.strip() or job_id,
                    url,
                    extract_platform_article_id(platform, url),
                    published_at or timestamp,
                    capture_method,
                    timestamp,
                    timestamp,
                ),
            )
        self.resolve_alert(f"published-url:{job_id}:{platform}")
        return self.article(job_id, platform) or {}

    def article(self, job_id: str, platform: str) -> dict[str, Any] | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM published_articles WHERE job_id=? AND platform=?",
                (job_id, platform),
            ).fetchone()
        return dict(row) if row else None

    def articles_for_job(self, job_id: str) -> list[dict[str, Any]]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM published_articles WHERE job_id=? ORDER BY platform",
                (job_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def active_articles(self) -> list[dict[str, Any]]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM published_articles WHERE monitoring_status='active' ORDER BY published_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def article_due_for_engagement(self, article: dict[str, Any], current: datetime | None = None) -> bool:
        current = current or datetime.now()
        published = datetime.fromisoformat(str(article["published_at"]))
        age_days = max(0, (current.date() - published.date()).days)
        with self.connection() as db:
            row = db.execute(
                "SELECT captured_at FROM engagement_snapshots WHERE article_id=? AND status='success' ORDER BY captured_at DESC LIMIT 1",
                (article["id"],),
            ).fetchone()
        if not row:
            return True
        last = datetime.fromisoformat(row["captured_at"])
        return last.date() < current.date() if age_days <= 30 else (current - last) >= timedelta(days=7)

    def add_engagement_snapshot(self, article_id: int, metrics: dict[str, Any], status: str, error: str = "") -> None:
        fields = ("views", "likes", "comments", "favorites", "shares", "reposts")
        with self.connection() as db:
            db.execute(
                """
                INSERT INTO engagement_snapshots (
                    article_id, captured_at, views, likes, comments, favorites, shares,
                    reposts, status, raw_json, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    article_id,
                    now(),
                    *(metrics.get(field) for field in fields),
                    status,
                    json.dumps(metrics.get("raw", {}), ensure_ascii=False),
                    error,
                ),
            )

    def start_run(self, run_type: str) -> int:
        with self.connection() as db:
            cursor = db.execute(
                "INSERT INTO monitoring_runs(run_type, started_at, status) VALUES (?, ?, 'running')",
                (run_type, now()),
            )
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int, *, processed: int, succeeded: int, failed: int, error: str = "") -> None:
        status = "success" if failed == 0 else ("partial" if succeeded else "failed")
        with self.connection() as db:
            db.execute(
                "UPDATE monitoring_runs SET finished_at=?, status=?, processed=?, succeeded=?, failed=?, error=? WHERE id=?",
                (now(), status, processed, succeeded, failed, error, run_id),
            )

    def add_alert(
        self,
        alert_type: str,
        message: str,
        *,
        severity: str = "warning",
        job_id: str = "",
        provider: str = "",
        dedupe_key: str | None = None,
    ) -> None:
        key = dedupe_key or f"{alert_type}:{job_id}:{provider}:{message}"
        with self.connection() as db:
            db.execute(
                """
                INSERT INTO monitoring_alerts(alert_type, severity, job_id, provider, message, created_at, dedupe_key)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(dedupe_key) DO UPDATE SET
                    severity=excluded.severity, message=excluded.message, created_at=excluded.created_at, resolved_at=NULL
                """,
                (alert_type, severity, job_id, provider, message, now(), key),
            )

    def resolve_alert(self, dedupe_key: str) -> None:
        with self.connection() as db:
            db.execute("UPDATE monitoring_alerts SET resolved_at=? WHERE dedupe_key=?", (now(), dedupe_key))

    def geo_due_jobs(self, due_days: list[int], current: datetime | None = None) -> list[dict[str, Any]]:
        current = current or datetime.now()
        with self.connection() as db:
            rows = db.execute(
                """
                SELECT job_id, module_id, MIN(published_at) AS published_at,
                       GROUP_CONCAT(title, ' | ') AS titles
                FROM published_articles WHERE monitoring_status='active'
                GROUP BY job_id, module_id
                """
            ).fetchall()
        due: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            age = (current.date() - datetime.fromisoformat(item["published_at"]).date()).days
            eligible = [day for day in due_days if age >= day]
            if eligible:
                item["due_day"] = max(eligible)
                due.append(item)
        return due

    def geo_check_exists(self, job_id: str, provider: str, due_day: int, query_type: str) -> bool:
        with self.connection() as db:
            row = db.execute(
                "SELECT 1 FROM geo_checks WHERE job_id=? AND provider=? AND due_day=? AND query_type=? AND status='success'",
                (job_id, provider, due_day, query_type),
            ).fetchone()
        return bool(row)

    def add_geo_check(self, payload: dict[str, Any]) -> None:
        with self.connection() as db:
            db.execute(
                """
                INSERT INTO geo_checks (
                    job_id, module_id, provider, due_day, query_type, query_text, checked_at,
                    answer_text, citations_json, matched_url, match_type, score,
                    screenshot_path, status, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id, provider, due_day, query_type) DO UPDATE SET
                    checked_at=excluded.checked_at, answer_text=excluded.answer_text,
                    citations_json=excluded.citations_json, matched_url=excluded.matched_url,
                    match_type=excluded.match_type, score=excluded.score,
                    screenshot_path=excluded.screenshot_path, status=excluded.status, error=excluded.error
                """,
                (
                    payload["job_id"], payload["module_id"], payload["provider"], payload.get("due_day", 0),
                    payload["query_type"], payload["query_text"], payload.get("checked_at", now()),
                    payload.get("answer_text", ""), json.dumps(payload.get("citations", []), ensure_ascii=False),
                    payload.get("matched_url", ""), payload.get("match_type", "absent"), payload.get("score", 0),
                    payload.get("screenshot_path", ""), payload.get("status", "failed"), payload.get("error", ""),
                ),
            )

    def analytics_for_job(self, job_id: str) -> dict[str, Any]:
        articles = self.articles_for_job(job_id)
        with self.connection() as db:
            snapshots = db.execute(
                """
                SELECT e.*, a.platform FROM engagement_snapshots e
                JOIN published_articles a ON a.id=e.article_id
                WHERE a.job_id=? ORDER BY e.captured_at DESC
                """,
                (job_id,),
            ).fetchall()
            checks = db.execute(
                "SELECT * FROM geo_checks WHERE job_id=? ORDER BY checked_at DESC",
                (job_id,),
            ).fetchall()
        return {
            "articles": articles,
            "engagement": [dict(row) for row in snapshots],
            "geo_checks": [self._decode_geo_row(row) for row in checks],
        }

    def dashboard_summary(self, module_id: str = "all") -> dict[str, Any]:
        module_clause = "" if module_id == "all" else " WHERE module_id=?"
        params: tuple[Any, ...] = () if module_id == "all" else (module_id,)
        with self.connection() as db:
            articles = db.execute(f"SELECT COUNT(*) count FROM published_articles{module_clause}", params).fetchone()["count"]
            alerts = db.execute("SELECT COUNT(*) count FROM monitoring_alerts WHERE resolved_at IS NULL").fetchone()["count"]
            checks = db.execute(
                f"SELECT score, match_type FROM geo_checks{module_clause.replace('WHERE', 'WHERE') if module_clause else ''}"
                + (" AND status='success'" if module_clause else " WHERE status='success'"),
                params,
            ).fetchall()
            latest = db.execute(
                """
                SELECT a.platform, a.job_id, a.module_id, a.title, a.published_url,
                       e.views, e.likes, e.comments, e.favorites, e.shares, e.reposts,
                       e.captured_at, e.status
                FROM published_articles a
                LEFT JOIN engagement_snapshots e ON e.id=(
                    SELECT id FROM engagement_snapshots
                    WHERE article_id=a.id AND status='success'
                    ORDER BY captured_at DESC LIMIT 1
                )
                """ + (" WHERE a.module_id=?" if module_id != "all" else "") + " ORDER BY a.published_at DESC LIMIT 30",
                params,
            ).fetchall()
            alert_rows = db.execute(
                "SELECT * FROM monitoring_alerts WHERE resolved_at IS NULL ORDER BY created_at DESC LIMIT 12"
            ).fetchall()
            recent_geo = db.execute(
                "SELECT * FROM geo_checks " + ("WHERE module_id=? " if module_id != "all" else "") + "ORDER BY checked_at DESC LIMIT 20",
                params,
            ).fetchall()
            article_ids = [int(row["id"]) for row in db.execute(
                "SELECT id FROM published_articles" + (" WHERE module_id=?" if module_id != "all" else ""),
                params,
            ).fetchall()]
            recent_snapshots: dict[int, list[dict[str, Any]]] = {}
            for article_id in article_ids:
                rows = db.execute(
                    "SELECT * FROM engagement_snapshots WHERE article_id=? AND status='success' ORDER BY captured_at DESC LIMIT 2",
                    (article_id,),
                ).fetchall()
                recent_snapshots[article_id] = [dict(row) for row in rows]
        scores = [int(row["score"]) for row in checks]
        exact = sum(row["match_type"] == "exact_url" for row in checks)
        today_text = date.today().isoformat()
        today_views_growth = 0
        interaction_growth = 0
        for snapshots in recent_snapshots.values():
            if not snapshots or not str(snapshots[0]["captured_at"]).startswith(today_text):
                continue
            latest_snapshot = snapshots[0]
            previous = snapshots[1] if len(snapshots) > 1 else {}
            today_views_growth += max(0, int(latest_snapshot.get("views") or 0) - int(previous.get("views") or 0))
            for field in ("likes", "comments", "favorites", "shares", "reposts"):
                interaction_growth += max(0, int(latest_snapshot.get(field) or 0) - int(previous.get(field) or 0))
        platform_summary: dict[str, dict[str, int]] = {
            platform: {field: 0 for field in ("articles", "collected_articles", "views", "likes", "comments", "favorites", "shares", "reposts")}
            for platform in PLATFORM_HOSTS
        }
        for row in latest:
            summary = platform_summary[row["platform"]]
            summary["articles"] += 1
            if row["status"] == "success":
                summary["collected_articles"] += 1
            for field in ("views", "likes", "comments", "favorites", "shares", "reposts"):
                summary[field] += int(row[field] or 0)
        return {
            "kpis": {
                "tracked_articles": articles,
                "today_views_growth": today_views_growth,
                "interaction_growth": interaction_growth,
                "exact_citation_rate": round(exact / len(checks) * 100) if checks else 0,
                "geo_average_score": round(sum(scores) / len(scores)) if scores else 0,
                "open_alerts": alerts,
            },
            "articles": [dict(row) for row in latest],
            "platform_summary": platform_summary,
            "geo_checks": [self._decode_geo_row(row) for row in recent_geo],
            "alerts": [dict(row) for row in alert_rows],
        }

    @staticmethod
    def _decode_geo_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        try:
            item["citations"] = json.loads(item.pop("citations_json", "[]"))
        except json.JSONDecodeError:
            item["citations"] = []
        return item
