from __future__ import annotations

import contextlib
import html
import io
import json
import os
import subprocess
import sys
import threading
import traceback
from datetime import date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from core.config import load_config
from core.content_profiles import DEFAULT_MODULE_ID, get_content_profile, list_content_profiles
from core.job_queue import load_job, load_recent_jobs, save_job
from main import PROJECT_ROOT, load_app_config, run_once, setup_logging


HOST = "127.0.0.1"
PORT = 8765
PUBLISH_PLATFORMS = {
    "zhihu": {"name": "知乎", "format": "富文本", "accent": "blue"},
    "csdn": {"name": "CSDN", "format": "Markdown", "accent": "orange"},
    "sohu": {"name": "搜狐号", "format": "富文本", "accent": "green"},
}
MODULE_FILES = {
    "cover": [
        "assets/cover_image.jpg",
        "assets/image_metadata.json",
        "assets/attribution.txt",
        "cover_image.jpg",
        "cover_image.json",
        "cover_image_attribution.txt",
    ],
    "articles": [
        "zhihu/zhihu_rich.html",
        "csdn/csdn.md",
        "sohu/sohu_rich.html",
        "zhihu_rich.html",
        "csdn.md",
        "sohu_rich.html",
        "sohu.md",
        "zhihu.md",
    ],
    "metadata": ["metadata.json"],
    "prompt": ["assets/cover_prompt.txt", "cover_prompt.txt"],
}
ALLOWED_FILES = sorted({name for values in MODULE_FILES.values() for name in values})

state_lock = threading.Lock()
run_state = {
    "running": False,
    "module_id": "",
    "started_at": "",
    "finished_at": "",
    "logs": "系统已就绪。\n",
}
publish_state = {
    platform: {
        "running": False,
        "status": "idle",
        "module_id": "",
        "job_id": "",
        "started_at": "",
        "finished_at": "",
        "message": "等待发布",
    }
    for platform in PUBLISH_PLATFORMS
}


def get_config() -> dict:
    return load_config(PROJECT_ROOT / "config.yaml")


def get_data_dir() -> Path:
    config = get_config()
    return PROJECT_ROOT / config.get("project", {}).get("data_dir", "data")


def get_output_dir() -> Path:
    config = get_config()
    return PROJECT_ROOT / config.get("project", {}).get("output_dir", "outputs")


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return {}


def read_tail(path: Path, limit: int = 80) -> str:
    if not path.exists():
        return ""
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:])


def append_log(message: str) -> None:
    with state_lock:
        run_state["logs"] += message.rstrip() + "\n"


def set_running(value: bool, module_id: str = "") -> None:
    with state_lock:
        run_state["running"] = value
        if value:
            run_state.update(
                module_id=module_id,
                started_at=datetime.now().isoformat(timespec="seconds"),
                finished_at="",
                logs="",
            )
        else:
            run_state["finished_at"] = datetime.now().isoformat(timespec="seconds")


def run_pipeline(module_id: str) -> None:
    set_running(True, module_id)
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            setup_logging(PROJECT_ROOT)
            jobs = run_once(load_app_config(), module_id)
        append_log(buffer.getvalue())
        append_log(f"[INFO] module={module_id} jobs finished: {len(jobs)}")
        for job in jobs:
            append_log(f"[INFO] job={job.get('id')} status={job.get('status')} output={job.get('output_dir')}")
    except Exception:
        append_log(f"[ERROR] module={module_id} run failed")
        append_log(traceback.format_exc())
    finally:
        set_running(False)


