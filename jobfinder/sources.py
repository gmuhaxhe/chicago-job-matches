"""Job feed adapters. Each returns a list of normalized job dicts:

uid, source, company, setting, title, location, url, posted (YYYY-MM-DD or None),
description (plain text), remote (bool), employment, salary, lat, lon
"""
import datetime as dt
import email.utils
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

UA = "ChicagoJobMatches/1.0 (personal job-search script)"


# ---------------------------------------------------------------- helpers
class _Text(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "tr", "section"}

    def __init__(self):
        super().__init__()
        self.out = []

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCK:
            self.out.append("\n")
        if tag == "li":
            self.out.append("- ")

    def handle_data(self, data):
        self.out.append(data)


def html_to_text(s):
    if not s:
        return ""
    if "&lt;" in s[:500]:
        s = html.unescape(s)
    p = _Text()
    p.feed(s)
    text = html.unescape("".join(p.out))
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def http_text(url, data=None, headers=None, accept="application/json", timeout=25, retries=2):
    h = {"User-Agent": UA, "Accept": accept}
    if headers:
        h.update(headers)
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    last = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=body, headers=h, method="POST" if body else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (400, 401, 403, 404, 406, 422):
                raise
        except Exception as e:  # network hiccup / timeout
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise last


def http_json(url, data=None, headers=None, timeout=25, retries=2):
    return json.loads(http_text(url, data, headers, "application/json", timeout, retries))


def http_xml(url):
    return ET.fromstring(http_text(url, accept="application/rss+xml, application/atom+xml, */*"))


