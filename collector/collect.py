#!/usr/bin/env python3
"""
OpenRoles job collector.

Reads companies.csv, pulls open jobs from each company's OFFICIAL job sources,
cleans them up, and writes site/data/jobs.json + site/data/companies.json.

Sources (the "source" column of companies.csv):
  greenhouse       token = board name          (public job-board API)
  lever            token = company name        (public postings API)
  ashby            token = board name          (public job-board API)
  smartrecruiters  token = company identifier  (public postings API)
  workday          token = career site URL, e.g. https://acme.wd5.myworkdayjobs.com/External
  oracle           token = career site URL, e.g. https://acme.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1
  successfactors   token = career site URL, e.g. https://careers.acme.com
  sitemap          token = website URL; finds job pages through the site's sitemap and reads their JobPosting data
  jsonld           token = a careers page URL; reads JobPosting data on it and the job pages it links to
  auto             token = ANY official careers page URL. The collector works out which of the above it uses.

Rules this script follows:
  * Workday, Oracle, SuccessFactors, sitemap, jsonld and auto only read pages that the site's robots.txt allows.
  * At most one request per second to any single website; it identifies itself as OpenRolesBot.
  * Stores only a short summary of each job and links to the original.

Uses only the Python standard library, so there is nothing to install.

  python collector/collect.py                                        # live run
  python collector/collect.py --fixtures collector/test_fixtures     # offline test with saved responses
  python collector/collect.py --only "Infosys"                       # run a single company (debugging)
"""
import argparse, csv, datetime as dt, email.utils, hashlib, html, json, os, re, sys, threading, time
import urllib.request, urllib.error, urllib.parse, urllib.robotparser
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")
BOT = "OpenRolesBot"
USER_AGENT = f"{BOT}/1.1 (+{SITE_URL or 'https://github.com'}/about.html)"

REQUEST_DELAY = 1.0            # seconds between requests to the same website
PARALLEL_COMPANIES = 8         # companies processed at the same time (each on its own website)
MAX_AGE_DAYS = 120             # hide postings older than this
SUMMARY_CHARS = 360            # short summary only; the full text stays on the company site
MAX_JOBS_PER_COMPANY = 3000
WORKDAY_DETAIL_PAGES = 150     # newest Workday jobs to open for description/salary (the rest show title/location/date)
SITEMAP_MAX_PAGES = 300        # job pages read per company through a sitemap
LINKED_PAGES_MAX = 40          # job pages followed from a careers page (jsonld)

NOW = dt.datetime.now(dt.timezone.utc)
TODAY = NOW.date()
FIXTURES = None                # folder of saved responses for offline tests
FIX_INDEX = {}


# ======================================================================== fetching
_host_locks, _host_last, _meta_lock = {}, {}, threading.Lock()

def _throttle(host):
    with _meta_lock:
        lock = _host_locks.setdefault(host, threading.Lock())
    with lock:
        wait = _host_last.get(host, 0) + REQUEST_DELAY - time.time()
        if wait > 0:
            time.sleep(wait)
        _host_last[host] = time.time()

def _fixture(url, data):
    key = url + (f"#offset={data.get('offset')}" if isinstance(data, dict) and "offset" in data else "")
    best = max((k for k in FIX_INDEX if k in key), key=len, default=None)
    if best is None:
        raise RuntimeError("HTTP 404 (no saved response)")
    with open(os.path.join(FIXTURES, FIX_INDEX[best]), encoding="utf-8") as f:
        return f.read()

def fetch(url, kind="json", data=None):
    """GET (or POST when data is given). kind: 'json' or 'text'."""
    if FIXTURES is not None:
        body = _fixture(url, data)
        return json.loads(body) if kind == "json" else body
    host = urllib.parse.urlparse(url).netloc
    last = None
    for attempt in range(3):
        _throttle(host)
        try:
            headers = {"User-Agent": USER_AGENT, "Accept": "application/json, text/html, application/xml;q=0.9, */*;q=0.8",
                       "Accept-Language": "en-US,en;q=0.8"}
            payload = None
            if data is not None:
                payload = json.dumps(data).encode()
                headers["Content-Type"] = "application/json"
            req = urllib.request.Request(url, data=payload, headers=headers)
            with urllib.request.urlopen(req, timeout=40) as r:
                body = r.read().decode("utf-8", "replace")
            return json.loads(body) if kind == "json" else body
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403, 404, 405, 410):
                raise RuntimeError(f"HTTP {e.code}")
            last = e
        except json.JSONDecodeError:
            raise RuntimeError("the response was not job data")
        except Exception as e:
            last = e
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"could not connect ({last})")

_robots_cache, _robots_lock = {}, threading.Lock()

def allowed(url):
    """True when robots.txt of that website lets our bot read this URL."""
    p = urllib.parse.urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    with _robots_lock:
        rp = _robots_cache.get(base)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        try:
            rp.parse(fetch(base + "/robots.txt", kind="text").splitlines())
        except Exception:
            rp.parse([])          # no robots.txt: nothing is restricted
        with _robots_lock:
            _robots_cache[base] = rp
    return rp.can_fetch(BOT, url)

