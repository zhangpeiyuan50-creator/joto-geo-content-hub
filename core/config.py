from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")

    with path.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file) or {}

    required_sections = ["project", "collectors", "topic_filter", "dify", "content_profiles"]
    missing = [section for section in required_sections if section not in config]
    if missing:
        raise ValueError(f"missing config sections: {', '.join(missing)}")

    if not isinstance(config.get("content_profiles"), dict) or not config["content_profiles"]:
        raise ValueError("content_profiles must define at least one content module")

    return config
