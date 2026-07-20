from __future__ import annotations

import logging
import json
from pathlib import Path
from typing import Any

from core.content_profiles import DEFAULT_MODULE_ID, build_module_config
from core.job_queue import load_job, save_job
from core.publishers import CSDNPublisher, SohuPublisher, ZhihuPublisher
from core.publishers.base import extract_article_html, html_to_text, looks_like_html


PUBLISHERS = {
    "zhihu": ZhihuPublisher,
    "csdn": CSDNPublisher,
    "sohu": SohuPublisher,
}


def setup_publisher_logger(project_root: Path) -> logging.Logger:
    log_dir = project_root / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("publisher")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    file_handler = logging.FileHandler(log_dir / "publisher.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def choose_platform() -> str:
    print("请选择发布平台：")
    print("1.zhihu")
    print("2.csdn")
    print("3.sohu")
    choice = input("请输入序号：").strip()
    mapping = {
        "1": "zhihu",
        "2": "csdn",
        "3": "sohu",
        "zhihu": "zhihu",
        "csdn": "csdn",
        "sohu": "sohu",
    }
    platform = mapping.get(choice.lower())
    if not platform:
        raise ValueError("未知平台，请输入 1、2、3 或 zhihu/csdn/sohu")
    return platform


PLATFORM_CONTENT_FILES = {
    "zhihu": ("zhihu/zhihu_rich.html", "zhihu_rich.html", "zhihu.md"),
    "csdn": ("csdn/csdn.md", "csdn.md"),
    "sohu": ("sohu/sohu_rich.html", "sohu_rich.html", "sohu.md"),
}


def package_is_reviewed(package: Path, requires_review: bool) -> bool:
    if not requires_review:
        return True
    metadata_path = package / "metadata.json"
    if not metadata_path.exists():
        return False
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return metadata.get("review_status") == "approved"


def latest_content_package(output_dir: Path, platform: str, requires_review: bool = False) -> Path:
    if not output_dir.exists():
        raise FileNotFoundError(f"outputs directory not found: {output_dir}")
    packages = [path for path in output_dir.iterdir() if path.is_dir()]
    packages.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for package in packages:
        if not package_is_reviewed(package, requires_review):
            continue
        for relative_path in PLATFORM_CONTENT_FILES[platform]:
            content_path = package / relative_path
            if content_path.is_file() and is_publishable_content(content_path):
                return package
    raise FileNotFoundError(
        f"没有找到可发布的 {platform} Content Package。请确认正文有效，合作内容还需要先通过人工审核"
    )


def is_publishable_content(content_path: Path) -> bool:
    if content_path.stat().st_size <= 20:
        return False
    content = content_path.read_text(encoding="utf-8", errors="replace")
    if looks_like_html(content):
        article = extract_article_html(content)
        return len(html_to_text(article).strip()) > 80
    return len(content.strip()) > 80


def resolve_package(
    project_root: Path,
    config: dict[str, Any],
    module_id: str,
    platform: str,
    job_id: str | None,
) -> tuple[Path, dict[str, Any] | None]:
    output_root = project_root / config.get("project", {}).get("output_dir", "outputs")
    module_config = build_module_config(config, module_id)
    module = module_config["active_module"]
    data_dir = project_root / config.get("project", {}).get("data_dir", "data")

    if job_id:
        job = load_job(data_dir, job_id)
        if not job:
            raise FileNotFoundError(f"job not found: {job_id}")
        if job.get("module_id", DEFAULT_MODULE_ID) != module_id:
            raise ValueError(f"job {job_id} does not belong to module {module_id}")
        if module.get("requires_review") and job.get("review_status") != "approved":
            raise PermissionError("合作内容尚未通过人工审核，不能启动发布辅助")
        package = Path(str(job.get("output_dir", "")))
        if not package.exists():
            raise FileNotFoundError(f"job output directory not found: {package}")
        if not any(
            (package / relative_path).is_file()
            and is_publishable_content(package / relative_path)
            for relative_path in PLATFORM_CONTENT_FILES[platform]
        ):
            raise FileNotFoundError(
                f"job {job_id} does not contain publishable {platform} content"
            )
        return package, job

    module_root = output_root / module_id
    if module_root.exists():
        return latest_content_package(module_root, platform, bool(module.get("requires_review"))), None
    if module_id == DEFAULT_MODULE_ID:
        return latest_content_package(output_root, platform, False), None
    raise FileNotFoundError(f"module output directory not found: {module_root}")


def publish_interactive(
    project_root: Path,
    config: dict[str, Any],
    platform: str | None = None,
    module_id: str = DEFAULT_MODULE_ID,
    job_id: str | None = None,
) -> int:
    logger = setup_publisher_logger(project_root)
    selected_platform = platform or choose_platform()
    module_config = build_module_config(config, module_id)
    package_dir, job = resolve_package(project_root, config, module_id, selected_platform, job_id)
    publisher_class = PUBLISHERS[selected_platform]
    publisher = publisher_class(package_dir, module_config, logger)

    logger.info(
        "browser publisher started module=%s platform=%s job=%s package=%s",
        module_id,
        selected_platform,
        job_id or "latest",
        package_dir,
    )
    print(f"本次内容模块：{module_config['active_module']['name']}")
    print(f"本次发布平台：{selected_platform}")
    print(f"读取内容包：{package_dir}")
    try:
        publisher.run()
        if job:
            job.setdefault("publish_status", {})[selected_platform] = "ready"
            save_job(project_root / config.get("project", {}).get("data_dir", "data"), job)
        logger.info("browser publisher finished platform=%s package=%s", selected_platform, package_dir)
        return 0
    except Exception as exc:
        if job:
            job.setdefault("publish_status", {})[selected_platform] = "failed"
            save_job(project_root / config.get("project", {}).get("data_dir", "data"), job)
        screenshot = publisher.screenshot_on_error(exc)
        print(f"发布辅助失败：{exc}")
        print(f"错误截图已保存：{screenshot}")
        return 1
    finally:
        publisher.close()