def need_allowed(url):
    if not allowed(url):
        raise RuntimeError("robots.txt does not allow automated reading of this site, so it was skipped")

def sitemaps_of(base):
    """Sitemap URLs a site declares in robots.txt, plus the usual default."""
    out = []
    try:
        for line in fetch(base + "/robots.txt", kind="text").splitlines():
            if line.lower().startswith("sitemap:"):
                out.append(line.split(":", 1)[1].strip())
    except Exception:
        pass
    return out or [base + "/sitemap.xml"]


# ======================================================================== text helpers
TAG = re.compile(r"<[^>]+>")
WS = re.compile(r"\s+")

def to_text(s):
    if not s:
        return ""
    s = html.unescape(str(s))
    s = re.sub(r"(?i)<\s*(br|/p|/li|/h\d|/div)\s*/?>", "\n", s)
    s = TAG.sub(" ", s)
    s = html.unescape(s)
    parts = [WS.sub(" ", p).strip() for p in s.split("\n")]
    parts = [p if p[-1] in ".!?:;" else p + "." for p in parts if p]
    return " ".join(parts)

def summary(text):
    if len(text) <= SUMMARY_CHARS:
        return text
    cut = text[:SUMMARY_CHARS].rsplit(" ", 1)[0]
    return cut.rstrip(",.;:") + "…"

