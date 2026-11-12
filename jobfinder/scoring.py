"""Match score (0-100) for a job against the candidate profile in profile.json.

Points: role fit (title) + requirements fit (description) + skills overlap
+ commute + recency. Every adjustment is recorded so the website can show
"why this score".
"""
import datetime as dt
import json
import re

from . import geo

LANGS = ["spanish", "polish", "mandarin", "cantonese", "chinese", "korean", "vietnamese", "arabic", "french", "russian",
         "ukrainian", "tagalog", "hindi", "urdu", "portuguese", "italian", "german", "japanese", "albanian", "serbian",
         "croatian", "bosnian", "greek", "turkish", "american sign language", "asl"]

EVERGREEN = re.compile(r"talent (pool|community|network)|future opportunit|general (application|interest)|expression of interest", re.I)

# Temp / contract detection. These only label a job; they don't change its score.
TEMP_TO_HIRE = re.compile(r"\b(temp(orary)?|contract)[- ]to[- ](hire|perm(anent)?)\b|\bc2h\b", re.I)
TEMP_WORD = r"(temporary|temp|seasonal|interim|fixed[- ]term|limited[- ]term|short[- ]term)"
TEMP_TITLE = re.compile(rf"\b{TEMP_WORD}\b|[(\[]\s*(contract|contractor|freelance)[^)\]]*[)\]]|[-–,]\s*contract(or)?\s*$|^contract\s", re.I)
TEMP_FIELD = re.compile(r"temp|contract|seasonal|fixed[ -]?term|freelance|interim", re.I)
TEMP_DESC = re.compile(
    rf"(employment|job|position|worker( sub)?|assignment) type\s*[:\-]?\s*({TEMP_WORD}|contract(or)?)\b"
    rf"|\b(this|the) (is a |role is a |position is a |job is a )?({TEMP_WORD}|contract) (position|role|assignment|opportunity|job)\b"
    rf"|\b\d{{1,2}}[- ]?(\+ )?(month|week)s?\b[^.\n]{{0,25}}\b(contract|assignment)\b"
    rf"|\b(temporary|contract) (assignment|engagement)\b"
    rf"|\b(anticipated|expected|estimated) end date\b|\bduration of (the )?(assignment|contract)\b",
    re.I)
YEARS = re.compile(r"(?<![\d.$])(\d{1,2})\s*(?:\+|plus)?\s*(?:(?:-|to|–)\s*\d{1,2}\s*)?\+?\s*(?:or more\s*)?years?\b([^.;\n]{0,60})", re.I)


