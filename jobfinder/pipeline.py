"""One refresh: pull every enabled source, keep Chicago-area / remote jobs in her field, score, store."""
import concurrent.futures as cf
import datetime as dt
import threading
import traceback

import urllib.parse

from . import geo
from .sources import FETCHERS, _workday_parts
from .store import now


def uid_prefix(s):
    """The uid prefix a source's jobs are stored under (must match the adapters in sources.py)."""
    t = s["type"]
    if t == "workday":
        return f"wd:{_workday_parts(s['url'])[1]}:"
    if t == "oracle":
        return f"oracle:{s['host'].split('.')[0]}:"
    if t == "icims":
        return f"icims:{s['host']}:"
    if t == "successfactors":
        return f"sf:{s['host']}:"
    if t == "atom":
        return f"atom:{urllib.parse.urlparse(s['url']).netloc}:"
    if t in ("amazon", "adzuna", "usajobs"):
        return f"{t}:"
    return {"greenhouse": "gh", "lever": "lever", "ashby": "ashby", "smartrecruiters": "sr"}[t] + f":{s['token']}:"


class Context:
    """What a source adapter may use: config, cache lookups, pre-filters, logging."""

    def __init__(self, config, scorer, store, log):
        self.config, self.scorer, self.store, self.log = config, scorer, store, log

    def cached(self, uid):
        return self.store.cached(uid)

    def near_chicago(self, text):
        loc = geo.locate(text, self.scorer.home)
        return loc["in_area"] and loc["place"] != "Remote"

    def worth_details(self, job, skip_location_check=False):
        """Cheap title + location screen before spending a request on a job's full description."""
        pts, cat, *_ = self.scorer.role(job["title"])
        if cat in ("excluded", "other") or pts < 10:
            return False
        if skip_location_check:
            return True
        loc = geo.locate(job["location"], self.scorer.home)
        return (loc["in_area"] or job.get("remote")) and not loc["foreign"]


class Refresher:
    def __init__(self, config_loader, scorer_loader, store):
        self.config_loader, self.scorer_loader, self.store = config_loader, scorer_loader, store
        self.lock = threading.Lock()
        self.status = {"running": False, "log": [], "sources": {}, "started": None, "finished": None}

    def log(self, msg):
        line = f"{dt.datetime.now():%H:%M:%S} {msg}"
        self.status["log"] = (self.status["log"] + [line])[-200:]
        print(line, flush=True)

    def start(self):
        with self.lock:
            if self.status["running"]:
                return False
            self.status = {"running": True, "log": [], "sources": {}, "started": now(), "finished": None}
        threading.Thread(target=self.run, daemon=True).start()
        return True

    def run(self):
        run_id = self.store.start_run()
        started = self.status["started"]
        ok_sources, summary = [], {"new": 0, "kept": 0, "errors": 0}
        try:
            config = self.config_loader()
            scorer = self.scorer_loader()
            ctx = Context(config, scorer, self.store, self.log)
            sources = [s for s in config["sources"] if s.get("enabled", True)]
            for s in sources:
                self.status["sources"][s.get("company") or s["type"]] = {"state": "waiting"}
            self.log(f"Refreshing {len(sources)} sources...")

            def one(src):
                name = src.get("company") or src["type"]
                self.status["sources"][name] = {"state": "fetching"}
                try:
                    jobs = FETCHERS[src["type"]](src, ctx)
                except Exception as e:
                    msg = getattr(e, "reason", None) or str(e)
                    self.status["sources"][name] = {"state": "error", "error": str(msg)[:200]}
                    self.log(f"{name}: FAILED - {msg}")
                    return src, None
                return src, jobs

            with cf.ThreadPoolExecutor(config.get("parallel_sources", 6)) as ex:
                for src, jobs in ex.map(one, sources):
                    name = src.get("company") or src["type"]
                    if jobs is None:
                        summary["errors"] += 1
                        continue
                    kept = new = 0
                    for job in jobs:
                        # e.g. University of Chicago lists just "Chicago, IL"; its jobs are mostly in Hyde Park.
                        if src.get("site_hint") and geo.locate(job["location"], scorer.home)["place"] == geo.UNLISTED:
                            job["location"] = f"{job['location']} ({src['site_hint']})"
                        res = scorer.score(job)
                        if res["category"] in ("excluded", "other") or not res["loc"]["in_area"] or res["loc"]["foreign"]:
                            continue
                        kept += 1
                        new += self.store.upsert(job, res, now())
                    summary["new"] += new
                    summary["kept"] += kept
                    self.status["sources"][name] = {"state": "ok", "listed": len(jobs), "kept": kept, "new": new}
                    self.log(f"{name}: {len(jobs)} listed, {kept} match her field & area, {new} new")
                    ok_sources.append(src)

            self.store.close_missing([(s, uid_prefix(s)) for s in ok_sources], started)
            self.log(f"Done: {summary['kept']} matching jobs ({summary['new']} new), {summary['errors']} source errors.")
            self.store.finish_run(run_id, True, dict(summary, sources=self.status["sources"], log=self.status["log"]))
        except Exception:
            self.log("Refresh crashed:\n" + traceback.format_exc())
            self.store.finish_run(run_id, False, dict(summary, sources=self.status["sources"], log=self.status["log"]))
        finally:
            self.status["running"] = False
            self.status["finished"] = now()

    def rescore_all(self):
        scorer = self.scorer_loader()
        hints = [(uid_prefix(s), s["site_hint"]) for s in self.config_loader()["sources"] if s.get("site_hint")]
        n = 0
        for row in self.store.all_rows():
            for prefix, hint in hints:
                if row["uid"].startswith(prefix) and geo.locate(row["location"], scorer.home)["place"] == geo.UNLISTED:
                    row["location"] = f"{row['location']} ({hint})"
            res = scorer.score(row)
            if res["category"] in ("excluded", "other"):
                self.store.delete(row["uid"])
                continue
            self.store.rescore(row["uid"], res, row["location"])
            n += 1
        return n
