"""Job finder - local website.

    python server.py              start the site at http://127.0.0.1:8765 and open the browser
    python server.py --no-browser start without opening a browser tab
    python server.py --fetch-once refresh the job list once and exit (Task Scheduler / GitHub Actions)
    --db PATH                     use another database file (default data/jobs.db)
"""
import argparse
import datetime as dt
import json
import mimetypes
import os
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from jobfinder.pipeline import Refresher  # noqa: E402
from jobfinder.scoring import Scorer  # noqa: E402
from jobfinder.store import Store  # noqa: E402

CONFIG = os.path.join(ROOT, "config.json")
PROFILE = os.path.join(ROOT, "profile.json")
PERSONAL = os.path.join(ROOT, "personal.json")  # not in git: {"name": "..."} shown in the page title
WEB = os.path.join(ROOT, "web")
DEFAULT_DB = os.path.join(ROOT, "data", "jobs.db")

store = refresher = None  # set in main()


SECRETS = os.path.join(ROOT, "secrets.json")  # not in git: API keys for the optional key-based sources
SECRET_ENV = {"adzuna": {"app_id": "ADZUNA_APP_ID", "app_key": "ADZUNA_APP_KEY"},
              "usajobs": {"email": "USAJOBS_EMAIL", "api_key": "USAJOBS_API_KEY"}}


def load_config():
    """config.json plus API keys from secrets.json or environment variables (GitHub Actions secrets).
    A key-based source switches itself on once its keys are present."""
    with open(CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    try:
        with open(SECRETS, encoding="utf-8") as f:
            secrets = json.load(f)
    except (OSError, ValueError):
        secrets = {}
    for s in cfg["sources"]:
        fields = SECRET_ENV.get(s["type"])
        if not fields:
            continue
        for field, env in fields.items():
            s[field] = os.environ.get(env) or (secrets.get(s["type"]) or {}).get(field, "")
        s["enabled"] = all(s[f] for f in fields)
    return cfg


def load_scorer():
    return Scorer(PROFILE)


def personal_name():
    try:
        with open(PERSONAL, encoding="utf-8") as f:
            return json.load(f).get("name", "")
    except (OSError, ValueError):
        return ""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # keep the console readable
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/api/jobs":
            prof = load_scorer().p
            return self._send(200, {
                "jobs": store.jobs_for_ui(include_closed=q.get("closed") == ["1"]),
                "last_run": store.last_run(),
                "status": refresher.status,
                "home": prof["home"]["label"],
                "name": personal_name(),
                "today": dt.date.today().isoformat(),
            })
        if u.path == "/api/status":
            return self._send(200, refresher.status)
        if u.path == "/api/job":
            return self._send(200, {"description": store.description(q.get("uid", [""])[0])})
        path = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
        full = os.path.normpath(os.path.join(WEB, path))
        if not full.startswith(WEB) or not os.path.isfile(full):
            return self._send(404, {"error": "not found"})
        with open(full, "rb") as f:
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            return self._send(200, f.read(), ctype)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/refresh":
            return self._send(200, {"started": refresher.start()})
        if u.path == "/api/mark":
            b = self._body()
            store.set_mark(b["uid"], b.get("status"), b.get("note"))
            return self._send(200, {"ok": True})
        if u.path == "/api/rescore":
            return self._send(200, {"count": refresher.rescore_all()})
        return self._send(404, {"error": "not found"})


def auto_refresh_loop():
    """Refresh at startup if the data is stale, then every N hours while the site is running."""
    while True:
        hours = load_config().get("auto_refresh_hours", 12)
        last = store.last_run()
        stale = True
        if last and last.get("finished"):
            age = dt.datetime.now() - dt.datetime.fromisoformat(last["finished"])
            stale = age > dt.timedelta(hours=hours)
        if stale and not refresher.status["running"]:
            refresher.start()
        time.sleep(600)


def main():
    global store, refresher
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--fetch-once", action="store_true")
    ap.add_argument("--db", default=DEFAULT_DB)
    args = ap.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    store = Store(args.db)
    refresher = Refresher(load_config, load_scorer, store)

    if args.fetch_once:
        refresher.status["started"] = dt.datetime.now().isoformat(timespec="seconds")
        refresher.status["running"] = True
        refresher.run()
        return

    port = load_config().get("port", 8765)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Job finder running at {url}  (close this window to stop)", flush=True)
    threading.Thread(target=auto_refresh_loop, daemon=True).start()
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