class Scorer:
    def __init__(self, profile_path):
        with open(profile_path, encoding="utf-8") as f:
            self.p = json.load(f)
        c = lambda s: re.compile(s, re.I)
        self.cats = [dict(k, rx=[c(x) for x in k["patterns"]]) for k in self.p["categories"]]
        self.exclude = [c(x) for x in self.p["exclude_title_patterns"]]
        self.seniority = [dict(s, rx=c(s["pattern"])) for s in self.p["seniority"]]
        self.entry = [c(x) for x in self.p["entry_bonus_patterns"]]
        self.skills = [s.lower() for s in self.p["skills"]]
        self.degree_fields = [c(x) for x in self.p["degree_field_patterns"]]
        self.w = self.p["weights"]
        self.home = self.p["home"]
        self.commute_cfg = self.p["commute"]
        self.spoken = set(self.p["languages_spoken"])

    # ---------- title ----------
    def excluded(self, title):
        t = title or ""
        return any(rx.search(t) for rx in self.exclude)

    def role(self, title):
        """(points, category_key, category_label, notes, flags) from the title alone."""
        t = (title or "").strip()
        if self.excluded(t):
            return 0, "excluded", "Not a fit", ["Title is outside her field (licensed / technical / unrelated)"], []
        best = None
        for cat in self.cats:
            if any(rx.search(t) for rx in cat["rx"]):
                if best is None or cat["points"] > best["points"]:
                    best = cat
        if best is None:
            return 5, "other", "Other", ["Title doesn't match her target roles"], []
        max_pts = self.w["role"]
        pts = best["points"] * max_pts / 45.0
        notes = [f"Title fits '{best['label']}'"]
        flags = []
        factor = 1.0
        for s in self.seniority:
            if s["rx"].search(t) and s["factor"] < factor:
                factor = s["factor"]
                if s["flag"]:
                    flags = [s["flag"]]
        if factor < 1:
            notes.append(f"Seniority adjustment x{factor}")
        pts *= factor
        if factor == 1.0 and any(rx.search(t) for rx in self.entry):
            pts = min(max_pts, pts + 3)
            notes.append("Entry-level wording in title (+3)")
        return pts, best["key"], best["label"], notes, flags

    # ---------- description ----------
    def requirements(self, desc):
        max_pts = self.w["requirements"]
        if not desc or len(desc) < 200:
            return max_pts * 0.7, ["Full description not available; assumed average"], ["Description not loaded"]
        d = desc.lower()
        pts, notes, flags = float(max_pts), [], []

        years = []
        for m in YEARS.finditer(desc):
            n, tail = int(m.group(1)), m.group(2).lower()
            if 0 < n <= 12 and "experience" in tail and not re.search(r"\b(our|we|company|history|serving|founded)\b", tail):
                years.append(n)
        if years:
            y = min(years)
            pen = 20 if y >= 5 else 12 if y >= 3 else 5 if y == 2 else 1
            pts -= pen
            notes.append(f"Asks for {y}+ yrs experience (-{pen})")
            if y >= 3:
                flags.append(f"Wants {y}+ yrs experience")
        if re.search(r"\b(entry[- ]level|no (prior )?experience (is )?(required|necessary)|will train|training provided|recent grad)", d):
            pts += 3
            notes.append("Entry-level friendly (+3)")

        if re.search(r"master'?s(\s+degree)?\s+(is\s+)?(required|in\b)|requires? a master", d) and "master's preferred" not in d:
            pts -= 8
            notes.append("Master's degree required (-8)")
            flags.append("Master's required")
        if re.search(r"\b(ph\.?d|psy\.?d|doctorate)\b[^.]{0,40}\brequired", d):
            pts -= 12
            notes.append("Doctorate required (-12)")
            flags.append("Doctorate required")
        if any(rx.search(d) for rx in self.degree_fields) and "bachelor" in d:
            pts += 2
            notes.append("Bachelor's in psychology / related field fits (+2)")

        lic = re.search(r"\b(rn|lpn|lcsw|lcpc|lpc|lmft|cna|cma|cpc|shrm-cp|phr|sphr)\b[^.]{0,50}\b(required|must)\b", d)
        if lic:
            pts -= 6
            notes.append(f"Requires {lic.group(1).upper()} credential (-6)")
            flags.append(f"{lic.group(1).upper()} required")

        for lang in LANGS:
            if lang in self.spoken or lang not in d:
                continue
            ctx = re.search(rf"(bilingual|fluen\w+|speak\w*|proficien\w+)[^.]{{0,60}}\b{lang}\b|\b{lang}\b[^.]{{0,40}}(bilingual|fluen|speak|proficien)", d)
            if ctx:
                req = re.search(r"\b(required|must|necessary)\b", ctx.group(0)) or re.search(rf"\b{lang}\b[^.]{{0,40}}\brequired", d)
                if req:
                    pts -= 8
                    notes.append(f"{lang.title()} required (-8)")
                    flags.append(f"{lang.title()} required")
                else:
                    pts -= 1
                    notes.append(f"{lang.title()} preferred (-1)")
                break
        if "albanian" in d:
            pts += 4
            notes.append("Albanian mentioned (+4)")
            flags.append("Albanian is a plus!")

        if re.search(r"(valid driver'?s licen[sc]e|own (reliable )?(vehicle|car|transportation)|reliable (vehicle|automobile))", d):
            pts -= 2
            notes.append("Car / driver's license mentioned (-2)")
            flags.append("May need a car")
        return max(0.0, min(float(max_pts), pts)), notes, flags

    def skill_overlap(self, desc):
        max_pts = self.w["skills"]
        if not desc or len(desc) < 200:
            return max_pts * 0.5, [], ["no description"]
        hits = []
        for s in self.skills:
            m = re.search(r"\b" + re.escape(s) + r"[\w-]*", desc, re.I)
            if m:
                hits.append(m.group(0).lower())  # show the word as written, e.g. "scheduling" for "schedul"
        pts = max_pts * min(1.0, len(hits) / 10.0)
        return pts, hits, []

    # ---------- place & time ----------
    def commute(self, loc):
        max_pts = self.w["commute"]
        c = self.commute_cfg
        if loc["place"] == "Remote" or (loc["remote"] and loc["in_area"]):
            return max_pts, "Remote - no commute", []
        m = loc["minutes"]
        if m is None:
            return max_pts * 0.5, "Location unclear", ["Location unclear"]
        frac = 1.0 if m <= c["great"] else 0.75 if m <= c["good"] else 0.45 if m <= c["ok"] else 0.2 if m <= c["max"] else 0.0
        flags = [f"Long commute (~{m} min)"] if m > c["ok"] else []
        if loc["hybrid"] and frac < 1:
            frac = 1 - (1 - frac) / 2
        note = f"~{m} min by transit from Pilsen" + (" (hybrid)" if loc["hybrid"] else "")
        if not loc["exact"]:
            note += ", exact site not listed"
        return max_pts * frac, note, flags

    def recency(self, posted, title):
        max_pts = self.w["recency"]
        if EVERGREEN.search(title or ""):
            return max_pts * 0.1, "Evergreen / talent-pool posting", ["Talent-pool posting"]
        if not posted:
            return max_pts * 0.4, "Posting date unknown", []
        try:
            days = (dt.date.today() - dt.date.fromisoformat(posted[:10])).days
        except ValueError:
            return max_pts * 0.4, "Posting date unknown", []
        frac = 1.0 if days <= 3 else 0.85 if days <= 7 else 0.65 if days <= 14 else 0.5 if days <= 21 else 0.35 if days <= 30 else 0.1
        flags = ["Posted 30+ days ago"] if days > 30 else []
        return max_pts * frac, f"Posted {days} day(s) ago", flags

    # ---------- temp / contract ----------
    @staticmethod
    def job_type(job):
        """('Temp-to-hire' | 'Contract' | 'Temp' | None, reason) from title, employment type and description."""
        title, emp, desc = job.get("title") or "", job.get("employment") or "", (job.get("description") or "")[:15000]
        for text, where in ((title, "title"), (emp, "job type"), (desc, "description")):
            m = TEMP_TO_HIRE.search(text)
            if m:
                return "Temp-to-hire", f"{where} says '{m.group(0)}'"
        m = TEMP_TITLE.search(title)
        if m:
            kind = "Contract" if "contract" in m.group(0).lower() or "freelance" in m.group(0).lower() else "Temp"
            return kind, f"title says '{m.group(0).strip(' -,()[]')}'"
        m = TEMP_FIELD.search(emp)
        if m:
            return ("Contract" if m.group(0).lower() in ("contract", "freelance") else "Temp"), f"job type is '{emp}'"
        m = TEMP_DESC.search(desc)
        if m:
            return ("Contract" if "contract" in m.group(0).lower() else "Temp"), f"description says '{m.group(0).strip()}'"
        return None, None

    # ---------- total ----------
    def score(self, job):
        loc = geo.locate(job.get("location"), self.home, job.get("lat"), job.get("lon"))
        if job.get("remote") and not loc["remote"]:
            loc["remote"] = True
            if not loc["in_area"] and not loc["foreign"]:
                loc.update(place="Remote", minutes=0, in_area=True)
        r_pts, cat, cat_label, r_notes, r_flags = self.role(job.get("title"))
        q_pts, q_notes, q_flags = self.requirements(job.get("description"))
        s_pts, s_hits, _ = self.skill_overlap(job.get("description"))
        c_pts, c_note, c_flags = self.commute(loc)
        t_pts, t_note, t_flags = self.recency(job.get("posted"), job.get("title"))
        # A weak title fit shouldn't be rescued by a short commute: scale the other parts by role fit.
        gate = 0.5 + 0.5 * (r_pts / self.w["role"])
        q_pts, s_pts, c_pts, t_pts = (x * gate for x in (q_pts, s_pts, c_pts, t_pts))
        if gate < 0.99:
            r_notes.append(f"Other parts scaled x{gate:.2f} because the title is a partial fit")
        total = 0 if cat == "excluded" else round(r_pts + q_pts + s_pts + c_pts + t_pts)
        jtype, jreason = self.job_type(job)
        if jtype:
            r_notes.append(f"{jtype} job: {jreason} (label only, score unchanged)")
        breakdown = [
            {"part": "Role fit", "points": round(r_pts, 1), "max": self.w["role"], "notes": r_notes},
            {"part": "Requirements", "points": round(q_pts, 1), "max": self.w["requirements"], "notes": q_notes},
            {"part": "Skills overlap", "points": round(s_pts, 1), "max": self.w["skills"],
             "notes": [("Mentions: " + ", ".join(s_hits[:12])) if s_hits else "Few of her skills mentioned"]},
            {"part": "Commute", "points": round(c_pts, 1), "max": self.w["commute"], "notes": [c_note]},
            {"part": "Recency", "points": round(t_pts, 1), "max": self.w["recency"], "notes": [t_note]},
        ]
        return {
            "score": max(0, min(100, total)),
            "category": cat,
            "category_label": cat_label,
            "breakdown": breakdown,
            "flags": r_flags + q_flags + c_flags + t_flags,
            "loc": loc,
            "job_type": jtype,
        }
