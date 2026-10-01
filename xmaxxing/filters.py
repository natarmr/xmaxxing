from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

LEGACY_NEGATIVE_PATTERNS: list[str] = [
    r"\bi\s+am\s+open\s+for\s+new\s+roles\b",
    r"\bi\s+am\s+open\s+to\b",
    r"\bopen\s+for\s+new\s+tech\s+roles\b",
    r"\bopen\s+to\s+(?:work|roles|opportunities)\b",
    r"\blooking\s+for\s+(?:a\s+)?(?:job|internship|role|opportunity)\b",
    r"\bactively\s+(?:seeking|looking)\b",
    r"\bmy\s+last\s+ppo\b",
    r"\blet\s+me\s+know\s+if\s+you\s+are\s+hiring\b",
    r"\bt&c\s+apply\b",
    r"\bterms\s+(?:and\s+conditions\s+)?apply\b",
    r"\bfees?\s+apply\b",
    r"\bcharges?\s+apply\b",
    r"\bapply\s+to\s+all\b",
    r"\bdoes\s+not\s+apply\b",
    r"\brule\s+.*?\s+apply\b",
    r"\brules\s+.*?\s+apply\b",
    r"\bcomment\s+['\"].*?['\"]\s+and\s+i(?:'ll|'m|’ll|’m|\s+will)\s+dm\b",
    r"\bcomment\s+['\"].*?['\"]\s+to\s+get\b",
    r"\breply\s+['\"].*?['\"]\s+and\s+i(?:'ll|'m|’ll|’m|\s+will)\s+dm\b",
    r"\bdm\s+me\s+the\s+word\b",
    r"\bdm\s+['\"]\w+['\"]\b",
    r"\breply\s+[\"'].*?[\"']\s+and\s+i\s+will\b",
    r"\bcomment\s+[\"'].*?[\"']\s+and\s+i\s+will\b",
    r"\bpay\s+[\d,]+\s*(?:rs|inr|usd)?\s+to\s+do\s+the\s+internship\b",
    r"\bintern:\s",
    r"\bjuniors?:\s",
    r"\bseniors?:\s",
    r"\bboss:\s",
    r"\bmanager:\s",
    r"\bdeveloper:\s",
    r"\bhot\s+take\b",
    r"\bmy\s+first\s+remote\s+job\b",
    r"\bmy\s+first\s+job\b",
    r"\bhow\s+to\s+get\s+a\s+job\b",
    r"\badvice\s+to\b",
    r"\bproductivity\s+advice\b",
    r"\bmental\s+clutter\b",
    r"\bclass\s+action\b",
    r"\bclass-action\b",
    r"\bsec\s+fines\b",
    r"\bgeopolitical\b",
    r"\bkremlin\b",
    r"\bputin\b",
    r"\braised\s+(?:around|nearly|over|\$)?\s*\d+\s*(?:million|m|billion|b)\b",
    r"\bvaluation\b",
    r"\bseed\s+round\b",
    r"\bseries\s+[a-f]\b",
    r"\bexit\s+liquidity\b",
    r"\boption\s+sweep\b",
    r"\bcalls?\s+at\s+the\s+ask\b",
    r"\bintraday\b",
    r"\btrading\s+competition\b",
    r"\bpre-order\b",
    r"\bpre\s+order\b",
    r"\bhardware\s+wallet\b",
    r"\bgiveaway\b",
    r"\bgive-away\b",
    r"\bfree\s+trial\b",
    r"\bfellowship\b",
    r"\bug\s+program\b",
    r"\badmission\s+through\b",
    r"\bdegree\s+cutoff\b",
    r"\bcgpa\b",
]

HACKATHON_PATTERNS: list[str] = [
    r"\bhackathons?\b",
    r"\bbuildathons?\b",
    r"\bideathons?\b",
    r"\bdatathons?\b",
    r"\bcode\s?fest\b",
    r"\bcompetitions?\b",
    r"\bcontests?\b",
    r"\bjam\b.{0,20}\b(?:prize|register|submit)\b",
]

