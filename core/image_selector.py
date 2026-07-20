from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests


LOGGER = logging.getLogger(__name__)
UNSPLASH_API_BASE = "https://api.unsplash.com"


def attach_unsplash_cover_image(
    topic_dir: Path,
    topic: dict[str, Any],
    result: dict[str, Any],
    config: dict[str, Any],
    output_root: Path,
) -> dict[str, Any] | None:
    image_config = config.get("images", {})
    if not image_config.get("enabled", False):
        LOGGER.info("image selection disabled")
        return None
    if image_config.get("provider", "unsplash") != "unsplash":
        LOGGER.info("image provider is not unsplash; skipped")
        return None

    access_key = os.getenv("UNSPLASH_ACCESS_KEY", "").strip()
    if not access_key:
        LOGGER.warning("UNSPLASH_ACCESS_KEY is not configured; skipped Unsplash cover image")
        return None

    query = build_search_query(topic, result, image_config)
    used_photo_ids = load_used_photo_ids(output_root)
    photo = search_photo(access_key, query, image_config, used_photo_ids)
    if not photo:
        fallback_query = str(image_config.get("fallback_query", "fashion design studio apparel workflow"))
        LOGGER.warning("no Unsplash photo found for query=%s; retrying fallback=%s", query, fallback_query)
        photo = search_photo(access_key, fallback_query, image_config, used_photo_ids)
        query = fallback_query

    if not photo:
        LOGGER.warning("no Unsplash photo found; skipped cover image")
        return None

    photo_id = str(photo["id"])
    image_url = build_download_image_url(photo, image_config)
    assets_dir = topic_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    image_path = assets_dir / "cover_image.jpg"
    download_image(image_url, image_path)
    trigger_unsplash_download(access_key, photo_id)

    metadata = build_image_metadata(photo, query, image_url, image_path)
    (assets_dir / "image_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (assets_dir / "attribution.txt").write_text(
        build_attribution_text(metadata),
        encoding="utf-8",
    )
    (topic_dir / "cover_image.jpg").write_bytes(image_path.read_bytes())
    (topic_dir / "cover_image.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (topic_dir / "cover_image_attribution.txt").write_text(build_attribution_text(metadata), encoding="utf-8")
    merge_cover_image_metadata(topic_dir / "metadata.json", metadata)
    LOGGER.info("Unsplash cover image saved: %s photo_id=%s", image_path, photo_id)
    return metadata


def build_search_query(topic: dict[str, Any], result: dict[str, Any], image_config: dict[str, Any]) -> str:
    prompt = str(result.get("cover_prompt") or "").strip()
    fallback_query = str(
        image_config.get("fallback_query", "fashion design studio apparel workflow")
    ).strip()
    if not prompt:
        return fallback_query

    prompt_path_text = prompt

    english_words = re.findall(r"[A-Za-z][A-Za-z\-]{2,}", prompt_path_text)
    if english_words:
        query = " ".join(english_words[:8])
    else:
        title = str(topic.get("title") or topic.get("source_title") or "")
        title_words = re.findall(r"[A-Za-z][A-Za-z\-]{2,}", title)
        query = " ".join(title_words[:6])

    if not query:
        query = fallback_query

    query = re.sub(r"\b(logo|text|typography|wordmark|brand)\b", "", query, flags=re.I)
    query = re.sub(r"\s+", " ", query).strip()
    return query or fallback_query


def load_used_photo_ids(output_root: Path) -> set[str]:
    used: set[str] = set()
    if not output_root.exists():
        return used

    for metadata_path in list(output_root.glob("**/metadata.json")) + list(output_root.glob("**/assets/image_metadata.json")):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        cover_image = metadata.get("cover_image") or metadata
        photo_id = cover_image.get("id") if isinstance(cover_image, dict) else None
        if photo_id:
            used.add(str(photo_id))
    return used


def search_photo(
    access_key: str,
    query: str,
    image_config: dict[str, Any],
    used_photo_ids: set[str],
) -> dict[str, Any] | None:
    params = {
        "query": query,
        "orientation": image_config.get("orientation", "landscape"),
        "per_page": int(image_config.get("per_page", 10)),
        "content_filter": image_config.get("content_filter", "high"),
    }
    response = get_with_retries(
        f"{UNSPLASH_API_BASE}/search/photos",
        retry_label="photo search",
        headers=auth_headers(access_key),
        params=params,
        timeout=int(image_config.get("timeout_seconds", 20)),
    )
    payload = response.json()
    results = payload.get("results") or []
    candidates = [photo for photo in results if str(photo.get("id")) not in used_photo_ids]
    if not candidates:
        candidates = results
    if not candidates:
        return None
    return random.SystemRandom().choice(candidates)


def build_download_image_url(photo: dict[str, Any], image_config: dict[str, Any]) -> str:
    urls = photo.get("urls") or {}
    raw_url = urls.get("raw") or urls.get("regular") or urls.get("full")
    if not raw_url:
        raise ValueError("Unsplash photo has no image URL")

    separator = "&" if "?" in raw_url else "?"
    params = {
        "w": int(image_config.get("width", 1600)),
        "h": int(image_config.get("height", 900)),
        "fit": "crop",
        "crop": "entropy",
        "auto": "format",
        "fm": "jpg",
        "q": 85,
    }
    return f"{raw_url}{separator}{urlencode(params)}"


def download_image(url: str, path: Path, attempts: int = 3) -> None:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(url, timeout=(15, 45))
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "")
            if "image" not in content_type.lower():
                raise ValueError(f"Unsplash download did not return an image: {content_type}")
            path.write_bytes(response.content)
            return
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt >= attempts:
                break
            delay = attempt * 2
            LOGGER.warning(
                "Unsplash image download failed attempt=%s/%s; retrying in %ss: %s",
                attempt,
                attempts,
                delay,
                exc,
            )
            time.sleep(delay)
    raise RuntimeError(f"Unsplash image download failed after {attempts} attempts: {last_error}")


