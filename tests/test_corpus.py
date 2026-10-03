from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xmaxxing import config as config_module, filters

# The shipped example config, never a developer's local config.toml - otherwise
# CI results depend on whatever someone tuned locally.
EXAMPLE_CONFIG = Path(__file__).resolve().parent.parent / "config.example.toml"

BLOCK = re.compile(r"^##\s+Post\s+by\s+(?P<who>.+?)\n(?P<body>.*?)(?=^##\s+Post\s+by\s|\Z)", re.MULTILINE | re.DOTALL)
LINK = re.compile(r"^\- \*\*Tweet Link:\*\*\s*(?P<url>\S+)", re.MULTILINE)
QUOTE = re.compile(r"^\s*>\s?(?P<line>.*)$", re.MULTILINE)


def parse_markdown(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="ignore")
    posts: list[dict[str, str]] = []
    for match in BLOCK.finditer(text):
        body = match.group("body")
        link = LINK.search(body)
        lines = [line.group("line").rstrip() for line in QUOTE.finditer(body)]
        content = "\n".join(line for line in lines if line.strip())
        posts.append({
            "who": match.group("who").strip(),
            "url": link.group("url") if link else "",
            "text": content,
        })
    return posts


def report(posts: list[dict[str, str]], scorer, title: str) -> dict[str, int]:
    accepted: list[tuple[int, str, str]] = []
    for post in posts:
        if not post["text"].strip():
            continue
        result = scorer.score_text(post["text"], has_links="http" in post["text"])
        verdict, reason = scorer.verdict(result)
        if verdict:
            accepted.append((result.score, post["who"][:34], reason))
    print(f"\n=== {title}: {len(accepted)}/{len(posts)} accepted")
    for score, who, reason in sorted(accepted, reverse=True):
        print(f"  {score:>3}  {who:<36} {reason}")
    return {"posts": len(posts), "accepted": len(accepted)}


FIXTURES = Path(__file__).resolve().parent / "fixtures"
NOISE_CORPUS = FIXTURES / "noise.md"
KEEPS_CORPUS = FIXTURES / "legacy_keeps.md"


def main() -> int:
    config = config_module.load(EXAMPLE_CONFIG)
    scorer = filters.Scorer(config)
    print(f"threshold={scorer.threshold}")

    noise = parse_markdown(NOISE_CORPUS)
    kept = parse_markdown(KEEPS_CORPUS)
    noise_stats = report(noise, scorer, "noise.md (pure noise)")
    kept_stats = report(kept, scorer, "legacy_keeps.md (legacy keeps)")

    print("\n--- verdict ---")
    print(f"noise leaked: {noise_stats['accepted']}/{noise_stats['posts']}")
    print(f"legacy keeps retained: {kept_stats['accepted']}/{kept_stats['posts']}")
    return 0 if noise_stats["accepted"] == 0 else 1


def test_noise_corpus_is_rejected() -> None:
    config = config_module.load(EXAMPLE_CONFIG)
    scorer = filters.Scorer(config)
    noise = parse_markdown(NOISE_CORPUS)
    assert noise, "tests/fixtures/noise.md corpus missing"
    leaked = [post for post in noise if scorer.verdict(scorer.score_text(post["text"]))[0]]
    assert not leaked, f"{len(leaked)} noise posts accepted: " + " | ".join(p["who"][:40] for p in leaked)


def test_legacy_keeps_are_still_found() -> None:
    config = config_module.load(EXAMPLE_CONFIG)
    scorer = filters.Scorer(config)
    kept = parse_markdown(KEEPS_CORPUS)
    assert kept, "tests/fixtures/legacy_keeps.md corpus missing"
    found = [post for post in kept if scorer.verdict(scorer.score_text(post["text"], has_links="http" in post["text"]))[0]]
    assert len(found) >= 20, f"only {len(found)}/{len(kept)} legacy keeps still detected"


def test_fixture_corpora_never_leak_a_real_handle() -> None:
    """Fixtures are committed, so they must stay synthetic."""
    import re as _re

    for path in (NOISE_CORPUS, KEEPS_CORPUS):
        text = path.read_text(encoding="utf-8")
        handles = set(_re.findall(r"@([A-Za-z0-9_]+)", text))
        for handle in handles:
            assert not handle.startswith(("your-name", "your-handle")), (
                f"{path.name} references a real account @{handle}"
            )
        for url in _re.findall(r"https?://[^\s)]+", text):
            assert "x.com" in url or "example" in url, f"{path.name} has a non-synthetic URL: {url}"


if __name__ == "__main__":
    raise SystemExit(main())