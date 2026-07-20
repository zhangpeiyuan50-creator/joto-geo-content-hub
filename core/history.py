from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


def load_recent_history(output_dir: Path, days: int) -> list[dict[str, Any]]:
    if days <= 0 or not output_dir.exists():
        return []

    cutoff = datetime.now() - timedelta(days=days)
    records: list[dict[str, Any]] = []

    for metadata_path in output_dir.glob("**/metadata.json"):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        generated_at = _parse_datetime(metadata.get("generated_at", ""))
        if generated_at and generated_at < cutoff:
            continue

        topic = metadata.get("topic", {})
        if isinstance(topic, dict):
            records.append(topic)

    return records


def build_history_keys(records: list[dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for record in records:
        for field in ["title", "source_title", "url"]:
            value = str(record.get(field, "")).strip().lower()
            if value:
                keys.add(value)
    return keys


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
