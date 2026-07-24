from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

from core.config import load_config
from core.browser_publisher import publish_interactive
from core.content_profiles import DEFAULT_MODULE_ID, build_module_config, list_content_profiles, module_output_dir
from core.crawler import collect_hot_items
from core.publisher import publish_all
from core.scheduler import run_multi_scheduler, write_scheduler_state
from core.topic_generator import run_topic_job, select_generation_topics
from core.analytics_store import AnalyticsStore
from core.engagement_monitor import EngagementMonitor, analytics_path
from core.geo_monitor import GeoMonitor, login_provider


PROJECT_ROOT = Path(__file__).resolve().parent
MAX_RESTARTS = 5
RETRY_DELAYS_SECONDS = [0, 30]


def setup_logging(project_root: Path) -> Path:
    log_dir = project_root / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "fasium_geo_auto.log"
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s - %(message)s")

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)

    root_logger.addHandler(stream_handler)
    root_logger.addHandler(file_handler)
    return log_path


def get_paths(config: dict[str, Any]) -> tuple[Path, Path]:
    data_dir = PROJECT_ROOT / config.get("project", {}).get("data_dir", "data")
    output_dir = PROJECT_ROOT / config.get("project", {}).get("output_dir", "data/outputs")
    data_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    return data_dir, output_dir


def failure_log_path(config: dict[str, Any]) -> Path:
    data_dir, _ = get_paths(config)
    return data_dir / "logs" / "failures.log"


def append_failure(config: dict[str, Any], message: str) -> None:
    path = failure_log_path(config)
    with path.open("a", encoding="utf-8") as file:
        file.write(f"{datetime.now().isoformat(timespec='seconds')} {message}\n")


def retry_operation(
    operation: Callable[[], Any],
    label: str,
    config: dict[str, Any],
    is_failure: Callable[[Any], bool] | None = None,
) -> Any:
    logger = logging.getLogger(__name__)
    total_attempts = 1 + len(RETRY_DELAYS_SECONDS)
    last_error: Exception | None = None

    for index in range(total_attempts):
        attempt = index + 1
        if index > 0:
            delay = RETRY_DELAYS_SECONDS[index - 1]
            if delay:
                logger.warning("[RETRY] %s retrying after %s seconds; attempt=%s/%s", label, delay, attempt, total_attempts)
                time.sleep(delay)
            else:
                logger.warning("[RETRY] %s immediate retry; attempt=%s/%s", label, attempt, total_attempts)

        try:
            result = operation()
            if is_failure and is_failure(result):
                raise RuntimeError(f"{label} returned failed result")
            return result
        except Exception as exc:
            last_error = exc
            logger.warning("[RETRY] %s failed attempt=%s/%s error=%s", label, attempt, total_attempts, exc)
            append_failure(config, f"{label} failed attempt={attempt}/{total_attempts} error={exc}")

    assert last_error is not None
    raise last_error


