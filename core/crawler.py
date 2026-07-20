from __future__ import annotations

import logging
from typing import Any

from collectors import CSDNCollector, JuejinCollector, ZhihuCollector


LOGGER = logging.getLogger(__name__)


def collect_hot_items(config: dict[str, Any]) -> list[dict[str, Any]]:
    collectors_config = config["collectors"]
    sources_config = collectors_config.get("sources", {})

    collector_classes = {
        "zhihu": ZhihuCollector,
        "csdn": CSDNCollector,
        "juejin": JuejinCollector,
    }

    items: list[dict[str, Any]] = []
    for source_name, collector_class in collector_classes.items():
        source_config = sources_config.get(source_name, {})
        if not source_config.get("enabled", True):
            LOGGER.info("collector skipped: %s", source_name)
            continue

        collector = collector_class(
            url=source_config["hot_url"],
            fallback_url=source_config.get("fallback_url"),
            timeout=collectors_config.get("timeout_seconds", 15),
            user_agent=collectors_config.get("user_agent", ""),
            max_items=collectors_config.get("max_items_per_source", 20),
        )
        try:
            source_items = collector.collect()
        except Exception as exc:
            LOGGER.warning("%s collect failed: %s", source_name, exc)
            continue

        LOGGER.info("collected %s items from %s", len(source_items), source_name)
        items.extend(source_items)

    return items