JOB_PATTERNS: list[str] = [
    r"\bhiring\b",
    r"\blooking for\b",
    r"\bopenings?\b",
    r"\bfounding (?:engineer|researcher|scientist|developer)\b",
    r"\bapply\b",
    r"\broles?\b",
    r"\bpositions?\b",
    r"\bjob\b",
    r"\bstipend\b",
    r"\bvacanc(?:y|ies)\b",
    r"\bdrives?\b",
    r"\bon-?site\b",
    r"\bfull[- ]?time\b",
    r"\bpart[- ]?time\b",
    r"\binternship\b",
    r"\bopportunit(?:y|ies)\b",
    r"\bjoin (?:us|the team)\b",
]

INTERNSHIP_TERMS: list[str] = [
    r"\binternships?\b",
    r"\bintern\b",
    r"\btrainee\b",
    r"\bapprentice\b",
    r"\bfresher\b",
    r"\bgraduate engineer\b",
    r"\bco[- ]?op\b",
    r"\bstipend\b",
]

HACKATHON_CTA_PATTERNS: list[str] = [
    r"\bregister(?:s|ed|ing)?\b",
    r"\bsign ?ups?\b",
    r"\bapply(?:ing|ied|ies)?\b",
    r"\bsubmit(?:s|ting|ted)?\b",
    r"\bsubmissions?\b",
    r"\bparticipat(?:e|es|ed|ing|ion|ors?)\b",
    r"\bentr(?:y|ies)\b",
    r"\bdeadlines?\b",
    r"\bprizes?\b",
    r"\bwinner(?:s)?\b",
    r"\bjoin(?:s|ed|ing)?\b",
    r"\bspots?\b",
    r"\brounds?\b",
    r"\blaunch(?:es|ed|ing)?\b",
    r"\bhosting\b",
    r"\bco-?host(?:ing)?\b",
    r"\bcall(?:s|ing)?\b",
    r"\bopen(?:ing)? (?:now|today|soon)\b",
    r"\bregistrations?\b",
    r"\beditions?\b",
    r"\bstars?\b",
    r"\bscheduled\b",
    r"\bparticipants?\b",
    r"\bteams?\b",
    r"\bapply\b",
    r"\bdms? (?:are )?open\b",
]


def _compile_all(patterns: Iterable[str]) -> list[tuple[str, re.Pattern[str]]]:
    return [(pattern, re.compile(pattern, re.IGNORECASE)) for pattern in patterns]


LEGACY_NEGATIVES = _compile_all(LEGACY_NEGATIVE_PATTERNS)
HACKATHON_CTA = _compile_all(HACKATHON_CTA_PATTERNS)
HACKATHONS = _compile_all(HACKATHON_PATTERNS)
JOBS = _compile_all(JOB_PATTERNS)
INTERNSHIP = _compile_all(INTERNSHIP_TERMS)


@dataclass
class Signal:
    name: str
    weight: int


@dataclass
class Score:
    text: str
    score: int = 0
    kind: str | None = None
    signals: list[str] = field(default_factory=list)
    hard_reject: str | None = None
    role_hit: str | None = None
    location_tag: str | None = None

    @property
    def accepted(self) -> bool:
        return self.hard_reject is None and self.score > 0 and self.kind is not None

    def reason(self) -> str:
        if self.hard_reject:
            return f"hard_negative:{self.hard_reject}"
        if self.score <= 0:
            return "below_threshold"
        if self.kind == "hackathon":
            return "hackathon_match:" + "+".join(self.signals)
        if self.role_hit is None:
            return "profile_role_mismatch"
        return f"{self.kind}_match:" + ("+".join(self.signals) or "none")