def run_once(
    config: dict[str, Any],
    module_id: str = DEFAULT_MODULE_ID,
    hot_items: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    logger = logging.getLogger(__name__)
    data_dir, output_root = get_paths(config)
    module_config = build_module_config(config, module_id)
    output_dir = module_output_dir(output_root, module_id)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("content factory run started module=%s", module_id)
    if hot_items is None:
        hot_items = retry_operation(
            lambda: collect_hot_items(config),
            "crawler",
            config,
            is_failure=lambda items: not items,
        )
    if not hot_items:
        logger.info("no hot items collected")
        return []

    topics = select_generation_topics(hot_items, module_config, output_dir)
    if not topics:
        logger.info("no topics matched current filters")
        return []

    jobs: list[dict[str, Any]] = []
    for topic in topics:
        logger.info("topic selected: %s", topic["title"])
        job = retry_operation(
            lambda topic=topic: run_topic_job(topic, module_config, output_dir, data_dir),
            f"dify job topic={topic['title']}",
            module_config,
            is_failure=lambda item: item.get("status") == "failed",
        )
        jobs.append(job)
        if job.get("status") == "generated" and module_config.get("publish", {}).get("enabled", False):
            publish_results = publish_all(job, module_config)
            job["publish_results"] = publish_results

    logger.info("content factory run finished module=%s jobs=%s", module_id, len(jobs))
    return jobs


def run_all_modules(config: dict[str, Any]) -> list[dict[str, Any]]:
    hot_items = retry_operation(
        lambda: collect_hot_items(config),
        "crawler",
        config,
        is_failure=lambda items: not items,
    )
    jobs: list[dict[str, Any]] = []
    for profile in list_content_profiles(config, enabled_only=True):
        try:
            jobs.extend(run_once(config, profile["id"], hot_items=hot_items))
        except Exception:
            logging.getLogger(__name__).exception("module run failed; continuing module=%s", profile["id"])
    return jobs


def load_app_config() -> dict[str, Any]:
    env_path = PROJECT_ROOT / ".env"
    load_dotenv(env_path, override=True)
    for key in [
        "DIFY_API_KEY",
        "DIFY_API_KEY_FASIUM",
        "DIFY_API_KEY_WORKBUDDY",
        "DIFY_API_KEY_ADP",
        "DIFY_API_KEY_DIFY",
        "UNSPLASH_ACCESS_KEY",
    ]:
        apply_env_fallback(env_path, key)
    return load_config(PROJECT_ROOT / "config.yaml")


def apply_env_fallback(env_path: Path, key: str) -> None:
    if os.getenv(key):
        return
    if not env_path.exists():
        return

    prefix = f"{key}="
    for line in env_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if line.startswith(prefix):
            value = line[len(prefix) :].strip().strip('"').strip("'")
            if value:
                os.environ[key] = value
            return


def scheduler_values(config: dict[str, Any]) -> tuple[str, int, bool]:
    scheduler_config = config.get("scheduler", {})
    schedule_value = str(scheduler_config.get("daily_time") or scheduler_config.get("cron") or "08:30")
    poll_seconds = int(scheduler_config.get("poll_seconds", 30))
    run_on_start = bool(scheduler_config.get("run_on_start", False))
    return schedule_value, poll_seconds, run_on_start


def run_schedule_forever(config: dict[str, Any]) -> None:
    logger = logging.getLogger(__name__)
    data_dir, _ = get_paths(config)
    scheduler_config = config.get("scheduler", {})
    poll_seconds = int(scheduler_config.get("poll_seconds", 30))
    generation_enabled = bool(scheduler_config.get("enabled", False))
    tasks: dict[str, tuple[Callable[[], None], str]] = {}
    if generation_enabled:
        tasks.update({
            profile["id"]: (
                lambda module_id=profile["id"]: run_once(config, module_id),
                str(profile.get("schedule", "08:30")),
            )
            for profile in list_content_profiles(config, enabled_only=True)
        })
    monitoring_config = config.get("monitoring", {})
    monitoring_enabled = bool(monitoring_config.get("enabled", False))
    if monitoring_enabled:
        tasks["monitoring:engagement"] = (
            lambda: run_engagement_monitor(config),
            str(monitoring_config.get("engagement", {}).get("daily_time", "14:00")),
        )
        tasks["monitoring:geo"] = (
            lambda: run_geo_monitor(config),
            str(monitoring_config.get("geo", {}).get("daily_time", "15:00")),
        )
    enabled = bool(tasks)
    logger.info(
        "[SCHEDULER] current mode: multi-module generation_enabled=%s monitoring_enabled=%s",
        generation_enabled,
        monitoring_enabled,
    )
    run_multi_scheduler(
        tasks,
        poll_seconds=poll_seconds,
        state_path=data_dir / "scheduler_state.json",
        enabled=enabled,
    )


def analytics_store(config: dict[str, Any]) -> AnalyticsStore:
    return AnalyticsStore(analytics_path(PROJECT_ROOT, config))


def run_engagement_monitor(
    config: dict[str, Any], job_id: str | None = None, force: bool = False
) -> dict[str, int]:
    result = EngagementMonitor(analytics_store(config), config).run(job_id=job_id, force=force)
    logging.getLogger(__name__).info("engagement monitor finished result=%s", result)
    return result


def run_geo_monitor(
    config: dict[str, Any], job_id: str | None = None, force: bool = False
) -> dict[str, int]:
    result = GeoMonitor(PROJECT_ROOT, analytics_store(config), config).run(job_id=job_id, force=force)
    logging.getLogger(__name__).info("geo monitor finished result=%s", result)
    return result


def supervise_scheduler(config: dict[str, Any]) -> int:
    logger = logging.getLogger(__name__)
    data_dir, _ = get_paths(config)
    state_path = data_dir / "scheduler_state.json"
    restart_count = 0
    pid_path(config).write_text(str(os.getpid()), encoding="utf-8")
    write_scheduler_state(state_path, status="supervising", restart_count=restart_count, supervisor_pid=os.getpid())
    logger.info("[SUPERVISOR] started pid=%s max_restarts=%s", os.getpid(), MAX_RESTARTS)

    while restart_count <= MAX_RESTARTS:
        try:
            run_schedule_forever(config)
            logger.warning("[SUPERVISOR] scheduler returned unexpectedly")
            return 1
        except KeyboardInterrupt:
            logger.info("[SUPERVISOR] stopped by keyboard interrupt")
            write_scheduler_state(state_path, status="stopped", restart_count=restart_count)
            return 0
        except Exception as exc:
            restart_count += 1
            logger.exception("[SUPERVISOR] scheduler crashed; restart_count=%s", restart_count)
            append_failure(config, f"supervisor restart_count={restart_count} error={exc}")
            write_scheduler_state(
                state_path,
                status="restarting" if restart_count <= MAX_RESTARTS else "failed",
                restart_count=restart_count,
                last_error=str(exc),
            )
            if restart_count > MAX_RESTARTS:
                logger.error("[SUPERVISOR] max restart count reached; giving up")
                return 1
            time.sleep(5)

    return 1


def pid_path(config: dict[str, Any]) -> Path:
    data_dir, _ = get_paths(config)
    return data_dir / "scheduler.pid"


def read_pid(config: dict[str, Any]) -> int | None:
    path = pid_path(config)
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def is_process_running(pid: int) -> bool:
    if os.name == "nt":
        return get_windows_tasklist_line(pid) is not None

    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def get_windows_tasklist_line(pid: int) -> str | None:
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return None

    output = result.stdout.strip()
    if not output or "INFO:" in output or "No tasks" in output:
        return None
    return output.splitlines()[0]


def start_daemon(config: dict[str, Any]) -> int:
    logger = logging.getLogger(__name__)
    existing_pid = read_pid(config)
    if existing_pid and is_process_running(existing_pid):
        logger.info("[DAEMON] already running pid=%s", existing_pid)
        print(f"Scheduler daemon already running: pid={existing_pid}")
        return 0

    log_path = PROJECT_ROOT / "data" / "logs" / "fasium_geo_auto.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    python_exe = sys.executable
    args = [python_exe, str(PROJECT_ROOT / "main.py"), "supervise"]
    log_file = log_path.open("a", encoding="utf-8")

    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags |= subprocess.CREATE_NO_WINDOW

    try:
        process = subprocess.Popen(
            args,
            cwd=str(PROJECT_ROOT),
            stdout=log_file,
            stderr=log_file,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=os.name != "nt",
            start_new_session=os.name != "nt",
        )
    finally:
        log_file.close()

    pid_path(config).write_text(str(process.pid), encoding="utf-8")
    logger.info("[DAEMON] started pid=%s", process.pid)
    print(f"Scheduler daemon started: pid={process.pid}")
    if os.name == "nt":
        print(f'Tasklist check: tasklist /FI "PID eq {process.pid}"')
    return 0


def stop_daemon(config: dict[str, Any]) -> int:
    logger = logging.getLogger(__name__)
    pid = read_pid(config)
    if not pid:
        print("Scheduler daemon is not running: no pid file")
        return 0
    if not is_process_running(pid):
        pid_path(config).unlink(missing_ok=True)
        print("Scheduler daemon is not running: stale pid removed")
        return 0

    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)
    else:
        os.kill(pid, signal.SIGTERM)
    time.sleep(1)
    pid_path(config).unlink(missing_ok=True)
    logger.info("[DAEMON] stopped pid=%s", pid)
    print(f"Scheduler daemon stopped: pid={pid}")
    return 0