def run_publisher(module_id: str, platform: str, job_id: str) -> None:
    with state_lock:
        publish_state[platform].update(
            running=True,
            status="running",
            module_id=module_id,
            job_id=job_id,
            started_at=datetime.now().isoformat(timespec="seconds"),
            finished_at="",
            message="正在打开平台编辑器",
        )

    command = [
        sys.executable,
        str(PROJECT_ROOT / "main.py"),
        "publish",
        "--module",
        module_id,
        "--platform",
        platform,
        "--job",
        job_id,
    ]
    child_env = os.environ.copy()
    child_env["FASIUM_WEB_PUBLISH"] = "1"
    status = "failed"
    message = "发布辅助启动失败，请查看日志"
    try:
        result = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=child_env,
        )
        append_log((result.stdout or "") + (result.stderr or ""))
        if result.returncode == 0:
            status = "ready"
            message = "内容已填写，请在浏览器中确认发布"
    except Exception as exc:
        append_log(f"[ERROR] publisher module={module_id} platform={platform} failed: {exc}")
        message = str(exc)
    finally:
        with state_lock:
            publish_state[platform].update(
                running=False,
                status=status,
                finished_at=datetime.now().isoformat(timespec="seconds"),
                message=message,
            )


def normalize_status(status: str) -> str:
    if status == "generated":
        return "success"
    if status in {"queued", "running"}:
        return "running"
    return status or "unknown"


def parse_dt(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def duration_seconds(job: dict) -> int | None:
    start = parse_dt(job.get("started_at") or job.get("created_at"))
    end = parse_dt(job.get("finished_at"))
    return max(0, int((end - start).total_seconds())) if start and end else None


def format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    if seconds < 60:
        return f"{seconds}s"
    minutes, rest = divmod(seconds, 60)
    return f"{minutes}m {rest}s" if minutes < 60 else f"{minutes // 60}h {minutes % 60}m"


def module_for_job(job: dict) -> str:
    return str(job.get("module_id") or DEFAULT_MODULE_ID)


def load_jobs(data_dir: Path, module_id: str = "all", limit: int = 1000) -> list[dict]:
    jobs = load_recent_jobs(data_dir, limit=limit)
    if module_id != "all":
        jobs = [job for job in jobs if module_for_job(job) == module_id]
    return jobs


def output_count_for_job(job: dict) -> int:
    path = Path(str(job.get("output_dir") or ""))
    if not path.exists():
        return 0
    return sum(1 for file_name in ALLOWED_FILES if (path / file_name).exists())


def enrich_job(job: dict) -> dict:
    duration = duration_seconds(job)
    return {
        "job_id": job.get("id", ""),
        "module_id": module_for_job(job),
        "module_name": job.get("module_name", "FasiumAI"),
        "workflow_name": job.get("workflow_name", ""),
        "status": normalize_status(str(job.get("status", ""))),
        "raw_status": job.get("status", ""),
        "review_status": job.get("review_status", "not_required"),
        "publish_status": job.get("publish_status", {}),
        "created_time": job.get("created_at", ""),
        "duration": duration,
        "duration_label": format_duration(duration),
        "output_count": output_count_for_job(job),
        "topic": job.get("topic", {}),
        "output_dir": job.get("output_dir", ""),
        "error": job.get("error", ""),
    }


def calculate_kpis(jobs: list[dict]) -> dict:
    today = date.today().isoformat()
    today_jobs = [job for job in jobs if str(job.get("created_at", "")).startswith(today)]
    total = len(today_jobs)
    success = sum(normalize_status(str(job.get("status", ""))) == "success" for job in today_jobs)
    failed = sum(normalize_status(str(job.get("status", ""))) == "failed" for job in today_jobs)
    running = sum(normalize_status(str(job.get("status", ""))) == "running" for job in today_jobs)
    durations = [duration_seconds(job) for job in today_jobs]
    finished = [value for value in durations if value is not None]
    avg_duration = int(sum(finished) / len(finished)) if finished else None
    generated = [job for job in today_jobs if normalize_status(str(job.get("status", ""))) == "success"]
    image_success = sum(
        bool(job.get("output_dir")) and (Path(str(job["output_dir"])) / "assets" / "cover_image.jpg").exists()
        for job in generated
    )
    pending_review = sum(job.get("review_status") == "pending" for job in jobs)
    return {
        "today_jobs": total,
        "success": success,
        "failed": failed,
        "running": running,
        "success_rate": round(success / total * 100) if total else 0,
        "avg_duration": avg_duration,
        "avg_duration_label": format_duration(avg_duration),
        "image_success_rate": round(image_success / len(generated) * 100) if generated else 0,
        "dify_success_rate": round(success / (success + failed) * 100) if success + failed else 0,
        "pending_review": pending_review,
    }


def safe_output_path(folder_name: str, file_name: str) -> Path:
    output_root = get_output_dir().resolve()
    target = (output_root / folder_name / file_name).resolve()
    try:
        target.relative_to(output_root)
    except ValueError as exc:
        raise ValueError("Invalid output path") from exc
    if file_name not in ALLOWED_FILES:
        raise ValueError("Invalid file name")
    return target


def is_image_file(file_name: str) -> bool:
    return file_name.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))


