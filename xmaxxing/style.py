from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

SENTENCE_SPLIT = re.compile(r"[.!?]+(?:\s|$)")
WORD = re.compile(r"[A-Za-z0-9']+")
CONTRACTION = re.compile(
    r"\b(?:i'm|i've|i'll|i'd|you're|you've|we're|we've|they're|can't|won't|don't|doesn't|didn't|isn't|aren't|"
    r"wasn't|weren't|it's|that's|there's|let's|we'll|i'd've)\b",
    re.IGNORECASE,
)
DASH = re.compile(r"—|--|\s-\s")
ELLIPSIS = re.compile(r"\.\.\.|…")
EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]"
)
BULLET = re.compile(r"^\s*(?:[•\-*▪]|\d+[.)])\s+", re.MULTILINE)
URL = re.compile(r"https?://\S+|\S+\.(?:com|dev|io|in|org)\b", re.IGNORECASE)


@dataclass(frozen=True)
class StyleProfile:
    """Measurable habits from a corpus of your own messages.

    Deliberately small and deterministic. This captures *shape* - length,
    punctuation, contractions, sign-off habits - because that is what can be
    measured with integer comparisons. It cannot and does not attempt to
    transfer novel phrasing: there is no model in this loop.

    Every field is derived from a file of real messages you point at. Empty
    fields mean "no preference detected" and the corresponding transform is
    skipped, so an absent or sparse corpus degrades to the plain template.
    """

    samples: int = 0
    mean_chars: float = 0.0
    mean_sentences: float = 0.0
    contraction_rate: float = 0.0
    dash_rate: float = 0.0
    ellipsis_rate: float = 0.0
    exclamation_rate: float = 0.0
    emoji: tuple[str, ...] = ()
    uses_bullets: bool = False
    opens_with_greeting: bool = False
    closes_with_signoff: bool = False
    uppercase_shapes: bool = False

    @property
    def usable(self) -> bool:
        return self.samples >= 3


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 3) if values else 0.0


def _rate(hits: int, total: int) -> float:
    return round(hits / total, 3) if total else 0.0


def load_messages(path: str | Path | None) -> list[str]:
    """Read a corpus of your own messages, one message per blank-line block.

    Lines starting with `>` are stripped so a thread pasted from X works as-is.
    """
    if not path:
        return []
    file = Path(path)
    if not file.exists():
        return []
    raw = file.read_text(encoding="utf-8", errors="ignore")
    messages = []
    for block in re.split(r"\n\s*\n", raw):
        lines = []
        for line in block.splitlines():
            cleaned = re.sub(r"^\s*>\s?", "", line)
            if cleaned.strip():
                lines.append(cleaned.strip())
        text = "\n".join(lines).strip()
        if text:
            messages.append(text)
    return messages


def analyse(messages: list[str]) -> StyleProfile:
    if not messages:
        return StyleProfile()
    lengths: list[float] = []
    sentence_counts: list[float] = []
    contractions = words = dashes = ellipses = exclamations = 0
    emoji_seen: dict[str, int] = {}
    bullets = greeting = signoff = uppercase = 0

    for message in messages:
        text = URL.sub(" ", message)
        lengths.append(len(message))
        sentence_counts.append(len([s for s in SENTENCE_SPLIT.split(text) if s.strip()]))
        found = WORD.findall(text)
        words += len(found)
        contractions += len(CONTRACTION.findall(text))
        dashes += len(DASH.findall(text))
        ellipses += len(ELLIPSIS.findall(text))
        exclamations += text.count("!")
        for char in EMOJI.findall(text):
            emoji_seen[char] = emoji_seen.get(char, 0) + 1
        if BULLET.search(message):
            bullets += 1
        first_line = next((line.strip() for line in message.splitlines() if line.strip()), "")
        if re.match(r"^(hi|hey|hello|yo|good (morning|afternoon|evening))\b", first_line, re.IGNORECASE):
            greeting += 1
        last_line = message.strip().splitlines()[-1].strip().lower()
        if re.search(
            r"\b(call|chat|talk|reach out|let me know|thanks|thank you|cheers|regards|best|open to)\b", last_line
        ):
            signoff += 1
        letters = [c for c in message if c.isalpha()]
        if letters and sum(c.isupper() for c in letters) / len(letters) > 0.6:
            uppercase += 1

    total = len(messages)
    top_emoji = tuple(
        char for char, count in sorted(emoji_seen.items(), key=lambda item: (-item[1], item[0]))
        if count >= 2
    )
    return StyleProfile(
        samples=total,
        mean_chars=_mean(lengths),
        mean_sentences=_mean(sentence_counts),
        contraction_rate=_rate(contractions, words),
        dash_rate=_rate(dashes, total),
        ellipsis_rate=_rate(ellipses, total),
        exclamation_rate=_rate(exclamations, total),
        emoji=top_emoji,
        uses_bullets=_rate(bullets, total) > 0.3,
        opens_with_greeting=_rate(greeting, total) > 0.5,
        closes_with_signoff=_rate(signoff, total) > 0.5,
        uppercase_shapes=_rate(uppercase, total) > 0.3,
    )


def from_config(config) -> StyleProfile:
    raw = config.get("profile", "style_corpus", "") or ""
    return analyse(load_messages(raw))


def apply(draft: str, profile: StyleProfile, seed: str = "") -> str:
    """Shape a finished draft toward the measured profile.

    Only safe, reversible transforms, and each one is skipped when the profile
    has nothing to say about it. Runs *after* template fill, so the identity and
    forbidden-word checks in the test suite must run on the result of this.
    """
    if not draft or not profile.usable:
        return draft
    text = draft.strip()

    if profile.closes_with_signoff:
        final = text.splitlines()[-1].strip()
        if not re.search(r"\b(15[- ]minute|week|call|chat|reach out|let me know)\b", final, re.IGNORECASE):
            closer = "\n\nHappy to talk this week."
            text = text + closer

    if profile.opens_with_greeting and not re.match(r"^(hi|hey|hello|yo)\b", text, re.IGNORECASE):
        if profile.emoji:
            text = f"{profile.emoji[pick_index(len(profile.emoji), seed)]} {text}"

    # Length budget: trim the closing flourish rather than cutting a sentence
    # mid-thought. Only applies when we have a real measurement to compare to.
    if profile.mean_chars and len(text) > profile.mean_chars * 1.6:
        lines = text.splitlines()
        while len(lines) > 2 and len("\n".join(lines)) > profile.mean_chars * 1.6:
            lines.pop()
        text = "\n".join(lines)

    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def strip_accents(value: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", value) if not unicodedata.combining(char)
    )