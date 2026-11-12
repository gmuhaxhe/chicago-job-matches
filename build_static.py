"""Build a static copy of the site (for GitHub Pages) from the job database.

    python build_static.py --db state/jobs.db --out _site

Writes index.html / app.js / style.css, jobs.json (the list the page shows) and
desc/<key>.json (one full description per job, loaded when "Why this score" opens).
Saved / Applied / Hidden marks live in each visitor's browser on the static site.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from jobfinder.store import Store  # noqa: E402

STATIC_FLAG = ('<meta name="robots" content="noindex, nofollow">\n'
               '  <script>window.JOBFINDER_STATIC = true;</script>\n')


def desc_key(uid):
    return hashlib.sha1(uid.encode()).hexdigest()[:16]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(ROOT, "data", "jobs.db"))
    ap.add_argument("--out", default=os.path.join(ROOT, "_site"))
    args = ap.parse_args()

    store = Store(args.db)
    with open(os.path.join(ROOT, "profile.json"), encoding="utf-8") as f:
        profile = json.load(f)

    if os.path.isdir(args.out):
        shutil.rmtree(args.out)
    os.makedirs(os.path.join(args.out, "desc"))

    for name in ("app.js", "style.css"):
        shutil.copy(os.path.join(ROOT, "web", name), os.path.join(args.out, name))
    with open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8") as f:
        page = f.read()
    with open(os.path.join(args.out, "index.html"), "w", encoding="utf-8") as f:
        f.write(page.replace("<title>", STATIC_FLAG + "  <title>", 1))

    jobs = [j for j in store.jobs_for_ui() if not j["closed"]]
    for j in jobs:
        j["desc_key"] = desc_key(j["uid"])
        j.pop("mark", None)
        j.pop("note", None)
        with open(os.path.join(args.out, "desc", j["desc_key"] + ".json"), "w", encoding="utf-8") as f:
            json.dump({"description": store.description(j["uid"]) or ""}, f, ensure_ascii=False)

    last = store.last_run()
    payload = {
        "jobs": jobs,
        "last_run": last,
        "status": {"running": False, "sources": {}, "log": []},
        "home": profile["home"]["label"],
        "name": "",
        "today": dt.date.today().isoformat(),
        "built": dt.datetime.now().isoformat(timespec="seconds"),
    }
    with open(os.path.join(args.out, "jobs.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Built {len(jobs)} jobs into {args.out}")


if __name__ == "__main__":
    main()