def image_content_type(file_name: str) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(Path(file_name).suffix.lower(), "application/octet-stream")


def view_href(folder_name: str, file_name: str) -> str:
    return f"/view?folder={quote(folder_name)}&file={quote(file_name, safe='')}"


def asset_href(folder_name: str, file_name: str) -> str:
    return f"/asset?folder={quote(folder_name)}&file={quote(file_name, safe='')}"


def download_href(folder_name: str, file_name: str) -> str:
    return f"/download?folder={quote(folder_name)}&file={quote(file_name, safe='')}"


def file_type(file_name: str) -> str:
    suffix = Path(file_name).suffix.lower()
    if suffix in {".html", ".md"}:
        return "article"
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return "image"
    if suffix == ".json":
        return "json"
    if suffix == ".txt":
        return "text"
    return "asset"


def file_info(folder: Path, file_name: str) -> dict:
    path = folder / file_name
    exists = path.exists()
    return {
        "name": file_name,
        "exists": exists,
        "size": path.stat().st_size if exists else 0,
        "version": path.stat().st_mtime_ns if exists else 0,
        "type": file_type(file_name),
    }


def preferred_file(files: dict[str, dict], candidates: list[str]) -> str:
    return next((name for name in candidates if files.get(name, {}).get("exists")), candidates[0])


def asset_payload(folder_key: str, file_name: str, files: dict[str, dict]) -> dict:
    payload = dict(files[file_name])
    payload["view_url"] = view_href(folder_key, file_name) if payload["exists"] else ""
    payload["download_url"] = download_href(folder_key, file_name) if payload["exists"] else ""
    payload["asset_url"] = (
        f"{asset_href(folder_key, file_name)}&v={payload['version']}"
        if payload["exists"] and is_image_file(file_name)
        else ""
    )
    return payload


def summarize_assets(files: dict[str, dict]) -> dict:
    values = [item for item in files.values() if item["exists"]]
    return {
        "images": sum(item["type"] == "image" for item in values),
        "html_md_outputs": sum(item["type"] == "article" for item in values),
        "json_metadata": sum(item["type"] == "json" for item in values),
        "text_prompts": sum(item["type"] == "text" for item in values),
        "total_assets": len(values),
    }


