from __future__ import annotations

import logging
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger(__name__)


def publish_zhihu(job: dict[str, Any], dry_run: bool = True) -> dict[str, Any]:
    return mock_publish(job, "zhihu", "zhihu/zhihu_rich.html", dry_run)


def publish_csdn(job: dict[str, Any], dry_run: bool = True) -> dict[str, Any]:
    return mock_publish(job, "csdn", "csdn/csdn.md", dry_run)


def publish_sohu(job: dict[str, Any], dry_run: bool = True) -> dict[str, Any]:
    return mock_publish(job, "sohu", "sohu/sohu_rich.html", dry_run)


def publish_all(job: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    publish_config = config.get("publish", {})
    dry_run = bool(publish_config.get("dry_run", True))

    results = {
        "zhihu": publish_zhihu(job, dry_run=dry_run),
        "csdn": publish_csdn(job, dry_run=dry_run),
        "sohu": publish_sohu(job, dry_run=dry_run),
    }
    LOGGER.info("publish mock finished for job %s", job.get("id"))
    return results


def mock_publish(job: dict[str, Any], platform: str, file_name: str, dry_run: bool) -> dict[str, Any]:
    output_dir = Path(str(job.get("output_dir", "")))
    content_path = output_dir / file_name if output_dir else Path(file_name)
    exists = content_path.exists()
    status = "mocked" if dry_run else "not_implemented"
    LOGGER.info(
        "publish_%s status=%s dry_run=%s file=%s exists=%s",
        platform,
        status,
        dry_run,
        content_path,
        exists,
    )
    return {
        "platform": platform,
        "status": status,
        "dry_run": dry_run,
        "file": str(content_path),
        "exists": exists,
        "message": "mock publish only; login and real posting are not implemented",
    }
