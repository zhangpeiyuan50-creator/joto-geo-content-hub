from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Mapping


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Schedule:
    minute: int
    hour: int


def parse_schedule(value: str) -> Schedule:
    value = value.strip()
    if ":" in value:
        hour_text, minute_text = value.split(":", 1)
        return Schedule(minute=int(minute_text), hour=int(hour_text))

    parts = value.split()
    if len(parts) >= 2:
        minute_text, hour_text = parts[:2]
        if minute_text == "*" or hour_text == "*":
            raise ValueError("scheduler currently supports one fixed minute and hour, not wildcard hour/minute")
        return Schedule(minute=int(minute_text), hour=int(hour_text))

    raise ValueError("schedule must be HH:MM or cron-like 'M H * * *'")


def should_run_now(schedule: Schedule, current: datetime, last_run_key: str | None) -> tuple[bool, str]:
    run_key = current.strftime("%Y-%m-%d")
    should_run = current.hour == schedule.hour and current.minute == schedule.minute and last_run_key != run_key
    return should_run, run_key


def next_run_time(schedule: Schedule, current: datetime | None = None) -> datetime:
    current = current or datetime.now()
    candidate = current.replace(hour=schedule.hour, minute=schedule.minute, second=0, microsecond=0)
    if candidate <= current:
        candidate += timedelta(days=1)
    return candidate


def run_scheduler(
    task: Callable[[], None],
    schedule_value: str,
    poll_seconds: int = 30,
    run_on_start: bool = False,
    state_path: Path | None = None,
) -> None:
    schedule = parse_schedule(schedule_value)
    next_run = next_run_time(schedule)
    LOGGER.info("[SCHEDULER] started")
    LOGGER.info("[SCHEDULER] mode: blocking loop")
    LOGGER.info("[SCHEDULER] schedule: %02d:%02d", schedule.hour, schedule.minute)
    LOGGER.info("[SCHEDULER] next run: %s", next_run.isoformat(timespec="seconds"))
    LOGGER.info("[SCHEDULER] entering loop; poll_seconds=%s", max(5, poll_seconds))
    write_scheduler_state(
        state_path,
        status="running",
        supervisor_pid=os.getpid(),
        schedule=f"{schedule.hour:02d}:{schedule.minute:02d}",
        next_run=next_run.isoformat(timespec="seconds"),
        heartbeat_at=datetime.now().isoformat(timespec="seconds"),
    )

    last_run_key: str | None = None
    if run_on_start:
        LOGGER.info("[SCHEDULER] run_on_start enabled")
        trigger_job(task, schedule, state_path)
        last_run_key = datetime.now().strftime("%Y-%m-%d")

    while True:
        current = datetime.now()
        write_scheduler_state(
            state_path,
            status="running",
            supervisor_pid=os.getpid(),
            schedule=f"{schedule.hour:02d}:{schedule.minute:02d}",
            next_run=next_run_time(schedule, current).isoformat(timespec="seconds"),
            heartbeat_at=current.isoformat(timespec="seconds"),
        )
        should_run, run_key = should_run_now(schedule, current, last_run_key)
        if should_run:
            trigger_job(task, schedule, state_path)
            last_run_key = run_key
        time.sleep(max(5, poll_seconds))


def run_multi_scheduler(
    tasks: Mapping[str, tuple[Callable[[], None], str]],
    poll_seconds: int = 30,
    state_path: Path | None = None,
    enabled: bool = True,
) -> None:
    schedules = {module_id: parse_schedule(value) for module_id, (_, value) in tasks.items()}
    last_run_keys: dict[str, str] = {}
    LOGGER.info("[SCHEDULER] started")
    LOGGER.info("[SCHEDULER] mode: multi-module blocking loop")
    LOGGER.info("[SCHEDULER] enabled: %s", enabled)
    LOGGER.info("[SCHEDULER] entering loop; poll_seconds=%s", max(5, poll_seconds))

    while True:
        current = datetime.now()
        next_runs = {
            module_id: next_run_time(schedule, current).isoformat(timespec="seconds")
            for module_id, schedule in schedules.items()
        }
        nearest = min(next_runs.values()) if next_runs and enabled else "-"
        write_scheduler_state(
            state_path,
            status="running" if enabled else "manual",
            supervisor_pid=os.getpid(),
            next_run=nearest,
            next_runs=next_runs if enabled else {},
            heartbeat_at=current.isoformat(timespec="seconds"),
        )

        if enabled:
            for module_id, (task, _) in tasks.items():
                schedule = schedules[module_id]
                should_run, run_key = should_run_now(schedule, current, last_run_keys.get(module_id))
                if not should_run:
                    continue
                LOGGER.info("[SCHEDULER] job triggered module=%s", module_id)
                write_scheduler_state(
                    state_path,
                    last_triggered_at=current.isoformat(timespec="seconds"),
                    last_module=module_id,
                )
                try:
                    task()
                    write_scheduler_state(state_path, last_status="success", last_module=module_id)
                except Exception as exc:
                    LOGGER.exception("[SCHEDULER] module job failed module=%s", module_id)
                    write_scheduler_state(
                        state_path,
                        last_status="failed",
                        last_module=module_id,
                        last_error=str(exc),
                    )
                last_run_keys[module_id] = run_key

        time.sleep(max(5, poll_seconds))


def trigger_job(task: Callable[[], None], schedule: Schedule, state_path: Path | None) -> None:
    LOGGER.info("[SCHEDULER] job triggered")
    write_scheduler_state(state_path, last_triggered_at=datetime.now().isoformat(timespec="seconds"))
    try:
        task()
        LOGGER.info("[SCHEDULER] job finished")
        LOGGER.info("[SCHEDULER] next run: %s", next_run_time(schedule).isoformat(timespec="seconds"))
        write_scheduler_state(
            state_path,
            status="running",
            last_status="success",
            next_run=next_run_time(schedule).isoformat(timespec="seconds"),
        )
    except Exception as exc:
        LOGGER.exception("[SCHEDULER] job failed")
        write_scheduler_state(
            state_path,
            status="running",
            last_status="failed",
            last_error=str(exc),
            next_run=next_run_time(schedule).isoformat(timespec="seconds"),
        )


def write_scheduler_state(state_path: Path | None, **updates: object) -> None:
    if state_path is None:
        return

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state: dict[str, object] = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}
    state.update(updates)
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