def group_output_package(folder: Path) -> dict:
    output_root = get_output_dir().resolve()
    folder_key = folder.resolve().relative_to(output_root).as_posix()
    metadata = read_json(folder / "metadata.json")
    module_id = str(metadata.get("module_id") or (folder.parent.name if folder.parent != output_root else DEFAULT_MODULE_ID))
    cover_meta = read_json(folder / "assets" / "image_metadata.json") or read_json(folder / "cover_image.json")
    files = {name: file_info(folder, name) for name in ALLOWED_FILES}
    zhihu = preferred_file(files, ["zhihu/zhihu_rich.html", "zhihu_rich.html"])
    csdn = preferred_file(files, ["csdn/csdn.md", "csdn.md"])
    sohu = preferred_file(files, ["sohu/sohu_rich.html", "sohu_rich.html"])
    cover = preferred_file(files, ["assets/cover_image.jpg", "cover_image.jpg"])
    cover_meta_file = preferred_file(files, ["assets/image_metadata.json", "cover_image.json"])
    attribution = preferred_file(files, ["assets/attribution.txt", "cover_image_attribution.txt"])
    prompt = preferred_file(files, ["assets/cover_prompt.txt", "cover_prompt.txt"])
    return {
        "folder": folder_key,
        "job_id": metadata.get("job_id", folder.name.removeprefix("job_")),
        "module_id": module_id,
        "module_name": metadata.get("module_name", "FasiumAI"),
        "title": metadata.get("topic", {}).get("title") or folder.name,
        "created_at": metadata.get("generated_at") or datetime.fromtimestamp(folder.stat().st_mtime).isoformat(timespec="seconds"),
        "review_status": metadata.get("review_status", "not_required"),
        "content_layer": {
            "article": {
                "zhihu": asset_payload(folder_key, zhihu, files),
                "csdn": asset_payload(folder_key, csdn, files),
                "sohu": asset_payload(folder_key, sohu, files),
            },
            "cover_image": {
                "image": asset_payload(folder_key, cover, files),
                "metadata": asset_payload(folder_key, cover_meta_file, files),
                "attribution": asset_payload(folder_key, attribution, files),
                "photographer": cover_meta.get("photographer", ""),
                "source": cover_meta.get("source", "Unsplash") if cover_meta else "",
            },
            "metadata": asset_payload(folder_key, "metadata.json", files),
            "prompt": asset_payload(folder_key, prompt, files),
        },
        "asset_layer": summarize_assets(files),
    }


def list_output_packages(module_id: str = "all", limit: int = 12) -> list[dict]:
    output_root = get_output_dir()
    if not output_root.exists():
        return []
    folders = [path.parent for path in output_root.glob("**/metadata.json")]
    folders.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    packages = [group_output_package(folder) for folder in folders]
    if module_id != "all":
        packages = [package for package in packages if package["module_id"] == module_id]
    return packages[:limit]


def summarize_global_assets(outputs: list[dict]) -> dict:
    total = {"images": 0, "html_md_outputs": 0, "json_metadata": 0, "text_prompts": 0, "total_assets": 0}
    for output in outputs:
        for key in total:
            total[key] += int(output.get("asset_layer", {}).get(key, 0))
    return total


def scheduler_snapshot(data_dir: Path) -> dict:
    state = read_json(data_dir / "scheduler_state.json")
    pid_path = data_dir / "scheduler.pid"
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip()) if pid_path.exists() else None
    except ValueError:
        pid = None
    running = False
    if pid:
        try:
            if os.name == "nt":
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )
                running = bool(result.stdout.strip() and "INFO:" not in result.stdout and "No tasks" not in result.stdout)
            else:
                os.kill(pid, 0)
                running = True
        except OSError:
            running = False
    return {
        "status": "running" if running else "stopped",
        "mode": state.get("status", "manual"),
        "next_run": state.get("next_run", "-"),
        "next_runs": state.get("next_runs", {}),
        "last_run": state.get("last_triggered_at", "-"),
        "last_module": state.get("last_module", "-"),
        "last_status": state.get("last_status", "-"),
        "heartbeat": state.get("heartbeat_at", "-"),
        "pid": pid or "-",
    }


def module_summaries(config: dict, jobs: list[dict]) -> list[dict]:
    today = date.today().isoformat()
    summaries: list[dict] = []
    for profile in list_content_profiles(config):
        module_jobs = [job for job in jobs if module_for_job(job) == profile["id"]]
        today_jobs = [job for job in module_jobs if str(job.get("created_at", "")).startswith(today)]
        summaries.append(
            {
                "id": profile["id"],
                "name": profile["name"],
                "short_name": profile.get("short_name", profile["name"]),
                "description": profile.get("description", ""),
                "color": profile.get("color", "blue"),
                "enabled": profile.get("enabled", True),
                "requires_review": profile.get("requires_review", False),
                "schedule": profile.get("schedule", "-"),
                "today_jobs": len(today_jobs),
                "success": sum(normalize_status(str(job.get("status", ""))) == "success" for job in today_jobs),
                "pending_review": sum(job.get("review_status") == "pending" for job in module_jobs),
                "latest_job": module_jobs[0].get("id", "") if module_jobs else "",
            }
        )
    return summaries


