from __future__ import annotations

from copy import deepcopy
from typing import Any


DEFAULT_MODULE_ID = "fasium"


def list_content_profiles(config: dict[str, Any], enabled_only: bool = False) -> list[dict[str, Any]]:
    profiles = config.get("content_profiles", {})
    result: list[dict[str, Any]] = []
    for module_id, profile in profiles.items():
        item = deepcopy(profile or {})
        item["id"] = module_id
        item.setdefault("name", module_id)
        item.setdefault("short_name", item["name"])
        item.setdefault("enabled", True)
        item.setdefault("requires_review", module_id != DEFAULT_MODULE_ID)
        item.setdefault("workflow_name", item["name"])
        if enabled_only and not item["enabled"]:
            continue
        result.append(item)
    return result


def get_content_profile(config: dict[str, Any], module_id: str) -> dict[str, Any]:
    for profile in list_content_profiles(config):
        if profile["id"] == module_id:
            return profile
    available = ", ".join(profile["id"] for profile in list_content_profiles(config))
    raise ValueError(f"unknown content module: {module_id}; available: {available}")


def build_module_config(config: dict[str, Any], module_id: str) -> dict[str, Any]:
    profile = get_content_profile(config, module_id)
    if not profile.get("enabled", True):
        raise ValueError(f"content module is disabled: {module_id}")

    module_config = deepcopy(config)
    module_config["active_module"] = {
        key: deepcopy(value)
        for key, value in profile.items()
        if key not in {"topic_filter", "content", "dify"}
    }
    module_config["topic_filter"] = deep_merge(config.get("topic_filter", {}), profile.get("topic_filter", {}))
    module_config["content"] = deep_merge(config.get("content", {}), profile.get("content", {}))
    module_config["dify"] = deep_merge(config.get("dify", {}), profile.get("dify", {}))
    module_config["images"] = deep_merge(config.get("images", {}), profile.get("images", {}))
    module_config["dify"]["api_key_env"] = profile.get("api_key_env", "DIFY_API_KEY")
    module_config["dify"]["workflow_name"] = profile.get("workflow_name", profile["name"])
    return module_config


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def module_output_dir(output_root: Any, module_id: str) -> Any:
    return output_root / module_id
