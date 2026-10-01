from __future__ import annotations

import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable

STATUS_RE = re.compile(r"(?:https?://)?(?:www\.)?(?:x|twitter)\.com/(?P<user>[\w.]+)/status(?:es)?/(?P<id>\d+)", re.IGNORECASE)
TRACKING_PARAMS = ("twclid", "s", "ref_src", "ref_url", "cxt")
MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10,
    "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}
CITIES = [
    "amravati", "amaravati", "bangalore", "bengaluru", "hyderabad", "delhi",
    "new delhi", "noida", "gurgaon", "gurugram", "mumbai", "pune", "chennai",
    "kolkata", "indore", "coimbatore", "jaipur", "lucknow", "ahmedabad",
    "trichy", "thiruvananthapuram", "kochi", "mysuru", "mysore", "chandigarh",
    "goa", "bhopal", "patna", "kanpur", "nagpur", "visakhapatnam", "vijayawada",
    "telangana", "andhra pradesh", "karnataka", "maharashtra", "tamil nadu",
    "delhi ncr", "ncr", "sf", "san francisco", "new york", "nyc", "seattle",
    "austin", "boston", "london", "berlin", "amsterdam", "dubai", "singapore",
    "toronto", "vancouver", "waterloo",
]
ROLE_WORDS = [
    "machine learning engineer", "ml engineer", "ai engineer", "ai ml engineer",
    "software engineer", "software developer", "full stack engineer",
    "fullstack engineer", "full stack developer", "backend engineer",
    "frontend developer", "front end developer", "devops engineer",
    "data engineer", "data scientist", "research assistant",
    "research engineer", "research scientist", "ml intern", "ai intern",
    "software intern", "engineering intern", "product intern", "associate engineer",
    "graduate engineer", "trainee engineer", "qa engineer", "test engineer",
    "embedded engineer", "platform engineer", "site reliability engineer",
    "nlp engineer", "computer vision engineer", "llm engineer", "ai agent engineer",
    "technical writer", "technical content writer", "prompt engineer",
    "product manager", "project manager", "business analyst", "data analyst",
    "intern", "trainee", "apprentice", "fresher", "developer", "engineer",
]
COMP_PATTERNS = [
    re.compile(r"(?:₹|rs\.?|inr)\s?(?P<lo>\d+(?:\.\d+)?)\s*(?P<unit>cr|crore|lakhs?|lpa|l)\s*(?:-|–|to)\s*(?:₹|rs\.?|inr)?\s?(?P<hi>\d+(?:\.\d+)?)\s*(?:cr|crore|lakhs?|lpa|l)\b", re.IGNORECASE),
    re.compile(r"(?:₹|rs\.?|inr)\s?(?P<lo>\d+(?:\.\d+)?)\s*(?P<unit>cr|crore|lakhs?|lpa|l)\b", re.IGNORECASE),
    re.compile(r"(?P<lo>\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(?P<hi>\d+(?:\.\d+)?)\s*(?P<unit>lpa|cr|crore|lakh|lakhs)\b", re.IGNORECASE),
    re.compile(r"(?P<lo>\d+(?:\.\d+)?)\s*(?P<unit>lpa|cr|crore|lakh|lakhs)\b", re.IGNORECASE),
    re.compile(r"(?P<cur>[$€£])\s?(?P<lo>\d[\d,]*(?:\.\d+)?)\s*(?P<k>k)?\s*(?:-|–|to)\s*(?P=cur)?\s?(?P<hi>\d[\d,]*(?:\.\d+)?)\s*(?P<k2>k)?", re.IGNORECASE),
    re.compile(r"(?P<cur>[$€£])\s?(?P<lo>\d[\d,]*(?:\.\d+)?)\s*(?P<k>k)?\b", re.IGNORECASE),
    re.compile(r"(?:₹|rs\.?|inr)\s?(?P<lo>\d[\d,]*)\s*(?:-|–|to)?\s?(?P<hi>\d[\d,]*)?\s*(?:/\s*(?:month|mo)|per\s+month|a\s+month)?", re.IGNORECASE),
]
COMPANIES_HOST = re.compile(r"utm_(?:source|campaign)=|twclid=")
INTERNAL_LINK = re.compile(r"^(?:https?://)?(?:www\.)?(?:x|twitter)\.com/(?:[\w.]+/(?:status(?:es)?/\d+|search|hashtag|explore|i|notifications|messages|settings|home|[A-Za-z_]+)|intent|tweet)", re.IGNORECASE)


