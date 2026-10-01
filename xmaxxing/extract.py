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


def absolutize(url: str) -> str:
    if url.startswith(("http://", "https://")):
        return url
    if url.startswith("/"):
        return "https://x.com" + url
    return url


def status_key(url: str | None) -> str | None:
    if not url:
        return None
    match = STATUS_RE.search(absolutize(url))
    if not match:
        return None
    return f"{match.group('user').lower()}/{match.group('id')}"


def canonical_status_url(url: str | None) -> str | None:
    if not url:
        return None
    match = STATUS_RE.search(absolutize(url))
    if not match:
        return None
    return f"https://x.com/{match.group('user')}/status/{match.group('id')}"


def clean_text(raw: str) -> str:
    text = raw.replace("\u2028", " ").replace("\u00a0", " ")
    text = re.sub(r"(https?://)\s*\n\s*", r"\1", text)
    text = re.sub(r"(https?://[^\n]*?)[ \t]+(?=[\w/?#=&.\-])", r"\1", text)
    text = re.sub(r"(https?://[^\n]*[\-/])\s*\n\s*(-?[\w.\-])", r"\1\2", text)
    text = re.sub(r"(https?://[^\n]*?)\n([-/?#.&=~_+%][^\s]*)", r"\1\2", text)
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
        if LINK_LABEL.match(label_text):
            label_text = re.sub(r"\s+", "", label_text)
            label_text = TRAILING_JUNK.sub("", label_text)
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


LINK_LABEL = re.compile(r"^https?://", re.IGNORECASE)


def resolve_short_url(url: str, timeout: float = 6.0) -> str | None:
    if "t.co/" not in url:
        return url
    if url in _RESOLVED:
        return _RESOLVED[url]
    request = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": "Mozilla/5.0 (compatible; xmaxxing-link-resolver/0.1)"},
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


COMPANY_STOPWORDS = {
    "i", "we", "they", "it", "this", "that", "the", "a", "an", "and", "or", "but",
    "are", "is", "am", "was", "who", "what", "when", "where", "our", "your", "my",
    "their", "his", "her", "its", "there", "here", "also", "now", "just", "still",
    "one", "two", "few", "some", "many", "new", "all", "any", "no", "yes", "hi",
    "hello", "hey", "everyone", "anyone", "someone", "nobody", "people", "team",
    "agent", "agents", "engineer", "engineers", "developer", "developers", "intern",
    "interns", "role", "roles", "graduates", "fresher", "fresherS",
}


def extract_company(segment: str, handle: str) -> str | None:
    patterns = [
        r"^\s*([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3})\s+is\s+(?:hiring|looking)\b",
        r"\b([A-Z][\w&.\-]{1,}(?:\s+[A-Z][\w&.\-]{1,})?)\s+is\s+(?:hiring|looking)\b",
        r"\bat\s+@([A-Za-z0-9_]+)",
        r"\b([A-Z][\w&.\-]{2,})\s+is\s+hiring\b",
        r"\bcompany\s*[:\-]\s*([A-Z][\w&.\- ]{1,30})",
        r"\bbrand\s*[:\-]\s*([A-Z][\w&.\- ]{1,30})",
        r"\borgani[sz]ation\s*[:\-]\s*([A-Z][\w&.\- ]{1,30})",
    ]
    for pattern in patterns:
        match = re.search(pattern, segment, re.IGNORECASE)
        if not match:
            continue
        candidate = re.sub(r"\s+", " ", match.group(1)).strip(" -:")
        words = candidate.lower().split()
        if not candidate or len(words) > 4:
            continue
        if all(word in COMPANY_STOPWORDS for word in words):
            continue
        if words[-1] in COMPANY_STOPWORDS and len(words) > 1:
            candidate = " ".join(words[:-1])
        return candidate
    return handle or None