def iso_date(v):
    """Accept ISO strings or epoch milliseconds; return YYYY-MM-DD."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return dt.datetime.fromtimestamp(v / 1000, dt.timezone.utc).date().isoformat()
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(v))
    return m.group(1) if m else None


def _job(src, **kw):
    base = {"source": src["type"], "company": src.get("company") or src.get("token"), "setting": src.get("setting", ""),
            "remote": False, "employment": "", "salary": "", "lat": None, "lon": None, "posted": None, "description": ""}
    base.update(kw)
    return base


# ---------------------------------------------------------------- Greenhouse
def greenhouse(src, ctx):
    d = http_json(f"https://boards-api.greenhouse.io/v1/boards/{src['token']}/jobs?content=true")
    out = []
    for j in d.get("jobs", []):
        loc = (j.get("location") or {}).get("name") or ""
        offices = "; ".join(o.get("name", "") for o in j.get("offices") or [] if o.get("name"))
        out.append(_job(src, uid=f"gh:{src['token']}:{j['id']}", title=j.get("title", ""),
                        location=loc + (("; " + offices) if offices and offices not in loc else ""),
                        url=j.get("absolute_url"), posted=iso_date(j.get("first_published") or j.get("updated_at")),
                        description=html_to_text(j.get("content")), remote="remote" in loc.lower(),
                        company=j.get("company_name") or src.get("company")))
    return out


# ---------------------------------------------------------------- Lever
def lever(src, ctx):
    host = "api.eu.lever.co" if src.get("region") == "eu" else "api.lever.co"
    d = http_json(f"https://{host}/v0/postings/{src['token']}?mode=json")
    out = []
    for j in d:
        cats = j.get("categories") or {}
        locs = cats.get("allLocations") or ([cats["location"]] if cats.get("location") else [])
        parts = [j.get("descriptionPlain") or ""]
        for lst in j.get("lists") or []:
            parts.append(lst.get("text", "") + "\n" + html_to_text(lst.get("content", "")))
        parts.append(j.get("additionalPlain") or "")
        sal = j.get("salaryRange") or {}
        out.append(_job(src, uid=f"lever:{src['token']}:{j['id']}", title=j.get("text", ""), location="; ".join(locs),
                        url=j.get("hostedUrl"), posted=iso_date(j.get("createdAt")), description="\n\n".join(parts).strip(),
                        remote=(j.get("workplaceType") == "remote"), employment=cats.get("commitment", ""),
                        salary=(f"${sal['min']:,}-${sal['max']:,} {sal.get('interval', '')}" if sal.get("min") else "")))
        if j.get("workplaceType") == "hybrid":
            out[-1]["location"] += " (Hybrid)"
    return out


# ---------------------------------------------------------------- Ashby
def ashby(src, ctx):
    d = http_json(f"https://api.ashbyhq.com/posting-api/job-board/{src['token']}?includeCompensation=true")
    out = []
    for j in d.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location") or ""] + [x.get("location", "") for x in j.get("secondaryLocations") or []]
        loc = "; ".join(l for l in locs if l)
        if j.get("workplaceType") == "Hybrid":
            loc += " (Hybrid)"
        comp = (j.get("compensation") or {}).get("compensationTierSummary") or ""
        out.append(_job(src, uid=f"ashby:{src['token']}:{j['id']}", title=j.get("title", ""), location=loc,
                        url=j.get("jobUrl"), posted=iso_date(j.get("publishedAt")), description=j.get("descriptionPlain") or "",
                        # isRemote is true when *any* location is remote; the location text already carries that.
                        remote=j.get("workplaceType") == "Remote",
                        employment=j.get("employmentType") or "", salary=comp))
    return out


def _with_details(ctx, src, job, fetch):
    """Reuse a stored description when we have one; otherwise call fetch(job) to fill it in."""
    cached = ctx.cached(job["uid"])
    if cached and cached.get("description"):
        for k in ("description", "location", "lat", "lon", "employment"):
            if cached.get(k) not in (None, ""):
                job[k] = cached[k]
        job["remote"] = bool(cached.get("remote")) or job["remote"]
        job["posted"] = cached.get("posted") or job["posted"]
        return job
    try:
        fetch(job)
        time.sleep(0.15)
    except Exception as e:
        ctx.log(f"{src.get('company')}: detail failed for {job['title']}: {e}")
    return job


def _queries(src, ctx):
    return src.get("queries") or ctx.config.get("workday_queries", [])


# ---------------------------------------------------------------- SmartRecruiters
def smartrecruiters(src, ctx):
    cid = src["token"]
    out, seen = [], set()

    def fill(job):
        det = http_json(f"https://api.smartrecruiters.com/v1/companies/{cid}/postings/{job['_id']}")
        secs = (det.get("jobAd") or {}).get("sections") or {}
        job["description"] = "\n\n".join(html_to_text((secs.get(k) or {}).get("text")) for k in
                                         ("jobDescription", "qualifications", "additionalInformation", "companyDescription"))

    for q in _queries(src, ctx):
        for page in range(src.get("pages", 1)):
            d = http_json(f"https://api.smartrecruiters.com/v1/companies/{cid}/postings?"
                          + urllib.parse.urlencode({"q": q, "limit": 100, "offset": page * 100, "country": "us"}))
            for j in d.get("content", []):
                if j["id"] in seen:
                    continue
                seen.add(j["id"])
                l = j.get("location") or {}
                loc = ", ".join(x for x in (l.get("address"), l.get("city"), " ".join(filter(None, (l.get("region"), l.get("postalCode")))))
                                if x) or l.get("fullLocation", "")
                if l.get("hybrid"):
                    loc += " (Hybrid)"
                job = _job(src, uid=f"sr:{cid}:{j['id']}", title=j.get("name", ""), location=loc, _id=j["id"],
                           url=f"https://jobs.smartrecruiters.com/{cid}/{j['id']}", posted=iso_date(j.get("releasedDate")),
                           remote=bool(l.get("remote")), employment=(j.get("typeOfEmployment") or {}).get("label", ""),
                           company=(j.get("company") or {}).get("name") or src.get("company"))
                if ctx.worth_details(job):
                    out.append(_with_details(ctx, src, job, fill))
            if (page + 1) * 100 >= d.get("totalFound", 0):
                break
        time.sleep(0.2)
    return out


# ---------------------------------------------------------------- Oracle Recruiting Cloud
def oracle(src, ctx):
    host, site = src["host"], src["site"]
    base = f"https://{host}/hcmRestApi/resources/latest"
    out, seen = [], set()

    def fill(job):
        url = (f"{base}/recruitingCEJobRequisitionDetails?expand=all&onlyData=true&finder=ById;"
               f"Id=%22{job['_id']}%22,siteNumber={site}")
        it = (http_json(url).get("items") or [{}])[0]
        job["description"] = "\n\n".join(html_to_text(it.get(k)) for k in
                                         ("ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr") if it.get(k))
        wl = (it.get("workLocation") or [{}])[0]
        addr = ", ".join(x for x in (wl.get("AddressLine1"), wl.get("TownOrCity"),
                                     " ".join(filter(None, (wl.get("Region2"), wl.get("PostalCode"))))) if x)
        if addr:
            job["location"] = addr
        flex = " ".join(str(f.get("Value", "")) for f in it.get("requisitionFlexFields") or [])
        if re.search(r"\bremote\b", flex, re.I):
            job["remote"] = True
        elif re.search(r"\bhybrid\b", flex, re.I):
            job["location"] += " (Hybrid)"
        job["employment"] = it.get("JobSchedule") or ""

    for q in _queries(src, ctx):
        for page in range(src.get("pages", 2)):
            finder = (f"findReqs;siteNumber={site},keyword={urllib.parse.quote(q)},limit=25,offset={page * 25},"
                      f"sortBy=POSTING_DATES_DESC")
            d = http_json(f"{base}/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList.secondaryLocations&finder={finder}")
            item = (d.get("items") or [{}])[0]
            reqs = item.get("requisitionList") or []
            for r in reqs:
                if r["Id"] in seen:
                    continue
                seen.add(r["Id"])
                locs = [r.get("PrimaryLocation") or ""] + [s.get("Name", "") for s in r.get("secondaryLocations") or []]
                job = _job(src, uid=f"oracle:{host.split('.')[0]}:{r['Id']}", title=r.get("Title", ""), _id=r["Id"],
                           location="; ".join(l for l in locs if l), posted=iso_date(r.get("PostedDate")),
                           url=f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{r['Id']}")
                # "IL, United States" names no site, so the detail call decides the area.
                if ctx.worth_details(job, skip_location_check=not re.search(r"[A-Za-z]+, IL", job["location"])):
                    out.append(_with_details(ctx, src, job, fill))
            if len(reqs) < 25:
                break
        time.sleep(0.2)
    return out


# ---------------------------------------------------------------- iCIMS / Jibe career sites with /api/jobs
def icims(src, ctx):
    host = src["host"]
    out, seen = [], set()
    for q in _queries(src, ctx):
        for page in range(1, src.get("pages", 2) + 1):
            d = http_json(f"https://{host}/api/jobs?" + urllib.parse.urlencode({"keywords": q, "limit": 100, "page": page}))
            jobs = d.get("jobs") or []
            for w in jobs:
                j = w.get("data") or {}
                key = j.get("req_id") or j.get("slug")
                if key in seen:
                    continue
                seen.add(key)
                loc = j.get("full_location") or ", ".join(x for x in (j.get("city"), j.get("state")) if x)
                tags = " ".join(str(j.get(t, "")) for t in ("tags1", "tags2", "tags3", "tags4", "tags5"))
                if re.search(r"\bhybrid\b", tags, re.I):
                    loc += " (Hybrid)"
                desc = "\n\n".join(html_to_text(j.get(k)) for k in ("description", "qualifications") if j.get(k))
                out.append(_job(src, uid=f"icims:{host}:{key}", title=j.get("title", ""), location=loc,
                                url=f"https://{host}/jobs/{j.get('slug') or key}?lang=en-us", posted=iso_date(j.get("posted_date")),
                                description=desc, lat=j.get("latitude"), lon=j.get("longitude"),
                                remote=bool(re.search(r"\bremote\b", tags + " " + loc, re.I)),
                                employment=(j.get("employment_type") or "").replace("_", " ").title(),
                                company=src.get("company") or j.get("hiring_organization")))
            if len(jobs) < 100:
                break
        time.sleep(0.2)
    return out


# ---------------------------------------------------------------- SAP SuccessFactors RSS
def successfactors(src, ctx):
    host = src["host"]
    out, seen = [], set()
    for q in _queries(src, ctx):
        root = http_xml(f"https://{host}/services/rss/job/?" + urllib.parse.urlencode({"locale": "en_US", "keywords": q}))
        for it in root.iter("item"):
            link = (it.findtext("link") or "").strip()
            if not link or link in seen:
                continue
            seen.add(link)
            raw = (it.findtext("title") or "").strip()
            m = re.match(r"(.*)\(([^()]*)\)\s*$", raw)  # "Job Title (Chicago, IL, US, 60607)"
            title, loc = (m.group(1).strip(), m.group(2)) if m else (raw, "")
            posted = None
            if it.findtext("pubDate"):
                try:
                    posted = email.utils.parsedate_to_datetime(it.findtext("pubDate")).date().isoformat()
                except (TypeError, ValueError):
                    pass
            m_id = re.search(r"/(\d+)/?$", link)
            out.append(_job(src, uid=f"sf:{host}:{m_id.group(1) if m_id else link}", title=title, location=loc, url=link,
                            posted=posted, description=html_to_text(it.findtext("description"))))
        time.sleep(0.3)
    return out


# ---------------------------------------------------------------- Atom feeds (PeopleAdmin, Sinai timeline, ...)
def atom(src, ctx):
    ns = {"a": "http://www.w3.org/2005/Atom"}
    urls = [src["url"].replace("{q}", urllib.parse.quote(q)) for q in _queries(src, ctx)] if "{q}" in src["url"] else [src["url"]]
    out, seen = [], set()
    for url in urls:
        root = http_xml(url)
        for e in root.findall("a:entry", ns):
            link_el = e.find("a:link[@rel='alternate']", ns)
            if link_el is None:
                link_el = e.find("a:link", ns)
            link = link_el.get("href") if link_el is not None else e.findtext("a:id", "", ns)
            if link in seen:
                continue
            seen.add(link)
            out.append(_job(src, uid=f"atom:{urllib.parse.urlparse(link).netloc}:{link.rstrip('/').split('/')[-1]}",
                            title=(e.findtext("a:title", "", ns) or "").strip(), location=src.get("default_location", ""),
                            url=link, posted=iso_date(e.findtext("a:published", "", ns) or e.findtext("a:updated", "", ns)),
                            description=html_to_text(e.findtext("a:content", "", ns) or e.findtext("a:summary", "", ns))))
        time.sleep(0.3)
    return out


# ---------------------------------------------------------------- amazon.jobs
def amazon(src, ctx):
    out, seen = [], set()
    for q in src.get("queries") or [""]:
        for page in range(src.get("pages", 5)):
            params = urllib.parse.urlencode({"base_query": q, "normalized_location[]": "Chicago, Illinois, USA",
                                             "result_limit": 100, "offset": page * 100, "sort": "recent"})
            d = http_json(f"https://www.amazon.jobs/en/search.json?{params}")
            jobs = d.get("jobs") or []
            for j in jobs:
                if j["id"] in seen:
                    continue
                seen.add(j["id"])
                cities = []
                for l in j.get("locations") or []:
                    try:
                        l = json.loads(l) if isinstance(l, str) else l
                        cities.append(f"{l.get('city')}, {l.get('normalizedStateName')}")
                    except (ValueError, AttributeError):
                        pass
                loc = "Chicago, Illinois" if any(c.startswith("Chicago,") for c in cities) else j.get("normalized_location", "")
                try:
                    posted = dt.datetime.strptime(j.get("posted_date", ""), "%B %d, %Y").date().isoformat()
                except ValueError:
                    posted = None
                desc = "\n\n".join(html_to_text(j.get(k)) for k in ("description", "basic_qualifications", "preferred_qualifications"))
                out.append(_job(src, uid=f"amazon:{j.get('id_icims') or j['id']}", title=j.get("title", ""), location=loc,
                                url="https://www.amazon.jobs" + j.get("job_path", ""), posted=posted, description=desc,
                                company=j.get("company_name") or "Amazon", employment=j.get("job_schedule_type") or ""))
            if len(jobs) < 100:
                break
            time.sleep(0.3)
    return out


# ---------------------------------------------------------------- Workday
def _workday_parts(url):
    """https://acme.wd5.myworkdayjobs.com/en-US/Careers -> (origin, tenant, site)."""
    u = urllib.parse.urlparse(url)
    path = [p for p in u.path.split("/") if p and not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", p)]
    origin = f"{u.scheme}://{u.netloc}"
    if "myworkdaysite.com" in u.netloc:  # .../recruiting/{tenant}/{site}
        i = path.index("recruiting")
        return origin, path[i + 1], path[i + 2]
    return origin, u.netloc.split(".")[0], path[0]


def _workday_posted(text):
    t = (text or "").lower()
    today = dt.date.today()
    if "today" in t:
        return today.isoformat()
    if "yesterday" in t:
        return (today - dt.timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\+?\s*days?", t)
    if m:
        return (today - dt.timedelta(days=int(m.group(1)))).isoformat()
    return None


def _workday_location_facets(facets, in_area):
    """Find Chicago-area location facet values so searches can be narrowed to them."""
    found = {}

    def walk(fs):
        for f in fs or []:
            param = f.get("facetParameter", "")
            vals = f.get("values") or []
            if vals and "facetParameter" in vals[0]:
                walk(vals)
                continue
            if "location" not in param.lower():
                continue
            ids = [v["id"] for v in vals if v.get("id") and in_area(v.get("descriptor", ""))]
            if ids:
                found.setdefault(param, []).extend(ids)

    walk(facets)
    if not found:
        return {}
    # Prefer the most specific location facet (the one with the most matching values).
    param = max(found, key=lambda k: len(found[k]))
    return {param: found[param]}


def workday(src, ctx):
    origin, tenant, site = _workday_parts(src["url"])
    api = f"{origin}/wday/cxs/{tenant}/{site}"
    queries = src.get("queries") or ctx.config.get("workday_queries", [])
    first = http_json(f"{api}/jobs", {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""})
    facets = _workday_location_facets(first.get("facets"), ctx.near_chicago)
    seen, listing = set(), []
    pages = src.get("pages", 2)
    for q in queries:
        for page in range(pages):
            try:
                d = http_json(f"{api}/jobs", {"appliedFacets": facets, "limit": 20, "offset": page * 20, "searchText": q})
            except urllib.error.HTTPError as e:
                ctx.log(f"{src.get('company')}: search '{q}' failed ({e.code})")
                break
            posts = d.get("jobPostings") or []
            for p in posts:
                path = p.get("externalPath")
                if path and path not in seen:
                    seen.add(path)
                    listing.append(p)
            if len(posts) < 20 or (page + 1) * 20 >= (d.get("total") or 0):
                break
            time.sleep(0.2)
        time.sleep(0.2)

    out = []
    for p in listing:
        path = p["externalPath"]
        req_id = next((b for b in p.get("bulletFields") or [] if re.search(r"\d", b)), path)
        job = _job(src, uid=f"wd:{tenant}:{req_id}", title=p.get("title", ""), location=p.get("locationsText", ""),
                   url=f"{origin}/en-US/{site}{path}", posted=_workday_posted(p.get("postedOn")))
        # "3 Locations" hides the sites until the detail call; a Chicago facet already guarantees the area.
        multi = re.match(r"\d+ locations", job["location"].lower())
        if not ctx.worth_details(job, skip_location_check=bool(multi) or bool(facets)):
            continue
        cached = ctx.cached(job["uid"])
        if cached and cached.get("description"):
            for k in ("description", "location", "remote", "employment"):
                job[k] = cached.get(k, job[k])
            job["posted"] = cached.get("posted") or job["posted"]
        else:
            try:
                det = http_json(f"{api}{path}")
                info = det.get("jobPostingInfo") or {}
                locs = [info.get("location") or ""] + list(info.get("additionalLocations") or [])
                job["location"] = "; ".join(l for l in locs if l) or job["location"]
                job["description"] = html_to_text(info.get("jobDescription"))
                job["posted"] = iso_date(info.get("startDate")) or job["posted"]
                job["employment"] = info.get("timeType") or ""
                job["remote"] = "remote" in (info.get("remoteType") or "").lower()
                if info.get("remoteType") and "hybrid" in info["remoteType"].lower():
                    job["location"] += " (Hybrid)"
                if info.get("externalUrl"):
                    job["url"] = info["externalUrl"]
                hiring = (det.get("hiringOrganization") or {}).get("name")
                if hiring and src.get("use_hiring_org"):
                    job["company"] = hiring
                time.sleep(0.15)
            except Exception as e:
                ctx.log(f"{src.get('company')}: detail failed for {job['title']}: {e}")
        out.append(job)
    return out


# ---------------------------------------------------------------- Adzuna (needs free API key)
def adzuna(src, ctx):
    if not (src.get("app_id") and src.get("app_key")):
        raise RuntimeError("add app_id and app_key in config.json (free at developer.adzuna.com)")
    out, seen = [], set()
    for q in src.get("queries") or ctx.config.get("search_queries", []):
        for page in range(1, src.get("pages", 2) + 1):
            params = urllib.parse.urlencode({
                "app_id": src["app_id"], "app_key": src["app_key"], "what": q, "where": src.get("where", "Chicago, IL"),
                "distance": src.get("distance_km", 25), "max_days_old": src.get("max_days_old", 30),
                "results_per_page": 50, "sort_by": "date", "content-type": "application/json"})
            d = http_json(f"https://api.adzuna.com/v1/api/jobs/us/search/{page}?{params}")
            res = d.get("results") or []
            for j in res:
                if j["id"] in seen:
                    continue
                seen.add(j["id"])
                sal = ""
                if j.get("salary_min"):
                    sal = f"${int(j['salary_min']):,}-${int(j.get('salary_max') or j['salary_min']):,}"
                    sal += " (estimate)" if str(j.get("salary_is_predicted")) == "1" else ""
                out.append(_job(src, uid=f"adzuna:{j['id']}", title=html_to_text(j.get("title")),
                                company=(j.get("company") or {}).get("display_name") or "Unknown",
                                location=(j.get("location") or {}).get("display_name", ""), url=j.get("redirect_url"),
                                posted=iso_date(j.get("created")), description=html_to_text(j.get("description")),
                                lat=j.get("latitude"), lon=j.get("longitude"), salary=sal,
                                employment=j.get("contract_time") or ""))
            if len(res) < 50:
                break
            time.sleep(0.5)
    return out


# ---------------------------------------------------------------- USAJobs (needs free API key)
def usajobs(src, ctx):
    if not (src.get("email") and src.get("api_key")):
        raise RuntimeError("add email and api_key in config.json (free at developer.usajobs.gov)")
    headers = {"Host": "data.usajobs.gov", "User-Agent": src["email"], "Authorization-Key": src["api_key"]}
    out, seen = [], set()
    for q in src.get("queries", []):
        params = urllib.parse.urlencode({"Keyword": q, "LocationName": "Chicago, Illinois", "Radius": 25,
                                         "DatePosted": 30, "ResultsPerPage": 100})
        d = http_json(f"https://data.usajobs.gov/api/search?{params}", headers=headers)
        for item in (d.get("SearchResult") or {}).get("SearchResultItems") or []:
            m = item.get("MatchedObjectDescriptor") or {}
            if m.get("PositionID") in seen:
                continue
            seen.add(m.get("PositionID"))
            locs = m.get("PositionLocation") or []
            chi = next((l for l in locs if "chicago" in (l.get("LocationName") or "").lower()), locs[0] if locs else {})
            details = (m.get("UserArea") or {}).get("Details") or {}
            pay = (m.get("PositionRemuneration") or [{}])[0]
            out.append(_job(src, uid=f"usajobs:{m.get('PositionID')}", title=m.get("PositionTitle", ""),
                            company=m.get("OrganizationName") or "Federal government", setting="government",
                            location=m.get("PositionLocationDisplay") or chi.get("LocationName", ""),
                            url=m.get("PositionURI"), posted=iso_date(m.get("PublicationStartDate")),
                            description=(details.get("JobSummary") or "") + "\n\n" + (m.get("QualificationSummary") or ""),
                            lat=chi.get("Latitude"), lon=chi.get("Longitude"),
                            salary=(f"${float(pay['MinimumRange']):,.0f}-${float(pay['MaximumRange']):,.0f}"
                                    if pay.get("MinimumRange") else "")))
        time.sleep(0.5)
    return out


FETCHERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "smartrecruiters": smartrecruiters,
            "workday": workday, "oracle": oracle, "icims": icims, "successfactors": successfactors, "atom": atom,
            "amazon": amazon, "adzuna": adzuna, "usajobs": usajobs}