def status_key(url: str | None) -> str | None:
    if not url:
        return None
    match = STATUS_RE.search(url)
    if not match:
        return None
    return f"{match.group('user').lower()}/{match.group('id')}"


def canonical_status_url(url: str | None) -> str | None:
    if not url:
        return None
    match = STATUS_RE.search(url)
    if not match:
        return None
    return f"https://x.com/{match.group('user')}/status/{match.group('id')}"


def clean_text(raw: str) -> str:
    text = raw.replace("\u2028", " ").replace("\u00a0", " ")
    text = re.sub(r"(https?://)\s*\n\s*", r"\1", text)
    text = re.sub(r"(https?://[^\s]+)\s+(?=[\w/?#=&.\-])", r"\1", text)
    text = re.sub(r"(https?://[^\n]*[\-/])\s*\n\s*(-?[\w.\-])", r"\1\2", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return text.strip()


def split_handle(display: str) -> tuple[str, str]:
    text = display.replace("\u00b7", " ").strip()
    handle = ""
    handle_match = re.search(r"@([A-Za-z0-9_]+)", text)
    if handle_match:
        handle = handle_match.group(1)
    name = re.sub(r"@([A-Za-z0-9_]+)", " ", text)
    name = re.sub(r"\s+", " ", name).strip()
    timestamp = ""
    stamp = re.search(r"\b(\d+[smhd]|\w{3} \d{1,2})\s*$", text)
    if stamp:
        timestamp = stamp.group(1)
        name = text[: stamp.start()].strip()
        name = re.sub(r"@([A-Za-z0-9_]+)", " ", name)
        name = re.sub(r"\s+", " ", name).strip()
    return name, handle or timestamp


def dedupe_links(links: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for href, label in links:
        if not href:
            continue
        absolute = href if href.startswith("http") else f"https://x.com{href}"
        stripped = re.sub(r"[?&](?:%s)=[^&\s]*" % "|".join(TRACKING_PARAMS), "", absolute)
        stripped = stripped.rstrip("?&")
        if stripped in seen:
            continue
        seen.add(stripped)
        label_text = re.sub(r"\s+", " ", label or "").strip()
        if label_text in {"", "link", "link preview"} or label_text in {href, absolute, stripped}:
            label_text = ""
        out.append((stripped, label_text))
    return out


def is_internal_link(url: str) -> bool:
    return bool(INTERNAL_LINK.match(url))


def looks_like_apply_url(url: str) -> bool:
    lowered = url.lower()
    if is_internal_link(url):
        return False
    if any(token in lowered for token in (
        "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "smartrecruiters",
        "jobvite", "taleo", "icims", "bamboohr", "jobfound.org", "linkedin.com/jobs",
        "wellfound.com", "naukri.com", "internshala.com", "cutshort.io", "otta.com",
        "apply.workable.com", "jobs.ashbyhq.com", "myworkdayjobs.com", "indeed.com",
        "telegram.me", "t.me", "forms.gle", "bit.ly", "forms.",
    )):
        return True
    return bool(re.search(r"(?:apply|career|job|opening|position|internship|hiring|vacanc)", lowered))


_RESOLVED: dict[str, str | None] = {}


def resolve_short_url(url: str, timeout: float = 6.0) -> str | None:
    if "t.co/" not in url:
        return url
    if url in _RESOLVED:
        return _RESOLVED[url]
    request = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/153 Safari/537.36"},
    )
    resolved: str | None = None
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            final = response.geturl()
            if "t.co/" not in final:
                resolved = final
    except urllib.error.HTTPError:
        try:
            request.full_url = url
            request.get_method = lambda: "GET"
            with urllib.request.urlopen(request, timeout=timeout) as response:
                final = response.geturl()
                if "t.co/" not in final:
                    resolved = final
        except Exception:
            resolved = None
    except Exception:
        resolved = None
    _RESOLVED[url] = resolved
    return resolved


def parse_compensation(text: str) -> list[str]:
    candidates: list[tuple[int, int, str]] = []
    for pattern in COMP_PATTERNS:
        for match in pattern.finditer(text):
            groups = match.groupdict()
            unit = (groups.get("unit") or "").lower()
            lo = groups.get("lo") or ""
            hi = groups.get("hi") or ""
            cur = groups.get("cur") or ""
            if unit in {"lpa", "cr", "crore", "lakh", "lakhs", "l"}:
                value = f"{lo}-{hi} {unit.upper()}" if hi else f"{lo} {unit.upper()}"
            elif cur:
                k1 = groups.get("k") or ""
                k2 = groups.get("k2") or ""
                value = f"{cur}{lo}{k1}-{cur}{hi}{k2}" if hi else f"{cur}{lo}{k1}"
            elif lo:
                per_month = re.search(r"(?:/|per\s+)month", match.group(0), re.IGNORECASE)
                value = f"~{lo}/month" if per_month else f"~{lo}"
            else:
                continue
            candidates.append((match.start(), match.end(), value))
    candidates.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    out: list[str] = []
    consumed_until = -1
    for start, end, value in candidates:
        if start < consumed_until:
            continue
        consumed_until = end
        key = value.replace(" ", "").lower()
        if key in {item.replace(" ", "").lower() for item in out}:
            continue
        out.append(value)
    return out[:4]


def parse_locations(text: str) -> list[str]:
    lowered = text.lower()
    hits = [city for city in CITIES if re.search(rf"\b{re.escape(city)}\b", lowered)]
    if re.search(r"\bremote\b|\bwfh\b|work from home", lowered):
        hits.append("remote")
    out: list[str] = []
    for hit in hits:
        pretty = hit if hit in {"remote"} else hit.title()
        if pretty not in out:
            out.append(pretty)
    return out[:6]


def parse_deadline(text: str, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    dated = re.search(
        r"(?:deadline|apply\s+by|closes?\s+on|last\s+date|submission\s+deadline)\s*(?:is|:)?\s*([^\n.;]{3,60})",
        text,
        re.IGNORECASE,
    )
    before = re.search(r"\b(?:before|by)\s+([A-Z][a-z]+\s+\d{1,2}(?:,?\s*\d{4})?)", text)
    window = dated or before
    raw = window.group(1).strip() if window else None
    iso: str | None = None
    expired: bool | None = None
    if raw:
        date_match = re.search(r"([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})?", raw)
        if date_match:
            month = MONTHS.get(date_match.group(1).lower())
            day = int(date_match.group(2))
            year = int(date_match.group(3)) if date_match.group(3) else today.year
            if month:
                try:
                    candidate = date(year, month, day)
                    if dated and not date_match.group(3) and candidate < today:
                        candidate = date(year + 1, month, day)
                    iso = candidate.isoformat()
                    expired = candidate < today
                except ValueError:
                    iso = None
    return {"raw": raw, "date": iso, "expired": expired}


def parse_eligibility(text: str) -> dict[str, Any]:
    window = re.search(
        r"((?:graduation|graduating|passing)\s*(?:year|batch)?\s*[:\-]?\s*[^.\n]{0,60}|batch\s*[:\-]?\s*\d{4}[^.\n]{0,40}|final\s*year[^.\n]{0,40})",
        text,
        re.IGNORECASE,
    )
    raw = re.sub(r"\s+", " ", window.group(1)).strip() if window else None
    years = sorted({int(y) for y in re.findall(r"\b(19\d{2}|20\d{2})\b", raw or "")})
    return {"raw": raw, "years": years, "tagged": raw is not None}


def extract_company(segment: str, handle: str) -> str | None:
    patterns = [
        r"^\s*([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3})\s+is\s+(?:hiring|looking)\b",
        r"\b([A-Z][\w&.\-]{1,}(?:\s+[A-Z][\w&.\-]{1,})?)\s+is\s+(?:hiring|looking)\b",
        r"\bat\s+@([A-Za-z0-9_]+)",
        r"\b([A-Z][\w&.\-]{2,})\s+(?:is\s+)?hiring\b",
        r"\bcompany\s*[:\-]\s*([A-Z][\w&.\- ]{1,30})",
    ]
    for pattern in patterns:
        match = re.search(pattern, segment)
        if match:
            candidate = re.sub(r"\s+", " ", match.group(1)).strip()
            if candidate and candidate.lower() not in {"i", "we", "they", "it"}:
                return candidate
    return handle or None


def extract_roles(segment: str) -> list[str]:
    lowered = segment.lower()
    hits: list[str] = []
    for role in ROLE_WORDS:
        if role in lowered:
            pretty = " ".join(word.capitalize() if word.islower() else word for word in role.split())
            if pretty not in hits:
                hits.append(pretty)
    bullets = re.findall(r"(?:^|\n)\s*[•\-*▪]\s*([A-Za-z][\w/ +#.\-]{2,45})", segment)
    for bullet in bullets:
        candidate = bullet.strip(" .,-")
        if re.search(r"(?:engineer|developer|intern|designer|scientist|manager|analyst|researcher|architect|trainee|fresher)", candidate, re.IGNORECASE):
            if candidate not in hits:
                hits.insert(0, candidate)
    return hits[:5]


MULTI_JOB_SPLIT = re.compile(r"(?=^\s*([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3})\s+is\s+hiring\b)", re.MULTILINE)


def split_multi_jobs(text: str) -> list[str]:
    matches = list(MULTI_JOB_SPLIT.finditer(text))
    if len(matches) < 2:
        return [text]
    segments: list[str] = []
    lead = text[: matches[0].start()].strip()
    if lead:
        segments.append(lead)
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        segments.append(text[start:end].strip())
    return [segment for segment in segments if segment]


@dataclass
class Posting:
    text: str
    links: list[tuple[str, str]] = field(default_factory=list)
    companies: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    comp: list[str] = field(default_factory=list)
    eligibility: dict[str, Any] = field(default_factory=dict)
    deadline: dict[str, Any] = field(default_factory=dict)
    apply_urls: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "companies": self.companies,
            "roles": self.roles,
            "locations": self.locations,
            "comp": self.comp,
            "eligibility": self.eligibility,
            "deadline": self.deadline,
            "apply_urls": self.apply_urls,
            "links": [{"url": url, "label": label} for url, label in self.links],
        }


def build_posting(text: str, links: list[tuple[str, str]], handle: str, resolve: bool = True) -> list[Posting]:
    cleaned = clean_text(text)
    segments = split_multi_jobs(cleaned)
    postings: list[Posting] = []
    for segment in segments:
        posting = Posting(text=segment, links=list(links))
        posting.companies = [company for company in [extract_company(segment, handle)] if company]
        posting.roles = extract_roles(segment)
        posting.locations = parse_locations(segment)
        posting.comp = parse_compensation(segment)
        posting.eligibility = parse_eligibility(segment)
        posting.deadline = parse_deadline(segment)
        for url, _label in links:
            if is_internal_link(url):
                continue
            resolved = resolve_short_url(url) if resolve else url
            if not resolved:
                continue
            resolved = re.sub(r"[?&](?:%s)=[^&\s]*" % "|".join(TRACKING_PARAMS), "", resolved)
            if is_internal_link(resolved):
                continue
            posting.apply_urls.append(resolved)
        posting.apply_urls = list(dict.fromkeys(posting.apply_urls))
        postings.append(posting)
    return postings


def snippet(text: str, limit: int = 180) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    return flat if len(flat) <= limit else flat[: limit - 1] + "\u2026"


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")