LOCATION_PATTERNS: dict[str, list[str]] = {
    "india": [
        r"\bindia\b",
        r"\bindian\b",
        r"\bbangalore\b",
        r"\bbengaluru\b",
        r"\bhyderabad\b",
        r"\bdelhi\b",
        r"\bmumbai\b",
        r"\bpune\b",
        r"\bchennai\b",
        r"\bkolkata\b",
        r"\bindore\b",
        r"\bcoimbatore\b",
        r"\bamravati\b",
        r"\bamaravati\b",
        r"\bnoida\b",
        r"\bgurgaon\b",
        r"\bgurugram\b",
        r"\btrichy\b",
        r"\bjaipur\b",
        r"\b Lucknow\b",
    ],
    "remote": [
        r"\bremote\b",
        r"\bwork from home\b",
        r"\bwfh\b",
        r"\bfully remote\b",
        r"\bremote[- ]first\b",
        r"\banywhere\b",
    ],
    "global": [
        r"\busa\b",
        r"\bunited states\b",
        r"\bunix?\b",
        r"\bcanada\b",
        r"\bengland\b",
        r"\bgermany\b",
        r"\bnetherlands\b",
        r"\bsingapore\b",
        r"\bdubai\b",
        r"\bremote[- ]global\b",
        r"\bworldwide\b",
        r"\bany country\b",
    ],
}


class Scorer:
    def __init__(self, config) -> None:
        scoring = config.scoring
        self.threshold = int(scoring.get("threshold", 5))
        self.positive = self._signals(scoring.get("positive", []))
        self.negative = self._signals(scoring.get("negative", []))
        profile = config.profile
        self.target_roles = [role.lower() for role in profile.get("target_roles", [])]
        self.location_priority = [tag.lower() for tag in profile.get("location_priority", [])]
        self._role_patterns = [
            (role, re.compile(
                r"(?<![\w])"
                + r"[\s\-_]+".join(re.escape(part) for part in role.split())
                + r"s?(?![\w])"
            ))
            for role in sorted(self.target_roles, key=len, reverse=True)
        ]

    @staticmethod
    def _signals(raw: list[dict[str, Any]]) -> list[tuple[str, int, list[re.Pattern[str]]]]:
        out = []
        for entry in raw:
            weight = int(entry.get("weight", 1))
            name = str(entry.get("name", "signal"))
            patterns = [re.compile(p, re.IGNORECASE) for p in entry.get("patterns", [])]
            out.append((name, weight, patterns))
        return out

    def location_tag(self, text: str) -> str | None:
        for tag in self.location_priority:
            for pattern in LOCATION_PATTERNS.get(tag, []):
                if re.search(pattern, text, re.IGNORECASE):
                    return tag
        return None

    def role_match(self, text: str) -> str | None:
        lowered = text.lower()
        for role, pattern in self._role_patterns:
            if pattern.search(lowered):
                return role
        return None

    def classify(self, text: str) -> str | None:
        for _, pattern in HACKATHONS:
            if pattern.search(text):
                return "hackathon"
        for _, pattern in JOBS:
            if pattern.search(text):
                return "job"
        return None

    def score_text(self, text: str, has_links: bool = False) -> Score:
        result = Score(text=text)
        if not text or not text.strip():
            result.hard_reject = "empty_text"
            return result

        for pattern, compiled in LEGACY_NEGATIVES:
            if compiled.search(text):
                result.hard_reject = pattern
                return result

        total = 0
        for name, weight, patterns in self.positive:
            if any(pattern.search(text) for pattern in patterns):
                total += weight
                result.signals.append(name)

        for name, weight, patterns in self.negative:
            if any(pattern.search(text) for pattern in patterns):
                total -= weight
                result.signals.append(f"-{name}")

        if has_links:
            total += 1
            result.signals.append("has_links")

        result.score = total
        result.kind = self.classify(text)
        result.role_hit = self.role_match(text)
        result.location_tag = self.location_tag(text)
        return result

    def is_internship(self, text: str) -> bool:
        return any(pattern.search(text) for _, pattern in INTERNSHIP)

    def verdict(self, result: Score) -> tuple[bool, str]:
        if result.hard_reject:
            return False, result.reason()
        if result.score < self.threshold:
            return False, f"score:{result.score}<{self.threshold}"
        if result.kind is None:
            return False, "no_kind"
        if result.kind == "hackathon":
            if not any(pattern.search(result.text) for _, pattern in HACKATHON_CTA):
                return False, "hackathon_without_cta"
            return True, result.reason()
        if result.role_hit is None:
            return False, "profile_role_mismatch"
        return True, result.reason()