"""SQLite storage for jobs, the candidate's status marks (saved / applied / hidden) and refresh runs."""
import datetime as dt
import json
import re
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    uid TEXT PRIMARY KEY,
    source TEXT, company TEXT, setting TEXT, title TEXT, location TEXT, url TEXT,
    posted TEXT, description TEXT, remote INTEGER, employment TEXT, salary TEXT, lat REAL, lon REAL,
    first_seen TEXT, last_seen TEXT, closed INTEGER DEFAULT 0,
    score INTEGER, category TEXT, category_label TEXT, breakdown TEXT, flags TEXT, loc TEXT
);
CREATE TABLE IF NOT EXISTS marks (uid TEXT PRIMARY KEY, status TEXT, note TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, started TEXT, finished TEXT, ok INTEGER, summary TEXT
);
CREATE INDEX IF NOT EXISTS jobs_score ON jobs(score);
"""

FIELDS = ["source", "company", "setting", "title", "location", "url", "posted", "description", "remote",
          "employment", "salary", "lat", "lon"]


def now():
    return dt.datetime.now().isoformat(timespec="seconds")


# Headings that usually start the part of a posting about the job itself (after the company boilerplate).
ROLE_START = re.compile(
    r"(job summary|position summary|position overview|about the (role|position|job|opportunity)|about this (role|position)|"
    r"the role|your role|role overview|job description|what you('|’)ll do|what you will do|responsibilities|"
    r"essential (duties|functions)|primary duties|duties|overview|summary)\s*[:\n]", re.I)


def snippet(desc, n=700):
    m = ROLE_START.search(desc[:6000])
    start = m.end() if m and m.start() > 80 else 0
    return desc[start:start + n].strip()


class Store:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(jobs)")}
        if "job_type" not in cols:  # added after the first version: Temp / Contract / Temp-to-hire
            self.db.execute("ALTER TABLE jobs ADD COLUMN job_type TEXT")
        self.db.commit()

    def cached(self, uid):
        with self.lock:
            r = self.db.execute("SELECT * FROM jobs WHERE uid=?", (uid,)).fetchone()
        return dict(r) if r else None

    def upsert(self, job, result, seen_at):
        vals = [job.get(f) for f in FIELDS]
        vals[FIELDS.index("remote")] = int(bool(job.get("remote")))
        scored = [result["score"], result["category"], result["category_label"], json.dumps(result["breakdown"]),
                  json.dumps(result["flags"]), json.dumps(result["loc"]), result.get("job_type")]
        with self.lock:
            exists = self.db.execute("SELECT 1 FROM jobs WHERE uid=?", (job["uid"],)).fetchone()
            if exists:
                sets = ", ".join(f"{f}=?" for f in FIELDS)
                self.db.execute(
                    f"UPDATE jobs SET {sets}, last_seen=?, closed=0, score=?, category=?, category_label=?, breakdown=?, "
                    f"flags=?, loc=?, job_type=? WHERE uid=?", vals + [seen_at] + scored + [job["uid"]])
            else:
                self.db.execute(
                    f"INSERT INTO jobs (uid, {', '.join(FIELDS)}, first_seen, last_seen, closed, score, category, "
                    f"category_label, breakdown, flags, loc, job_type) "
                    f"VALUES ({', '.join('?' * (len(FIELDS) + 1))}, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)",
                    [job["uid"]] + vals + [seen_at, seen_at] + scored)
            self.db.commit()
        return not exists

    def close_missing(self, source_keys, run_started):
        """Mark jobs closed when a source that fetched fine no longer lists them."""
        with self.lock:
            for src, prefix in source_keys:
                self.db.execute("UPDATE jobs SET closed=1 WHERE uid LIKE ? AND last_seen < ? AND closed=0",
                                (prefix + "%", run_started))
            self.db.commit()

    def all_rows(self):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM jobs")]

    def rescore(self, uid, result, location):
        with self.lock:
            self.db.execute("UPDATE jobs SET score=?, category=?, category_label=?, breakdown=?, flags=?, loc=?, location=?, "
                            "job_type=? WHERE uid=?",
                            (result["score"], result["category"], result["category_label"], json.dumps(result["breakdown"]),
                             json.dumps(result["flags"]), json.dumps(result["loc"]), location, result.get("job_type"), uid))
            self.db.commit()

    def delete(self, uid):
        with self.lock:
            self.db.execute("DELETE FROM jobs WHERE uid=?", (uid,))
            self.db.commit()

    def jobs_for_ui(self, include_closed=False):
        q = ("SELECT j.*, m.status AS mark, m.note AS note FROM jobs j LEFT JOIN marks m ON m.uid=j.uid "
             + ("" if include_closed else "WHERE j.closed=0 OR m.status IN ('saved','applied') ")
             + "ORDER BY j.score DESC")
        with self.lock:
            rows = [dict(r) for r in self.db.execute(q)]
        out = []
        for r in rows:
            desc = r.pop("description") or ""
            r["snippet"] = snippet(desc)
            r["has_description"] = len(desc) > 200
            for k in ("breakdown", "flags", "loc"):
                r[k] = json.loads(r[k]) if r[k] else None
            out.append(r)
        return out

    def description(self, uid):
        with self.lock:
            r = self.db.execute("SELECT description FROM jobs WHERE uid=?", (uid,)).fetchone()
        return r["description"] if r else None

    def set_mark(self, uid, status, note=None):
        with self.lock:
            if status in (None, "", "none"):
                self.db.execute("DELETE FROM marks WHERE uid=?", (uid,))
            else:
                self.db.execute("INSERT INTO marks (uid, status, note, updated) VALUES (?, ?, ?, ?) "
                                "ON CONFLICT(uid) DO UPDATE SET status=excluded.status, "
                                "note=COALESCE(excluded.note, marks.note), updated=excluded.updated",
                                (uid, status, note, now()))
            self.db.commit()

    def start_run(self):
        with self.lock:
            cur = self.db.execute("INSERT INTO runs (started, ok) VALUES (?, 0)", (now(),))
            self.db.commit()
            return cur.lastrowid

    def finish_run(self, run_id, ok, summary):
        with self.lock:
            self.db.execute("UPDATE runs SET finished=?, ok=?, summary=? WHERE id=?", (now(), int(ok), json.dumps(summary), run_id))
            self.db.commit()

    def last_run(self):
        with self.lock:
            r = self.db.execute("SELECT * FROM runs WHERE finished IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
        if not r:
            return None
        r = dict(r)
        r["summary"] = json.loads(r["summary"]) if r["summary"] else None
        return r