def dashboard_payload(module_id: str = "all") -> dict:
    config = get_config()
    if module_id != "all":
        get_content_profile(config, module_id)
    data_dir = get_data_dir()
    all_jobs = load_jobs(data_dir, "all")
    selected_jobs = all_jobs if module_id == "all" else [job for job in all_jobs if module_for_job(job) == module_id]
    outputs = list_output_packages(module_id, limit=12)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "selected_module": module_id,
        "modules": module_summaries(config, all_jobs),
        "run_state": dict(run_state),
        "kpis": calculate_kpis(selected_jobs),
        "job_layer": [enrich_job(job) for job in selected_jobs[:12]],
        "content_layer": outputs,
        "asset_layer": summarize_global_assets(outputs),
        "publish_layer": {
            platform: {**PUBLISH_PLATFORMS[platform], **dict(state)}
            for platform, state in publish_state.items()
        },
        "system_layer": {
            "scheduler": scheduler_snapshot(data_dir),
            "logs": {
                "system": read_tail(data_dir / "logs" / "fasium_geo_auto.log", 45),
                "failures": read_tail(data_dir / "logs" / "failures.log", 25),
            },
        },
    }


def approve_job(job_id: str) -> dict:
    data_dir = get_data_dir()
    job = load_job(data_dir, job_id)
    if not job:
        raise FileNotFoundError(f"job not found: {job_id}")
    if job.get("status") != "generated":
        raise ValueError("only generated jobs can be approved")
    output_dir = str(job.get("output_dir") or "").strip()
    if not output_dir or not Path(output_dir).is_dir():
        raise FileNotFoundError("job output directory not found")
    job["review_status"] = "approved"
    job["reviewed_at"] = datetime.now().isoformat(timespec="seconds")
    save_job(data_dir, job)
    metadata_path = Path(output_dir) / "metadata.json"
    metadata = read_json(metadata_path)
    if metadata_path.parent.exists():
        metadata["review_status"] = "approved"
        metadata["reviewed_at"] = job["reviewed_at"]
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return job


def render_index() -> str:
    return (PROJECT_ROOT / "templates" / "dashboard.html").read_text(encoding="utf-8")


