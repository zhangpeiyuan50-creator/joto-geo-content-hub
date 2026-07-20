from __future__ import annotations

import html
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from core.config import load_config
from core.job_queue import load_recent_jobs, summarize_jobs_today


PROJECT_ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8770


def get_data_dir() -> Path:
    config = load_config(PROJECT_ROOT / "config.yaml")
    return PROJECT_ROOT / config.get("project", {}).get("data_dir", "data")


def read_tail(path: Path, limit: int = 80) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-limit:])


def render_dashboard() -> str:
    data_dir = get_data_dir()
    summary = summarize_jobs_today(data_dir)
    recent_jobs = load_recent_jobs(data_dir, limit=10)
    log_tail = read_tail(data_dir / "logs" / "fasium_geo_auto.log", limit=80)
    failure_tail = read_tail(data_dir / "logs" / "failures.log", limit=40)
    state_path = data_dir / "scheduler_state.json"
    scheduler_state = state_path.read_text(encoding="utf-8") if state_path.exists() else "{}"

    rows = []
    for job in recent_jobs:
        topic = job.get("topic", {})
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(job.get('id', '')))}</td>"
            f"<td>{html.escape(str(job.get('status', '')))}</td>"
            f"<td>{html.escape(str(topic.get('title', '')))}</td>"
            f"<td>{html.escape(str(job.get('created_at', '')))}</td>"
            f"<td>{html.escape(str(job.get('output_dir', '')))}</td>"
            "</tr>"
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>FasiumAI GEO Job Dashboard</title>
  <style>
    body {{ margin: 0; font-family: "Segoe UI", Arial, sans-serif; background: #f6f7f9; color: #1f2937; }}
    header {{ padding: 22px 28px; background: #fff; border-bottom: 1px solid #d9dee7; }}
    main {{ padding: 22px 28px; display: grid; gap: 18px; }}
    .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }}
    .card {{ background: #fff; border: 1px solid #d9dee7; border-radius: 8px; padding: 16px; }}
    .num {{ font-size: 28px; font-weight: 700; margin-top: 8px; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #d9dee7; }}
    th, td {{ border-bottom: 1px solid #e5e7eb; padding: 10px; text-align: left; vertical-align: top; }}
    th {{ background: #f9fafb; }}
    td {{ overflow-wrap: anywhere; }}
    pre {{ white-space: pre-wrap; background: #111827; color: #e5e7eb; padding: 14px; border-radius: 8px; overflow: auto; max-height: 420px; }}
  </style>
</head>
<body>
  <header>
    <h1>FasiumAI GEO Job Dashboard</h1>
    <p>任务看板：今日生成、最近 job、scheduler 状态和 Dify 调用日志。</p>
  </header>
  <main>
    <section class="stats">
      <div class="card"><div>今日生成次数</div><div class="num">{summary["total"]}</div></div>
      <div class="card"><div>成功</div><div class="num">{summary["success"]}</div></div>
      <div class="card"><div>失败</div><div class="num">{summary["failed"]}</div></div>
      <div class="card"><div>运行中/排队</div><div class="num">{summary["running"] + summary["queued"]}</div></div>
    </section>

    <section>
      <h2>最近 10 个 Job</h2>
      <table>
        <thead><tr><th>ID</th><th>Status</th><th>Topic</th><th>Created</th><th>Output</th></tr></thead>
        <tbody>{''.join(rows) or '<tr><td colspan="5">No jobs yet.</td></tr>'}</tbody>
      </table>
    </section>

    <section>
      <h2>Scheduler 状态</h2>
      <pre>{html.escape(format_json(scheduler_state))}</pre>
    </section>

    <section>
      <h2>Dify / 系统日志</h2>
      <pre>{html.escape(log_tail)}</pre>
    </section>

    <section>
      <h2>失败日志</h2>
      <pre>{html.escape(failure_tail)}</pre>
    </section>
  </main>
</body>
</html>"""


def format_json(text: str) -> str:
    try:
        return json.dumps(json.loads(text), ensure_ascii=False, indent=2)
    except json.JSONDecodeError:
        return text


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path not in {"/", "/dashboard"}:
            self.send_text("Not found", HTTPStatus.NOT_FOUND)
            return
        self.send_html(render_dashboard())

    def send_html(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def send_text(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"FasiumAI GEO Job Dashboard: http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