def daemon_status(config: dict[str, Any]) -> int:
    data_dir, _ = get_paths(config)
    pid = read_pid(config)
    running = bool(pid and is_process_running(pid))
    state_path = data_dir / "scheduler_state.json"
    state_text = state_path.read_text(encoding="utf-8") if state_path.exists() else "{}"
    print(f"running={running}")
    print(f"pid={pid or '-'}")
    if pid and os.name == "nt":
        task_line = get_windows_tasklist_line(pid)
        print(f'tasklist_command=tasklist /FI "PID eq {pid}"')
        print(f"tasklist={task_line or '-'}")
    print(f"state={state_text}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="JOTO GEO Content Hub")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["run-once", "schedule", "supervise", "start", "stop", "status", "publish", "monitor", "llm-login"],
        default="run-once",
        help="run once, run scheduler, or manage daemon",
    )
    parser.add_argument(
        "legacy_platform",
        nargs="?",
        choices=["zhihu", "csdn", "sohu", "engagement", "geo", "yuanbao", "kimi", "deepseek", "doubao"],
        help="optional platform, monitor type, or LLM provider",
    )
    parser.add_argument("--module", default=DEFAULT_MODULE_ID, help="content module id or all")
    parser.add_argument("--platform", dest="named_platform", choices=["zhihu", "csdn", "sohu"])
    parser.add_argument("--job", "--job-id", dest="job_id", help="exact job id")
    args = parser.parse_args()

    log_path = setup_logging(PROJECT_ROOT)
    config = load_app_config()
    logging.getLogger(__name__).info("log file: %s", log_path)

    if args.command == "start":
        return start_daemon(config)
    if args.command == "stop":
        return stop_daemon(config)
    if args.command == "status":
        return daemon_status(config)
    if args.command == "publish":
        platform = args.named_platform or args.legacy_platform
        return publish_interactive(
            PROJECT_ROOT,
            config,
            platform=platform,
            module_id=args.module,
            job_id=args.job_id,
        )
    if args.command == "monitor":
        monitor_type = args.legacy_platform
        if monitor_type not in {"engagement", "geo"}:
            parser.error("monitor command requires engagement or geo")
        result = (
            run_engagement_monitor(config, args.job_id, force=bool(args.job_id))
            if monitor_type == "engagement"
            else run_geo_monitor(config, args.job_id, force=bool(args.job_id))
        )
        print(result)
        return 0 if result.get("failed", 0) == 0 else 1
    if args.command == "llm-login":
        provider = args.legacy_platform
        if provider not in {"yuanbao", "kimi", "deepseek", "doubao"}:
            parser.error("llm-login requires yuanbao, kimi, deepseek, or doubao")
        login_provider(PROJECT_ROOT, provider)
        return 0
    if args.command == "supervise":
        return supervise_scheduler(config)
    if args.command == "schedule":
        run_schedule_forever(config)
        return 0

    logging.getLogger(__name__).info("current mode: run-once")
    if args.module == "all":
        run_all_modules(config)
    else:
        run_once(config, args.module)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