def render_text_file(folder: str, file_name: str) -> str:
    path = safe_output_path(folder, file_name)
    if file_name.endswith(".html"):
        return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    if is_image_file(file_name):
        return render_image_file(folder, file_name)
    content = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(file_name)}</title><style>body{{margin:0;background:#f5f7fa;color:#17191d;font-family:"Microsoft YaHei",sans-serif}}header{{padding:16px 24px;background:white;border-bottom:1px solid #e2e5e9;display:flex;gap:16px}}a{{color:#2563eb;text-decoration:none}}main{{padding:24px;max-width:1100px;margin:auto}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:white;border:1px solid #e2e5e9;padding:24px;line-height:1.75}}</style></head><body><header><a href="/">返回内容中心</a><strong>{html.escape(folder)} / {html.escape(file_name)}</strong></header><main><pre>{html.escape(content)}</pre></main></body></html>"""


def render_image_file(folder: str, file_name: str) -> str:
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(file_name)}</title><style>body{{margin:0;background:#f5f7fa;font-family:"Microsoft YaHei",sans-serif}}header{{padding:16px 24px;background:white;border-bottom:1px solid #e2e5e9;display:flex;gap:16px}}a{{color:#2563eb;text-decoration:none}}main{{max-width:1120px;margin:24px auto;padding:0 24px}}img{{display:block;width:100%;border:1px solid #e2e5e9;background:white}}</style></head><body><header><a href="/">返回内容中心</a><strong>{html.escape(folder)} / {html.escape(file_name)}</strong></header><main><img src="{asset_href(folder, file_name)}" alt="{html.escape(file_name)}"></main></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if parsed.path == "/":
            self.send_html(render_index())
            return
        if parsed.path == "/api/dashboard":
            module_id = params.get("module", ["all"])[0]
            try:
                self.send_json(dashboard_payload(module_id))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/status":
            self.send_json(dict(run_state))
            return
        if parsed.path == "/view":
            folder = unquote(params.get("folder", [""])[0])
            file_name = unquote(params.get("file", [""])[0])
            try:
                self.send_html(render_text_file(folder, file_name))
            except Exception as exc:
                self.send_text(str(exc), HTTPStatus.BAD_REQUEST)
            return
        if parsed.path in {"/asset", "/download"}:
            folder = unquote(params.get("folder", [""])[0])
            file_name = unquote(params.get("file", [""])[0])
            try:
                path = safe_output_path(folder, file_name)
                if parsed.path == "/asset":
                    if not is_image_file(file_name):
                        raise ValueError("Invalid asset type")
                    self.send_binary(path, image_content_type(file_name))
                else:
                    self.send_download(path, file_name)
            except Exception as exc:
                self.send_text(str(exc), HTTPStatus.BAD_REQUEST)
            return
        self.send_text("Not found", HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        parts = [part for part in parsed.path.split("/") if part]

        if len(parts) == 3 and parts[:2] == ["api", "run"]:
            module_id = parts[2]
            try:
                get_content_profile(get_config(), module_id)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            with state_lock:
                running = bool(run_state["running"])
            if running:
                self.send_json({"error": "已有内容任务正在运行"}, HTTPStatus.CONFLICT)
                return
            threading.Thread(target=run_pipeline, args=(module_id,), daemon=True).start()
            self.send_json({"ok": True, "module_id": module_id}, HTTPStatus.ACCEPTED)
            return

        if len(parts) == 4 and parts[:2] == ["api", "publish"]:
            module_id, platform = parts[2], parts[3]
            payload = self.read_json_body()
            job_id = str(payload.get("job_id", "")).strip()
            if platform not in PUBLISH_PLATFORMS or not job_id:
                self.send_json({"error": "module、platform 和 job_id 都是必填项"}, HTTPStatus.BAD_REQUEST)
                return
            job = load_job(get_data_dir(), job_id)
            if not job or module_for_job(job) != module_id:
                self.send_json({"error": "没有找到该模块的 Job"}, HTTPStatus.NOT_FOUND)
                return
            if job.get("review_status") == "pending":
                self.send_json({"error": "合作内容尚未通过人工审核"}, HTTPStatus.FORBIDDEN)
                return
            with state_lock:
                if publish_state[platform]["running"]:
                    self.send_json({"error": "该平台的发布辅助正在运行"}, HTTPStatus.CONFLICT)
                    return
            threading.Thread(target=run_publisher, args=(module_id, platform, job_id), daemon=True).start()
            self.send_json({"ok": True, "module_id": module_id, "platform": platform, "job_id": job_id}, HTTPStatus.ACCEPTED)
            return

        if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "review":
            try:
                job = approve_job(parts[2])
                self.send_json({"ok": True, "job": enrich_job(job)})
            except FileNotFoundError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        self.send_text("Not found", HTTPStatus.NOT_FOUND)

    def read_json_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def send_html(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_encoded(body.encode("utf-8"), "text/html; charset=utf-8", status)

    def send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_encoded(json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def send_text(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_encoded(body.encode("utf-8"), "text/plain; charset=utf-8", status)

    def send_binary(self, path: Path, content_type: str) -> None:
        self.send_encoded(path.read_bytes(), content_type, HTTPStatus.OK)

    def send_download(self, path: Path, file_name: str) -> None:
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", image_content_type(file_name) if is_image_file(file_name) else "application/octet-stream")
        self.send_header("Content-Disposition", f'attachment; filename="{Path(file_name).name}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_encoded(self, data: bytes, content_type: str, status: HTTPStatus) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    setup_logging(PROJECT_ROOT)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"JOTO GEO Content Hub: http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