def trigger_unsplash_download(access_key: str, photo_id: str) -> None:
    try:
        get_with_retries(
            f"{UNSPLASH_API_BASE}/photos/{photo_id}/download",
            retry_label="download tracking",
            headers=auth_headers(access_key),
            timeout=15,
        )
    except RuntimeError as exc:
        LOGGER.warning("Unsplash download tracking failed; keeping downloaded image: %s", exc)


def get_with_retries(
    url: str,
    *,
    retry_label: str,
    attempts: int = 3,
    **kwargs: Any,
) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(url, **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= attempts:
                break
            delay = attempt * 2
            LOGGER.warning(
                "Unsplash %s failed attempt=%s/%s; retrying in %ss: %s",
                retry_label,
                attempt,
                attempts,
                delay,
                exc,
            )
            time.sleep(delay)
    raise RuntimeError(f"Unsplash {retry_label} failed after {attempts} attempts: {last_error}")


def auth_headers(access_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Client-ID {access_key}",
        "Accept-Version": "v1",
    }


def build_image_metadata(
    photo: dict[str, Any],
    query: str,
    image_url: str,
    image_path: Path,
) -> dict[str, Any]:
    user = photo.get("user") or {}
    links = photo.get("links") or {}
    user_links = user.get("links") or {}
    return {
        "id": photo.get("id"),
        "description": photo.get("description") or photo.get("alt_description") or "",
        "photographer": user.get("name") or user.get("username") or "",
        "photographer_username": user.get("username") or "",
        "photographer_url": user_links.get("html") or "",
        "unsplash_url": links.get("html") or "",
        "download_tracking_url": links.get("download_location") or "",
        "search_query": query,
        "image_url": image_url,
        "local_path": str(image_path),
        "downloaded_at": datetime.now().isoformat(timespec="seconds"),
        "source": "Unsplash",
    }


def build_attribution_text(metadata: dict[str, Any]) -> str:
    photographer = metadata.get("photographer") or "Unsplash photographer"
    photographer_url = metadata.get("photographer_url") or "https://unsplash.com"
    photo_url = metadata.get("unsplash_url") or "https://unsplash.com"
    return (
        f"Photo by {photographer} on Unsplash\n"
        f"Photographer: {photographer_url}\n"
        f"Photo: {photo_url}\n"
    )


def merge_cover_image_metadata(metadata_path: Path, cover_image: dict[str, Any]) -> None:
    metadata: dict[str, Any] = {}
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            metadata = {}
    metadata["cover_image"] = cover_image
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
