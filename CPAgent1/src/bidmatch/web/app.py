"""Flask web app for the BidMatch UI.

This file contains both the RunManager (in-memory run state) and the Flask
routes (added in Task 4). RunManager is tested independently of Flask.
"""

import threading
import uuid
from collections import OrderedDict
from typing import Callable, Optional

# Cap on retained completed-run state (oldest evicted).
COMPLETED_CAP = 8


class RunManager:
    """Holds exactly one active run plus a small recent-runs cache.

    States: 'running' | 'done' | 'failed'.

    The actual work is done in a background thread per run. The `runner`
    callable is the function that performs the work; it receives a
    `progress(line: str)` callback to stream log lines.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._active: dict | None = None
        self._completed: "OrderedDict[str, dict]" = OrderedDict()

    def start(
        self,
        token: str,
        days: int,
        runner: Callable[[str, int, Callable[[str], None]], dict],
    ) -> Optional[str]:
        """Start a new run. Returns run_id if started, None if busy."""
        with self._lock:
            if self._active is not None:
                return None
            run_id = uuid.uuid4().hex[:12]
            self._active = {
                "run_id": run_id,
                "state": "running",
                "log": [],
                "summary": None,
                "error": None,
            }

        def _runner_thread():
            state = self._active

            def progress(line: str) -> None:
                state["log"].append(line)

            try:
                summary = runner(token, days, progress)
                state["state"] = "done"
                state["summary"] = summary
            except Exception as exc:  # noqa: BLE001
                state["state"] = "failed"
                state["error"] = str(exc)
            finally:
                with self._lock:
                    self._completed[state["run_id"]] = state
                    while len(self._completed) > COMPLETED_CAP:
                        self._completed.popitem(last=False)
                    self._active = None

        threading.Thread(target=_runner_thread, daemon=True).start()
        return run_id

    def status(self, run_id: str) -> Optional[dict]:
        with self._lock:
            if self._active is not None and self._active["run_id"] == run_id:
                return _snapshot(self._active)
            cached = self._completed.get(run_id)
            return _snapshot(cached) if cached else None


def _snapshot(state: dict) -> dict:
    """Return a JSON-safe copy of the run state, used for /status responses."""
    return {
        "run_id": state["run_id"],
        "state": state["state"],
        "log": list(state["log"]),
        "summary": state["summary"],
        "error": state["error"],
    }


# --- Flask routes ---

from pathlib import Path
from typing import Callable
from urllib.parse import urlparse, parse_qs

from flask import Flask, jsonify, render_template, request, send_file

from bidmatch.config import load_config
from bidmatch.pipeline import execute as pipeline_execute


def _extract_sub_token(url: str) -> str | None:
    """Return the sub= query param value, or None if missing/invalid."""
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if not parsed.scheme.startswith("http"):
        return None
    q = parse_qs(parsed.query)
    values = q.get("sub", [])
    return values[0] if values else None


def _default_runner(token: str, days: int, progress: Callable[[str], None]) -> dict:
    """Default runner: load config for non-token settings, then pipeline.execute."""
    from datetime import date
    cfg = load_config()
    output = Path(f"bidmatch_{date.today().isoformat()}.xlsx")
    return pipeline_execute(
        token=token,
        days=days,
        output=output,
        progress=progress,
        no_cache=False,
        skip_pricing=False,
        skip_sam=False,
        sam_api_key=cfg.sam_api_key,
        cache_dir=cfg.cache_dir,
    )


def create_app(
    runner: Callable[[str, int, Callable[[str], None]], dict] = _default_runner,
) -> Flask:
    """Create the Flask app. `runner` is injectable for testing."""
    app = Flask(__name__)
    mgr = RunManager()

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/run", methods=["POST"])
    def run_route():
        body = request.get_json(silent=True) or {}
        url = (body.get("url") or "").strip()
        days = body.get("days")
        if not url:
            return jsonify(error="url is required"), 400
        token = _extract_sub_token(url)
        if not token:
            return jsonify(error="URL must include a sub= query parameter (the token)"), 400
        if not isinstance(days, int) or days < 1 or days > 30:
            return jsonify(error="days must be an integer between 1 and 30"), 400
        run_id = mgr.start(token=token, days=days, runner=runner)
        if run_id is None:
            return jsonify(error="another run is in progress"), 409
        return jsonify(run_id=run_id), 202

    @app.route("/status/<run_id>")
    def status_route(run_id):
        s = mgr.status(run_id)
        if s is None:
            return jsonify(error="unknown run_id"), 404
        return jsonify(s), 200

    @app.route("/download/<run_id>")
    def download_route(run_id):
        s = mgr.status(run_id)
        if s is None:
            return jsonify(error="unknown run_id"), 404
        if s["state"] != "done" or not s["summary"]:
            return jsonify(error="run is not complete"), 409
        path = Path(s["summary"]["output"])
        if not path.exists():
            return jsonify(error="output file not found"), 404
        return send_file(path, as_attachment=True, download_name=path.name)

    return app


def main() -> None:
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    app = create_app()
    app.run(host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