ACRONYMS = {
    "ai": "AI", "ml": "ML", "llm": "LLM", "llms": "LLMs", "nlp": "NLP", "nda": "NDA",
    "cv": "CV", "qa": "QA", "sre": "SRE", "sde": "SDE", "hr": "HR", "api": "API",
    "apis": "APIs", "gpu": "GPU", "gpus": "GPUs", "js": "JS", "ts": "TS", "ui": "UI",
    "ux": "UX", "pm": "PM", "db": "DB", "dba": "DBA", "seo": "SEO", "ios": "iOS",
    "android": "Android", "genai": "GenAI", "mlops": "MLOps", "devops": "DevOps",
    "fullstack": "FullStack", "deepseek": "DeepSeek", "rag": "RAG",
}


def titleize(role: str) -> str:
    return " ".join(ACRONYMS.get(word, word.capitalize()) for word in role.split())


def extract_roles(segment: str) -> list[str]:
    lowered = segment.lower()
    hits: list[str] = []
    for role in ROLE_WORDS:
        if role in lowered:
            pretty = titleize(role)
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


VALID_URL = re.compile(r"^https?://[a-z0-9-]+(\.[a-z0-9-]+)+(?:/|\?)")
TRAILING_JUNK = re.compile(r"[\s…\.。、,]+$")


def best_target(url: str, label: str = "", resolve: bool = True) -> str | None:
    if is_internal_link(url):
        return None
    candidates: list[str] = []
    if "t.co/" in url:
        if resolve:
            resolved = resolve_short_url(url)
            if resolved:
                candidates.append(resolved)
        candidates.append(url)
    else:
        candidates.append(url)
    if label and LINK_LABEL.match(label.strip()):
        candidates.append(re.sub(r"\s+", "", label.strip()))
    for candidate in candidates:
        if not candidate or is_internal_link(candidate):
            continue
        cleaned = re.sub(r"[?&](?:%s)=[^&\s]*" % "|".join(TRACKING_PARAMS), "", candidate)
        if VALID_URL.match(TRAILING_JUNK.sub("", cleaned)):
            return TRAILING_JUNK.sub("", cleaned)
    return None


TEXT_URL = re.compile(r"https?://[^\s]+")


def align_urls(text_urls: list[str], dom_targets: list[str], fallback_index: int | None = None) -> list[str]:
    aligned: list[str] = []
    for text_url in text_urls:
        match = next(
            (target for target in dom_targets if target.startswith(text_url) or text_url.startswith(target)),
            None,
        )
        aligned.append(match or text_url)
    if not aligned and fallback_index is not None and 0 <= fallback_index < len(dom_targets):
        aligned = [dom_targets[fallback_index]]
    return aligned


def build_posting(text: str, links: list[tuple[str, str]], handle: str, resolve: bool = True) -> list[Posting]:
    cleaned = clean_text(text)
    segments = split_multi_jobs(cleaned)
    dom_targets: list[str] = []
    for url, label in links:
        target = best_target(url, label, resolve=resolve)
        if target and target not in dom_targets:
            dom_targets.append(target)
    postings: list[Posting] = []
    multi = len(segments) > 1
    for index, segment in enumerate(segments):
        posting = Posting(text=segment, links=list(links))
        posting.companies = [company for company in [extract_company(segment, handle)] if company]
        posting.roles = extract_roles(segment)
        posting.locations = parse_locations(segment)
        posting.comp = parse_compensation(segment)
        posting.eligibility = parse_eligibility(segment)
        posting.deadline = parse_deadline(segment)
        local_urls = []
        for raw in TEXT_URL.findall(segment):
            target = best_target(raw, "", resolve=False)
            if target:
                local_urls.append(target)
        if local_urls:
            posting.apply_urls.extend(align_urls(local_urls, dom_targets, index if multi else None))
        elif multi:
            posting.apply_urls.extend(align_urls([], dom_targets, index))
        else:
            posting.apply_urls.extend(dom_targets)
        posting.apply_urls = list(dict.fromkeys(posting.apply_urls))
        postings.append(posting)
    return postings


def snippet(text: str, limit: int = 180) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    return flat if len(flat) <= limit else flat[: limit - 1] + "\u2026"


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")