def iso_date(v):
    """ISO strings, epoch milliseconds or RFC-822 dates -> YYYY-MM-DD, else ''."""
    if v in (None, ""):
        return ""
    try:
        if isinstance(v, (int, float)) or str(v).isdigit():
            return dt.datetime.fromtimestamp(int(v) / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
        s = str(v).strip()
        if re.match(r"\d{4}-\d{2}-\d{2}", s):
            return s[:10]
        return email.utils.parsedate_to_datetime(s).strftime("%Y-%m-%d")
    except Exception:
        return ""

def relative_date(s):
    """Workday style 'Posted Today' / 'Posted 3 Days Ago' / 'Posted 30+ Days Ago'."""
    s = (s or "").lower()
    if "today" in s:
        n = 0
    elif "yesterday" in s:
        n = 1
    else:
        m = re.search(r"(\d+)\+?\s*day", s)
        if not m:
            return ""
        n = int(m.group(1))
    return (TODAY - dt.timedelta(days=n)).strftime("%Y-%m-%d")


# ======================================================================== enrichment
SKILLS = ["Excel", "SQL", "Python", "R", "Power BI", "Tableau", "Looker", "SAP", "Oracle", "NetSuite", "Anaplan",
          "Hyperion", "Salesforce", "Java", "JavaScript", "TypeScript", "React", "Node.js", "Go", "Rust", "C++", "C#",
          ".NET", "Kotlin", "Swift", "AWS", "Azure", "GCP", "Kubernetes", "Docker", "Terraform", "Spark", "Snowflake",
          "dbt", "Airflow", "Kafka", "PostgreSQL", "MySQL", "MongoDB", "Machine Learning", "Figma", "Jira",
          "Financial Modeling", "FP&A", "Financial Planning", "Forecasting", "Budgeting", "Variance Analysis", "Accounting", "IFRS",
          "GAAP", "Ind AS", "Audit", "Tax", "GST", "Risk Management", "Compliance", "KYC", "AML", "Treasury",
          "Valuation", "Reconciliation", "SEO", "Google Analytics"]
SKILL_RE = [(s, re.compile(r"(?<![A-Za-z0-9])" + re.escape(s) + r"(?![A-Za-z0-9+#])", re.I if len(s) > 2 else 0)) for s in SKILLS]

def find_skills(text):
    return [s for s, rx in SKILL_RE if rx.search(text)][:10]

EXP_RE = re.compile(
    r"(\d{1,2})\s*(?:\+|plus)?\s*(?:(?:-|–|to)\s*(\d{1,2}))?\s*\+?\s*(?:years|yrs)\b[^.]{0,40}?experience"
    r"|experience[^.]{0,30}?(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*\d{1,2})?\s*(?:years|yrs)", re.I)
FRESHER_RE = re.compile(r"\b(intern(ship)?|graduate|new grad|fresher|trainee|apprentice|campus|entry[- ]level)\b", re.I)

def experience(title, text):
    if FRESHER_RE.search(title):
        return "Fresher"
    mins = [int(m.group(1) or m.group(3)) for m in EXP_RE.finditer(text) if (m.group(1) or m.group(3)) and int(m.group(1) or m.group(3)) <= 30]
    if not mins:
        return "Fresher" if FRESHER_RE.search(text[:300]) else ""
    n = min(mins)
    return "0–1" if n == 0 else "1–3" if n <= 2 else "3–5" if n <= 4 else "5–10" if n <= 9 else "10+"

def level(title):
    t = title.lower()
    if re.search(r"\b(intern|trainee|apprentice|graduate|new grad)\b", t): return "Entry-level"
    if re.search(r"\b(vp|vice president|director|head of|chief|cfo|cto|partner)\b", t): return "Management"
    if re.search(r"\bmanager\b", t) and not re.search(r"\b(product|program|project) manager\b", t): return "Management"
    if re.search(r"\b(senior|sr\.?|staff|principal|lead|avp)\b", t): return "Senior"
    if re.search(r"\b(junior|jr\.?|associate|entry|analyst i)\b", t): return "Associate"
    return "Mid-level"

EMPLOYMENT_MAP = {
    "fulltime": "Full-time", "full-time": "Full-time", "full time": "Full-time", "permanent": "Full-time", "regular": "Full-time",
    "parttime": "Part-time", "part-time": "Part-time", "part time": "Part-time",
    "contract": "Contract", "contractor": "Contract", "temporary": "Contract", "temp": "Contract", "fixed term": "Contract",
    "intern": "Internship", "internship": "Internship",
}

def employment(raw, title):
    r = (raw or "").strip().lower().replace("_", "")
    for k, v in EMPLOYMENT_MAP.items():
        if k in r:
            return v
    t = title.lower()
    if re.search(r"\bintern(ship)?\b", t): return "Internship"
    if re.search(r"\b(contract|contractor|temporary|fixed[- ]term)\b", t): return "Contract"
    if re.search(r"\bpart[- ]time\b", t): return "Part-time"
    return "Full-time"

COUNTRIES = ["India", "United States", "United Kingdom", "Canada", "Germany", "France", "Netherlands", "Ireland", "Spain",
             "Italy", "Sweden", "Denmark", "Norway", "Finland", "Poland", "Portugal", "Switzerland", "Austria", "Belgium",
             "Czech Republic", "Romania", "Hungary", "Israel", "United Arab Emirates", "Saudi Arabia", "Qatar", "Singapore",
             "Japan", "South Korea", "China", "Hong Kong", "Taiwan", "Australia", "New Zealand", "Brazil", "Mexico",
             "Argentina", "Colombia", "Chile", "South Africa", "Nigeria", "Kenya", "Egypt", "Indonesia", "Philippines",
             "Malaysia", "Vietnam", "Thailand", "Sri Lanka", "Bangladesh", "Estonia", "Luxembourg", "Slovakia", "Costa Rica"]
ALIASES = {"usa": "United States", "u.s.": "United States", "united states of america": "United States",
           "uk": "United Kingdom", "england": "United Kingdom", "scotland": "United Kingdom", "uae": "United Arab Emirates",
           "dubai": "United Arab Emirates", "abu dhabi": "United Arab Emirates", "deutschland": "Germany", "czechia": "Czech Republic",
           "bengaluru": "India", "bangalore": "India", "mumbai": "India", "pune": "India", "hyderabad": "India",
           "chennai": "India", "gurgaon": "India", "gurugram": "India", "noida": "India", "delhi": "India",
           "new delhi": "India", "kolkata": "India", "ahmedabad": "India", "jaipur": "India", "kochi": "India",
           "mysore": "India", "mysuru": "India", "coimbatore": "India", "chandigarh": "India", "indore": "India",
           "bhubaneswar": "India", "thiruvananthapuram": "India", "vadodara": "India", "nagpur": "India",
           "london": "United Kingdom", "manchester": "United Kingdom", "edinburgh": "United Kingdom", "glasgow": "United Kingdom",
           "dublin": "Ireland", "berlin": "Germany", "munich": "Germany", "frankfurt": "Germany", "hamburg": "Germany",
           "paris": "France", "amsterdam": "Netherlands", "stockholm": "Sweden", "toronto": "Canada", "vancouver": "Canada",
           "montreal": "Canada", "sydney": "Australia", "melbourne": "Australia", "tokyo": "Japan", "seoul": "South Korea",
           "shanghai": "China", "beijing": "China", "sao paulo": "Brazil", "são paulo": "Brazil", "mexico city": "Mexico",
           "tel aviv": "Israel", "warsaw": "Poland", "krakow": "Poland", "kraków": "Poland", "budapest": "Hungary",
           "lisbon": "Portugal", "madrid": "Spain", "barcelona": "Spain", "zurich": "Switzerland", "geneva": "Switzerland",
           "manila": "Philippines", "kuala lumpur": "Malaysia", "jakarta": "Indonesia", "bangkok": "Thailand",
           "new york": "United States", "san francisco": "United States", "seattle": "United States", "austin": "United States",
           "chicago": "United States", "boston": "United States", "los angeles": "United States", "denver": "United States",
           "atlanta": "United States", "dallas": "United States", "charlotte": "United States", "jersey city": "United States"}
CODES = {"IN": "India", "US": "United States", "USA": "United States", "GB": "United Kingdom", "UK": "United Kingdom",
         "GBR": "United Kingdom", "IND": "India", "DE": "Germany", "DEU": "Germany", "FR": "France", "NL": "Netherlands",
         "IE": "Ireland", "ES": "Spain", "IT": "Italy", "SE": "Sweden", "PL": "Poland", "PT": "Portugal", "CH": "Switzerland",
         "CA": "Canada", "AU": "Australia", "SG": "Singapore", "SGP": "Singapore", "JP": "Japan", "BR": "Brazil",
         "MX": "Mexico", "AE": "United Arab Emirates", "IL": "Israel", "PH": "Philippines", "MY": "Malaysia",
         "HK": "Hong Kong", "CN": "China", "HU": "Hungary", "CZ": "Czech Republic", "RO": "Romania", "ZA": "South Africa",
         "LU": "Luxembourg", "BE": "Belgium", "AT": "Austria", "DK": "Denmark", "NO": "Norway", "FI": "Finland",
         "AR": "Argentina", "CO": "Colombia", "CR": "Costa Rica", "LK": "Sri Lanka", "KR": "South Korea"}
US_STATES = set("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split())

def country_of(location):
    loc = (location or "").strip()
    if loc.upper() in CODES:
        return CODES[loc.upper()]
    low = loc.lower()
    for c in COUNTRIES:
        if c.lower() in low:
            return c
    for k, v in ALIASES.items():
        if re.search(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])", low):
            return v
    m = re.search(r"[,\s]([A-Z]{2,3})\s*(?:,\s*\d+)?\s*\)?$", loc)
    if m and m.group(1) in CODES:
        return CODES[m.group(1)]
    m = re.search(r",\s*([A-Z]{2})\b", loc)
    if m and m.group(1) in US_STATES:
        return "United States"
    return ""

def work_mode(location, text, remote_flag=None, workplace=None):
    w = (workplace or "").lower().replace("-", "").replace(" ", "")
    if "hybrid" in w: return "Hybrid"
    if "remote" in w or remote_flag is True: return "Remote"
    if "onsite" in w or "inoffice" in w: return "On-site"
    low = (location or "").lower()
    if "remote" in low: return "Remote"
    if "hybrid" in low or re.search(r"\bhybrid\b", text[:600], re.I): return "Hybrid"
    return "On-site"

def make_job(company, *, title, location, url, posted, text="", emp_raw="", department="", salary="",
             remote_flag=None, workplace=None, country_hint=""):
    title = WS.sub(" ", (title or "").strip())
    location = WS.sub(" ", (location or "").strip()) or "Not specified"
    full = f"{title}. {text}"
    jid = hashlib.sha1(f"{company['id']}|{url}".encode()).hexdigest()[:12]
    return {
        "id": jid, "company_id": company["id"], "company": company["name"], "industry": company["industry"],
        "title": title, "location": location, "country": country_hint or country_of(location),
        "mode": work_mode(location, text, remote_flag, workplace),
        "employment": employment(emp_raw, title), "department": (department or "").strip(),
        "level": level(title), "experience": experience(title, text),
        "skills": find_skills(full), "salary": (salary or "").strip(),
        "posted": posted, "summary": summary(text), "url": url, "source": company.get("detected_source") or company["source"],
    }


# ======================================================================== public job-board APIs
def from_greenhouse(c, token):
    data = fetch(f"https://boards-api.greenhouse.io/v1/boards/{urllib.parse.quote(token)}/jobs?content=true")
    out = []
    for j in data.get("jobs", []):
        meta = {str(m.get("name", "")).lower(): m.get("value") for m in (j.get("metadata") or []) if isinstance(m, dict)}
        emp = next((str(v) for k, v in meta.items() if "employment" in k or "job type" in k), "")
        out.append(make_job(c, title=j.get("title"), location=(j.get("location") or {}).get("name"),
                            url=j.get("absolute_url"), posted=iso_date(j.get("first_published") or j.get("updated_at")),
                            text=to_text(j.get("content")), emp_raw=emp,
                            department=", ".join(d.get("name", "") for d in (j.get("departments") or [])[:2])))
    return out

def from_lever(c, token):
    data = fetch(f"https://api.lever.co/v0/postings/{urllib.parse.quote(token)}?mode=json")
    out = []
    for j in data if isinstance(data, list) else []:
        cat = j.get("categories") or {}
        sal, r = "", j.get("salaryRange") or {}
        if r.get("min") and r.get("max"):
            per = {"per-year-salary": "/year", "per-month-salary": "/month", "per-hour-wage": "/hour",
                   "per-day-wage": "/day", "per-week-salary": "/week"}.get(r.get("interval", ""), "")
            sal = f"{r.get('currency', '')} {int(r['min']):,}–{int(r['max']):,} {per}".strip()
        text = j.get("descriptionPlain") or to_text(j.get("description"))
        for lst in j.get("lists") or []:
            text += " " + to_text(lst.get("text")) + " " + to_text(lst.get("content"))
        out.append(make_job(c, title=j.get("text"), location=cat.get("location") or ", ".join(cat.get("allLocations") or []),
                            url=j.get("hostedUrl") or j.get("applyUrl"), posted=iso_date(j.get("createdAt")),
                            text=WS.sub(" ", text).strip(), emp_raw=cat.get("commitment", ""),
                            department=cat.get("team") or cat.get("department") or "", salary=sal,
                            workplace=j.get("workplaceType"), country_hint=CODES.get((j.get("country") or "").upper(), "")))
    return out

def from_ashby(c, token):
    data = fetch(f"https://api.ashbyhq.com/posting-api/job-board/{urllib.parse.quote(token)}?includeCompensation=true")
    out = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        out.append(make_job(c, title=j.get("title"), location=j.get("location"),
                            url=j.get("jobUrl") or j.get("applyUrl"), posted=iso_date(j.get("publishedAt")),
                            text=j.get("descriptionPlain") or to_text(j.get("descriptionHtml")),
                            emp_raw=j.get("employmentType", ""), department=j.get("team") or j.get("department") or "",
                            salary=(j.get("compensation") or {}).get("compensationTierSummary") or "",
                            remote_flag=j.get("isRemote"), workplace=j.get("workplaceType")))
    return out

def from_smartrecruiters(c, token):
    out, offset = [], 0
    while True:
        data = fetch(f"https://api.smartrecruiters.com/v1/companies/{urllib.parse.quote(token)}/postings?limit=100&offset={offset}")
        items = data.get("content", [])
        for j in items:
            loc = j.get("location") or {}
            cc = (loc.get("country") or "").upper()
            place = ", ".join(x for x in [loc.get("city"), loc.get("region"), CODES.get(cc, cc)] if x)
            text = " ".join(filter(None, [(j.get("function") or {}).get("label"), (j.get("experienceLevel") or {}).get("label")]))
            out.append(make_job(c, title=j.get("name"), location=place,
                                url=f"https://jobs.smartrecruiters.com/{token}/{j.get('id')}",
                                posted=iso_date(j.get("releasedDate")), text=text,
                                emp_raw=(j.get("typeOfEmployment") or {}).get("label", ""),
                                department=(j.get("department") or {}).get("label", ""),
                                remote_flag=loc.get("remote"), workplace="hybrid" if loc.get("hybrid") else None,
                                country_hint=CODES.get(cc, "")))
        offset += len(items)
        if not items or offset >= data.get("totalFound", 0) or offset >= MAX_JOBS_PER_COMPANY:
            return out


# ======================================================================== enterprise career sites
WD_RE = re.compile(r"https?://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)")

def from_workday(c, token):
    m = WD_RE.match(token)
    if not m:
        raise RuntimeError("not a Workday career site address (expected https://name.wdN.myworkdayjobs.com/SiteName)")
    tenant, wd, site = m.groups()
    host = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{host}/wday/cxs/{tenant}/{site}"
    need_allowed(f"{host}/{site}")
    postings, offset, total = [], 0, None
    while True:
        data = fetch(f"{api}/jobs", data={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": ""})
        page = data.get("jobPostings") or []
        if total is None:
            total = data.get("total") or 0
        postings += page
        offset += 20
        if not page or offset >= min(total, MAX_JOBS_PER_COMPANY):
            break
    out = []
    for i, p in enumerate(postings):
        path = p.get("externalPath") or ""
        if not path:
            continue
        info = {}
        if i < WORKDAY_DETAIL_PAGES:
            try:
                info = fetch(api + path).get("jobPostingInfo") or {}
            except Exception:
                info = {}
        locs = [info.get("location")] + list(info.get("additionalLocations") or []) if info.get("location") else [p.get("locationsText")]
        location = "; ".join(x for x in locs if x)
        out.append(make_job(c, title=info.get("title") or p.get("title"), location=location,
                            url=info.get("externalUrl") or f"{host}/{site}{path}",
                            posted=iso_date(info.get("startDate")) or relative_date(info.get("postedOn") or p.get("postedOn")),
                            text=to_text(info.get("jobDescription")), emp_raw=info.get("timeType", ""),
                            workplace=info.get("remoteType"),
                            country_hint=((info.get("country") or {}).get("descriptor", "") if len(locs) == 1 else "")))
    return out

ORACLE_RE = re.compile(r"https?://([\w.-]+\.oraclecloud\.com)/hcmUI/CandidateExperience/([a-z]{2})/sites/([\w-]+)")

def from_oracle(c, token):
    m = ORACLE_RE.match(token)
    if not m:
        raise RuntimeError("not an Oracle career site address (expected https://name.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1)")
    host, lang, site = m.groups()
    need_allowed(token)
    out, offset, total = [], 0, None
    while True:
        finder = f"findReqs;siteNumber={site},facetsList=NONE,limit=25,offset={offset},sortBy=POSTING_DATES_DESC"
        url = (f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true"
               f"&expand=requisitionList.secondaryLocations&finder={urllib.parse.quote(finder, safe=';,=_')}")
        items = (fetch(url).get("items") or [{}])
        block = items[0] if items else {}
        if total is None:
            total = block.get("TotalJobsCount") or 0
        reqs = block.get("requisitionList") or []
        for r in reqs:
            locs = [r.get("PrimaryLocation")] + [s.get("Name") for s in (r.get("secondaryLocations") or []) if isinstance(s, dict)]
            out.append(make_job(c, title=r.get("Title"), location="; ".join(x for x in locs if x),
                                url=f"https://{host}/hcmUI/CandidateExperience/{lang}/sites/{site}/job/{r.get('Id')}",
                                posted=iso_date(r.get("PostedDate")), text=to_text(r.get("ShortDescriptionStr")),
                                emp_raw=r.get("JobSchedule") or r.get("ContractType") or "", workplace=r.get("WorkplaceType"),
                                department=r.get("Organization") or r.get("JobFamily") or "",
                                country_hint=CODES.get((r.get("PrimaryLocationCountry") or "").upper(), "") if len(locs) == 1 or not locs[1:] else ""))
        offset += 25
        if not reqs or offset >= min(total, MAX_JOBS_PER_COMPANY):
            return out

def from_successfactors(c, token):
    p = urllib.parse.urlparse(token)
    base = f"{p.scheme}://{p.netloc}"
    need_allowed(base + "/")
    # 1) the career site's own RSS job feed
    for path in ("/services/rss/job/?locale=en_US&keywords=()", "/services/rss/job/?locale=en_US"):
        url = base + path
        if not allowed(url):
            continue
        try:
            root = ET.fromstring(fetch(url, kind="text").encode("utf-8"))
        except Exception:
            continue
        items = root.findall(".//item")
        if not items:
            continue
        out = []
        for it in items[:MAX_JOBS_PER_COMPANY]:
            title = (it.findtext("title") or "").strip()
            loc = ""
            m = re.match(r"(.*)\(([^()]*)\)\s*$", title)
            if m:
                title, loc = m.group(1).strip(), m.group(2).strip()
            out.append(make_job(c, title=title, location=loc, url=(it.findtext("link") or "").strip(),
                                posted=iso_date(it.findtext("pubDate")), text=to_text(it.findtext("description"))))
        return out
    # 2) the site's sitemap and the JobPosting data on each job page
    return from_sitemap(c, base)


# ======================================================================== sitemaps & schema.org JobPosting
LD_RE = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)
HREF_RE = re.compile(r'href=["\']([^"\'#]+)["\']', re.I)
JOB_PATH_RE = re.compile(r"/(job|jobs|careers?|positions?|openings?|vacanc(y|ies)|requisitions?|jobdetail|job-detail)s?[/_-]", re.I)
LOC_RE = re.compile(r"<url>.*?<loc>\s*(.*?)\s*</loc>(?:.*?<lastmod>\s*(.*?)\s*</lastmod>)?.*?</url>", re.S | re.I)
SMLOC_RE = re.compile(r"<sitemap>.*?<loc>\s*(.*?)\s*</loc>", re.S | re.I)

def _jobpostings(obj):
    if isinstance(obj, list):
        for x in obj:
            yield from _jobpostings(x)
    elif isinstance(obj, dict):
        t = obj.get("@type")
        if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
            yield obj
        for k in ("@graph", "itemListElement", "item", "mainEntity"):
            if k in obj:
                yield from _jobpostings(obj[k])

def _ld_location(jp):
    locs = jp.get("jobLocation") or []
    locs = locs if isinstance(locs, list) else [locs]
    parts = []
    for l in locs:
        a = (l or {}).get("address") or {} if isinstance(l, dict) else {}
        if isinstance(a, str):
            parts.append(a)
            continue
        ctry = a.get("addressCountry")
        ctry = ctry.get("name") if isinstance(ctry, dict) else ctry
        parts.append(", ".join(x for x in [a.get("addressLocality"), a.get("addressRegion"), ctry] if x))
    return "; ".join(p for p in parts if p)

def _ld_salary(jp):
    b = jp.get("baseSalary") or {}
    if not isinstance(b, dict):
        return ""
    v = b.get("value") or {}
    if isinstance(v, dict) and (v.get("minValue") or v.get("value")):
        lo, hi = v.get("minValue") or v.get("value"), v.get("maxValue")
        unit = (v.get("unitText") or "").lower()
        try:
            rng = f"{float(lo):,.0f}" + (f"–{float(hi):,.0f}" if hi else "")
        except Exception:
            rng = f"{lo}" + (f"–{hi}" if hi else "")
        return f"{b.get('currency', '')} {rng}{' /' + unit if unit else ''}".strip()
    return ""

def _postings_in(page):
    found = []
    for block in LD_RE.findall(page):
        try:
            found += list(_jobpostings(json.loads(block.strip())))
        except Exception:
            continue
    return found

def _ld_job(c, jp, page_url):
    if jp.get("validThrough") and iso_date(jp["validThrough"]) and iso_date(jp["validThrough"]) < TODAY.strftime("%Y-%m-%d"):
        return None     # closed
    emp = jp.get("employmentType") or ""
    emp = emp[0] if isinstance(emp, list) and emp else emp
    return make_job(c, title=jp.get("title"), location=_ld_location(jp), url=jp.get("url") or page_url,
                    posted=iso_date(jp.get("datePosted")), text=to_text(jp.get("description")), emp_raw=str(emp),
                    salary=_ld_salary(jp), workplace="remote" if jp.get("jobLocationType") == "TELECOMMUTE" else None)

def _read_job_pages(c, urls, limit):
    out, seen, misses = [], set(), 0
    for u in urls[:limit]:
        if not allowed(u):
            continue
        try:
            jps = _postings_in(fetch(u, kind="text"))
        except Exception:
            jps = []
        if not jps:
            misses += 1
            if misses >= 10 and not out:
                break       # these pages don't carry job data: stop instead of reading hundreds of pages
            continue
        for jp in jps:
            j = _ld_job(c, jp, u)
            if j and j["url"] not in seen:
                seen.add(j["url"])
                out.append(j)
    return out

def from_sitemap(c, token):
    p = urllib.parse.urlparse(token)
    base = f"{p.scheme}://{p.netloc}"
    queue, done, entries = sitemaps_of(base), set(), []
    while queue and len(done) < 25:
        sm = queue.pop(0)
        if sm in done or not allowed(sm):
            continue
        done.add(sm)
        try:
            xml = fetch(sm, kind="text")
        except Exception:
            continue
        for sub in SMLOC_RE.findall(xml):
            if any(w in sub.lower() for w in ("job", "career", "position", "vacanc", "sitemap")) and sub not in done:
                queue.append(html.unescape(sub))
        for loc, lastmod in LOC_RE.findall(xml):
            loc = html.unescape(loc)
            if JOB_PATH_RE.search(urllib.parse.urlparse(loc).path + "/"):
                entries.append((lastmod or "", loc))
    if not entries:
        raise RuntimeError("no job pages found in this site's sitemap")
    entries.sort(reverse=True)               # newest first
    jobs = _read_job_pages(c, [u for _, u in entries], SITEMAP_MAX_PAGES)
    if not jobs:
        raise RuntimeError("job pages on this site don't include structured job data")
    return jobs

def from_jsonld(c, token, page=None):
    need_allowed(token)
    page = page if page is not None else fetch(token, kind="text")
    jobs = [j for j in (_ld_job(c, jp, token) for jp in _postings_in(page)) if j]
    if jobs:
        return jobs
    host = urllib.parse.urlparse(token).netloc
    links = []
    for h in HREF_RE.findall(page):
        u = urllib.parse.urljoin(token, html.unescape(h))
        if urllib.parse.urlparse(u).netloc == host and JOB_PATH_RE.search(urllib.parse.urlparse(u).path + "/") and u not in links and u != token:
            links.append(u)
    jobs = _read_job_pages(c, links, LINKED_PAGES_MAX)
    if not jobs:
        raise RuntimeError("no structured job data on this page")
    return jobs


# ======================================================================== auto-detection
DETECT = [
    ("greenhouse", re.compile(r"boards(?:-api)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=|v1/boards/)([\w-]+)")),
    ("greenhouse", re.compile(r"(?:job-)?boards(?:\.eu)?\.greenhouse\.io/(?!embed)([\w-]+)")),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([\w.-]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([\w.%-]+)")),
    ("smartrecruiters", re.compile(r"(?:careers|jobs)\.smartrecruiters\.com/([\w-]+)")),
]
SF_MARKERS = re.compile(r"jobs2web|/platform/js/j2w|rmkcdn|successfactors\.com/career|csb-|data-careersite", re.I)
NOT_TOKENS = {"embed", "v1", "jobs", "job", "api", "search", "oneclick-ui", "en-us"}

def detect(url_or_html, page_url=None):
    """Return (source, token) found in a URL or in a page's HTML, else None."""
    s = url_or_html.replace("\\/", "/")
    m = WD_RE.search(s)
    if m and m.group(3).lower() not in ("wday", "en-us"):
        return "workday", f"https://{m.group(1)}.{m.group(2)}.myworkdayjobs.com/{m.group(3)}"
    m = ORACLE_RE.search(s)
    if m:
        return "oracle", m.group(0)
    for src, rx in DETECT:
        m = rx.search(s)
        if m and m.group(1).lower() not in NOT_TOKENS:
            return src, m.group(1)
    if page_url and SF_MARKERS.search(s):
        p = urllib.parse.urlparse(page_url)
        return "successfactors", f"{p.scheme}://{p.netloc}"
    return None

def from_auto(c, token):
    found = detect(token)
    tried = []
    if not found:
        need_allowed(token)
        page = fetch(token, kind="text")
        found = detect(page, token)
        if not found:   # look one level deeper: the careers page's own "search jobs" links
            host = urllib.parse.urlparse(token).netloc.split(":")[0]
            root = ".".join(host.split(".")[-2:])
            cands = []
            for h in HREF_RE.findall(page):
                u = urllib.parse.urljoin(token, html.unescape(h))
                if found := detect(u):
                    break
                uh = urllib.parse.urlparse(u).netloc
                if uh.endswith(root) and re.search(r"job|search|opening|vacanc|career|opportunit", u, re.I) and u not in cands and u.rstrip("/") != token.rstrip("/"):
                    cands.append(u)
            for u in cands[:6]:
                if found or not allowed(u):
                    break
                try:
                    found = detect(fetch(u, kind="text"), u)
                except Exception:
                    continue
        if not found:
            for fn, name in ((lambda: from_jsonld(c, token, page), "careers page"), (lambda: from_sitemap(c, token), "sitemap")):
                try:
                    jobs = fn()
                    c["detected_source"] = "jsonld" if name == "careers page" else "sitemap"
                    c["detected"] = f"{c['detected_source']}: {token}"
                    return jobs
                except Exception as e:
                    tried.append(f"{name}: {e}")
            raise RuntimeError("couldn't find a readable job source on this careers page. Open one of their job ads "
                               "and paste that page's address as the token instead")
    src, tok = found
    c["detected_source"], c["detected"] = src, f"{src}: {tok}"
    return SOURCES[src](c, tok)

SOURCES = {"greenhouse": from_greenhouse, "lever": from_lever, "ashby": from_ashby, "smartrecruiters": from_smartrecruiters,
           "workday": from_workday, "oracle": from_oracle, "successfactors": from_successfactors,
           "sitemap": from_sitemap, "jsonld": from_jsonld, "auto": from_auto}


# ======================================================================== main
def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")

def norm_key(j):
    return (j["company_id"], re.sub(r"[^a-z0-9]", "", j["title"].lower()), re.sub(r"[^a-z0-9]", "", j["location"].lower()))

def run_company(c, previous, cutoff):
    started = time.time()
    status, jobs = "ok", []
    src = c["source"] or ("auto" if c["token"].startswith("http") else "")
    try:
        if src not in SOURCES:
            raise RuntimeError(f"unknown source '{c['source']}'")
        jobs = SOURCES[src](c, c["token"])
        if not jobs:
            status = "ok (no open roles found)"
    except Exception as e:
        status = f"failed: {e}"
        jobs = previous.get(c["id"], [])
        if jobs:
            status += " (showing yesterday's jobs)"
    jobs = [j for j in jobs if j.get("title") and str(j.get("url", "")).startswith("http")
            and (not j.get("posted") or j["posted"] >= cutoff)][:MAX_JOBS_PER_COMPANY]
    return c, jobs, status, time.time() - started

def main():
    global FIXTURES, FIX_INDEX
    ap = argparse.ArgumentParser()
    ap.add_argument("--companies", default=os.path.join(ROOT, "companies.csv"))
    ap.add_argument("--out", default=os.path.join(ROOT, "site", "data"))
    ap.add_argument("--fixtures", help="read saved responses from this folder instead of the internet")
    ap.add_argument("--previous", help="last published jobs.json; reused for a company whose source fails today")
    ap.add_argument("--only", help="run only companies whose name contains this text")
    args = ap.parse_args()
    if args.fixtures:
        FIXTURES = args.fixtures
        with open(os.path.join(FIXTURES, "index.json"), encoding="utf-8") as f:
            FIX_INDEX = json.load(f)

    with open(args.companies, newline="", encoding="utf-8") as f:
        companies = [{k: (v or "").strip() for k, v in row.items() if k} for row in csv.DictReader(f) if (row.get("name") or "").strip()]
    seen_ids = set()
    for c in companies:
        c["id"] = slug(c["name"])
        c["source"] = c.get("source", "").lower()
        c.setdefault("industry", ""); c.setdefault("country", "")
        if c["id"] in seen_ids:
            print(f"Note: '{c['name']}' appears twice in companies.csv; using the first line only.")
        seen_ids.add(c["id"])
    companies = list({c["id"]: c for c in reversed(companies)}.values())[::-1]
    if args.only:
        companies = [c for c in companies if args.only.lower() in c["name"].lower()]

    previous = {}
    if args.previous and os.path.exists(args.previous):
        try:
            with open(args.previous, encoding="utf-8") as f:
                for j in json.load(f).get("jobs", []):
                    previous.setdefault(j["company_id"], []).append(j)
        except Exception:
            pass

    cutoff = (TODAY - dt.timedelta(days=MAX_AGE_DAYS)).strftime("%Y-%m-%d")
    workers = 1 if FIXTURES else PARALLEL_COMPANIES
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(lambda c: run_company(c, previous, cutoff), companies))

    all_jobs, directory = [], []
    print(f"{'Company':<30} {'Source':<16} {'Jobs':>5}  Result")
    for c, jobs, status, secs in results:
        shown_src = c.get("detected_source") or c["source"] or "auto"
        print(f"{c['name'][:30]:<30} {shown_src:<16} {len(jobs):>5}  {status}" + (f"   [{c['detected']}]" if c.get("detected") and c["source"] in ("auto", "") else ""))
        all_jobs += jobs
        directory.append({"id": c["id"], "name": c["name"], "industry": c["industry"], "country": c.get("country", ""),
                          "source": shown_src, "open_roles": len(jobs),
                          "status": "ok" if status.startswith("ok") else "error"})

    seen, unique = set(), []
    for j in all_jobs:
        k = norm_key(j)
        if k not in seen:
            seen.add(k)
            unique.append(j)
    unique.sort(key=lambda j: j.get("posted") or "", reverse=True)

    os.makedirs(args.out, exist_ok=True)
    stamp = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(os.path.join(args.out, "jobs.json"), "w", encoding="utf-8") as f:
        json.dump({"generated": stamp, "count": len(unique), "jobs": unique}, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(args.out, "companies.json"), "w", encoding="utf-8") as f:
        json.dump({"generated": stamp, "companies": sorted(directory, key=lambda d: d["name"].lower())}, f, ensure_ascii=False, indent=1)
    ok = sum(1 for d in directory if d["status"] == "ok")
    print(f"\nDone: {len(unique)} jobs from {ok}/{len(directory)} companies -> {args.out}")
    if ok == 0 and directory and not previous:
        sys.exit("No source worked at all; not publishing an empty site.")

if __name__ == "__main__":
    main()
