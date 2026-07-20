from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any


def create_job(
    data_dir: Path,
    topic: dict[str, Any],
    module: dict[str, Any] | None = None,
) -> dict[str, Any]:
    module = module or {"id": "fasium", "name": "FasiumAI", "workflow_name": "FasiumAI GEO Workflow"}
    job = {
        "id": datetime.now().strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:8],
        "status": "queued",
        "topic": topic,
        "module_id": module.get("id", "fasium"),
        "module_name": module.get("name", "FasiumAI"),
        "workflow_name": module.get("workflow_name", ""),
        "review_status": "pending" if module.get("requires_review", False) else "not_required",
        "partnership_claims": [],
        "publish_status": {"zhihu": "idle", "csdn": "idle", "sohu": "idle"},
        "outputs": {},
        "output_dir": "",
        "error": "",
        "created_at": now(),
        "started_at": "",
        "finished_at": "",
    }
    save_job(data_dir, job)
    return job


def update_job(data_dir: Path, job: dict[str, Any], **changes: Any) -> dict[str, Any]:
    job.update(changes)
    save_job(data_dir, job)
    return job


def save_job(data_dir: Path, job: dict[str, Any]) -> Path:
    jobs_dir = data_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    path = jobs_dir / f"{job['id']}.json"
    path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    append_job_event(data_dir, job)
    return path


def append_job_event(data_dir: Path, job: dict[str, Any]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    event_path = data_dir / "jobs.jsonl"
    event = {
        "id": job.get("id"),
        "status": job.get("status"),
        "module_id": job.get("module_id", "fasium"),
        "module_name": job.get("module_name", "FasiumAI"),
        "review_status": job.get("review_status", "not_required"),
        "topic": {
            "title": job.get("topic", {}).get("title"),
            "source_title": job.get("topic", {}).get("source_title"),
            "source": job.get("topic", {}).get("source"),
        },
        "output_dir": job.get("output_dir", ""),
        "error": job.get("error", ""),
        "logged_at": now(),
    }
    with event_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False) + "\n")


def load_job(data_dir: Path, job_id: str) -> dict[str, Any] | None:
    path = data_dir / "jobs" / f"{job_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_job_events(data_dir: Path, limit: int | None = None) -> list[dict[str, Any]]:
    event_path = data_dir / "jobs.jsonl"
    if not event_path.exists():
        return []

    events: list[dict[str, Any]] = []
    for line in event_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if limit is not None:
        return events[-limit:]
    return events


def load_recent_jobs(data_dir: Path, limit: int = 10) -> list[dict[str, Any]]:
    jobs_dir = data_dir / "jobs"
    if not jobs_dir.exists():
        return []

    jobs: list[dict[str, Any]] = []
    for path in sorted(jobs_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            jobs.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
        if len(jobs) >= limit:
            break
    return jobs


def summarize_jobs_today(data_dir: Path) -> dict[str, int]:
    today = date.today().isoformat()
    jobs_dir = data_dir / "jobs"
    summary = {"total": 0, "success": 0, "failed": 0, "running": 0, "queued": 0}
    if not jobs_dir.exists():
        return summary

    for path in jobs_dir.glob("*.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        created_at = str(job.get("created_at", ""))
        if not created_at.startswith(today):
            continue

        summary["total"] += 1
        status = str(job.get("status", ""))
        if status == "generated":
            summary["success"] += 1
        elif status == "failed":
            summary["failed"] += 1
        elif status in summary:
            summary[status] += 1
    return summary
