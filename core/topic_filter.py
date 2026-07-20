from __future__ import annotations

import random
from datetime import date
from typing import Any


def select_topics(
    items: list[dict[str, Any]],
    config: dict[str, Any],
    history_keys: set[str] | None = None,
) -> list[dict[str, Any]]:
    history_keys = history_keys or set()
    core_keywords = [keyword.lower() for keyword in config.get("core_keywords", [])]
    bridge_keywords = [keyword.lower() for keyword in config.get("bridge_keywords", [])]
    exclude_keywords = [keyword.lower() for keyword in config.get("exclude_keywords", [])]
    min_score = int(config.get("min_score", 2))
    min_bridge_score = int(config.get("min_bridge_score", 1))
    max_topics = int(config.get("max_topics", 1))
    candidate_pool_size = int(config.get("candidate_pool_size", 12))

    scored_topics: list[dict[str, Any]] = []
    seen_rewritten_topics: set[str] = set()

    for item in items:
        title = item.get("title", "").strip()
        if not title:
            continue

        text = f"{title} {item.get('summary', '')}".lower()
        if any(keyword in text for keyword in exclude_keywords):
            continue

        matched_core_keywords = [keyword for keyword in core_keywords if keyword in text]
        matched_bridge_keywords = [keyword for keyword in bridge_keywords if keyword in text]
        score = len(matched_core_keywords) * 2 + len(matched_bridge_keywords)
        if score < min_score or len(matched_bridge_keywords) < min_bridge_score:
            continue

        rewritten_topic = rewrite_for_profile(title, config)
        history_values = {
            title.lower(),
            rewritten_topic.lower(),
            str(item.get("url", "")).strip().lower(),
        }
        if history_values & history_keys:
            continue

        if rewritten_topic in seen_rewritten_topics:
            continue

        seen_rewritten_topics.add(rewritten_topic)
        scored_topics.append(
            {
                **item,
                "score": score,
                "source_title": title,
                "title": rewritten_topic,
                "matched_core_keywords": matched_core_keywords,
                "matched_bridge_keywords": matched_bridge_keywords,
            }
        )

    scored_topics.sort(key=lambda topic: topic["score"], reverse=True)
    selected = select_from_candidate_pool(scored_topics, max_topics, candidate_pool_size, config)
    apply_content_angles(selected, config)
    return selected


def select_from_candidate_pool(
    topics: list[dict[str, Any]],
    max_topics: int,
    candidate_pool_size: int,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    if not topics:
        return []

    pool = topics[: max(candidate_pool_size, max_topics)]
    rng = build_random(config)
    selected: list[dict[str, Any]] = []
    available = pool[:]

    while available and len(selected) < max_topics:
        weights = [max(1, int(topic.get("score", 1))) for topic in available]
        choice = rng.choices(available, weights=weights, k=1)[0]
        selected.append(choice)
        available.remove(choice)

    return selected


def apply_content_angles(topics: list[dict[str, Any]], config: dict[str, Any]) -> None:
    angles = config.get("content_angles", [])
    if not angles:
        return

    rng = build_random(config, salt="angles")
    used_names: set[str] = set()
    for topic in topics:
        available_angles = [angle for angle in angles if angle.get("name") not in used_names] or angles
        angle = rng.choice(available_angles)
        used_names.add(angle.get("name", ""))
        topic["content_angle"] = angle


def build_random(config: dict[str, Any], salt: str = "") -> random.Random:
    seed = config.get("random_seed", "daily")
    if seed == "daily":
        seed_value = f"{date.today().isoformat()}:{salt}"
    elif seed in (None, "", "none"):
        seed_value = None
    else:
        seed_value = f"{seed}:{salt}"
    return random.Random(seed_value)


def rewrite_for_profile(source_title: str, config: dict[str, Any]) -> str:
    templates = config.get("rewrite_templates", {})
    title = source_title.lower()
    suffix = str(templates.get("source_suffix", "：从「{source}」寻找新的内容切入点"))

    rewrite_groups = [
        ("agent", "agent_topic"),
        ("model", "model_topic"),
        ("tool", "tool_topic"),
        ("workflow", "workflow_topic"),
    ]

    for keyword_key, topic_key in rewrite_groups:
        keywords = [keyword.lower() for keyword in templates.get(keyword_key, [])]
        if any(keyword in title for keyword in keywords):
            return build_dynamic_topic(
                templates.get(topic_key, templates.get("default_topic", source_title)),
                source_title,
                suffix,
            )

    return build_dynamic_topic(templates.get("default_topic", source_title), source_title, suffix)


def rewrite_for_fasium(source_title: str, config: dict[str, Any]) -> str:
    """Backward-compatible alias for older imports."""
    return rewrite_for_profile(source_title, config)


def build_dynamic_topic(base_topic: str, source_title: str, suffix: str = "：从「{source}」寻找新的内容切入点") -> str:
    source_hint = compact_source_title(source_title)
    if not source_hint:
        return base_topic
    return f"{base_topic}{suffix.format(source=source_hint)}"


def compact_source_title(title: str, max_length: int = 28) -> str:
    cleaned = " ".join(title.replace("\n", " ").split())
    cleaned = cleaned.strip(" -_|，。,.!?！？")
    if len(cleaned) <= max_length:
        return cleaned
    return cleaned[:max_length].rstrip() + "..."
