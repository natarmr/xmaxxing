from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xmaxxing import config as config_module, extract, filters, outreach, storage

ROOT = Path(__file__).resolve().parent.parent
CONFIG = config_module.load(ROOT / "config.toml")
TEMPLATES = outreach.load_templates(ROOT / "outreach_templates.toml")

FORBIDDEN_IN_DRAFTS = (
    "year of study",
    "1st year",
    "first year",
    "second year",
    "third year",
    "student",
    "university",
    "college",
    "b.tech",
    "btech",
    "graduat",
    "sgpa",
    "cgpa",
)


def test_status_key_canonicalises_every_variant() -> None:
    variants = [
        "https://x.com/ajay_2512x/status/2065013524519330282",
        "https://x.com/ajay_2512x/status/2065013524519330282?s=20&twclid=abc",
        "https://x.com/ajay_2512x/status/2065013524519330282/analytics",
        "https://x.com/ajay_2512x/status/2065013524519330282/photo/1",
        "https://twitter.com/Ajay_2512x/status/2065013524519330282",
        "/ajay_2512x/status/2065013524519330282",
        "/ajay_2512x/status/2065013524519330282/analytics",
    ]
    keys = {extract.status_key(url) for url in variants}
    assert keys == {"ajay_2512x/2065013524519330282"}, keys
    assert extract.canonical_status_url("/ajay_2512x/status/123/photo/1") == "https://x.com/ajay_2512x/status/123"
    assert extract.status_key("https://x.com/search?q=hiring") is None


def test_clean_text_repairs_wrapped_urls() -> None:
    raw = "Apply here:\nhttps://\njobfound.org/job/x\n-is-hiring-for-y\n\nExtra  Salary: 5-12 LPA"
    cleaned = extract.clean_text(raw)
    assert "https://jobfound.org/job/x-is-hiring-for-y" in cleaned, cleaned
    assert "https://\n" not in cleaned
    assert "Extra Salary" in cleaned
    spaced = extract.clean_text("Pre-order at http://\nbrilliant.xyz\n/?utm_source=x\n…")
    assert "http://brilliant.xyz/?utm_source=x" in spaced, spaced


def test_compensation_parsing() -> None:
    assert extract.parse_compensation("Expected Salary: 25-40 LPA") == ["25-40 LPA"]
    assert extract.parse_compensation("Stipend: INR 15,000/month") == ["~15,000/month"], extract.parse_compensation("Stipend: INR 15,000/month")
    dollars = extract.parse_compensation("$200K-$250K + equity")
    assert dollars and dollars[0].startswith("$200K"), dollars
    assert extract.parse_compensation("₹1.04Cr-₹1.85Cr | Full Time") == ["1.04-1.85 CR"]


def test_deadline_parsing() -> None:
    parsed = extract.parse_deadline("Deadline: June 17, 2026 (EOD)", today=extract.date(2026, 6, 1))
    assert parsed["date"] == "2026-06-17"
    assert parsed["expired"] is False
    past = extract.parse_deadline("Submit before March 2", today=extract.date(2026, 6, 1))
    assert past["expired"] is True, past
    assert extract.parse_deadline("No dates here")["date"] is None


def test_eligibility_years() -> None:
    parsed = extract.parse_eligibility("Graduation Year: 2023 / 2024 / 2025 / 2026")
    assert parsed["years"] == [2023, 2024, 2025, 2026]
    assert parsed["tagged"] is True
    assert extract.parse_eligibility("No eligibility mentioned")["tagged"] is False


def test_multi_job_splitting() -> None:
    text = (
        "Harborleaf is hiring for Frontend Developer\nExpected Salary: 5-12 LPA\nApply here:\nhttps://a\n\n"
        "SingleStore is hiring for Software Engineer\nExpected Salary: 30-40 LPA\nApply here:\nhttps://b"
    )
    segments = extract.split_multi_jobs(text)
    assert len(segments) == 2, segments
    assert segments[0].startswith("Harborleaf")
    assert extract.split_multi_jobs("Just one job posting") == ["Just one job posting"]


def test_posting_filters_internal_links() -> None:
    links = [
        ("https://t.co/abc123", ""),
        ("https://x.com/someone/status/999", ""),
        ("https://x.com/i/bookmarks", ""),
    ]
    postings = extract.build_posting("Company is hiring AI interns. Apply now", links, "tester", resolve=False)
    assert len(postings) == 1
    assert postings[0].apply_urls == ["https://t.co/abc123"], postings[0].apply_urls
    assert postings[0].companies == ["Company"], postings[0].companies
    assert "ai intern" in [role.lower() for role in postings[0].roles]


def test_dedupe_links_strips_tracking() -> None:
    result = extract.dedupe_links([
        ("https://x.com/a/status/1?s=20&twclid=z", "https://x.com/a/status/1?s=20&twclid=z"),
        ("https://x.com/a/status/1", ""),
        ("https://x.com/a/status/1?ref_src=twsrc", "same"),
    ])
    assert len(result) == 1, result
    assert result[0][0] == "https://x.com/a/status/1"
    assert result[0][1] == ""


def test_action_policy() -> None:
    dm_record = {"text": "We are hiring AI interns. DM me your resume", "kind": "job", "apply_urls": []}
    assert outreach.decide_action(dm_record, CONFIG)[0] == "dm"
    reply_record = {"text": "Hiring AI interns! Comment your portfolio", "kind": "job", "apply_urls": []}
    assert outreach.decide_action(reply_record, CONFIG)[0] == "reply"
    form_record = {"text": "Hiring AI interns", "kind": "job", "apply_urls": ["https://boards.greenhouse.io/x"]}
    assert outreach.decide_action(form_record, CONFIG)[0] == "apply"
    hackathon_record = {"text": "Hackathon registration open, apply now", "kind": "hackathon", "apply_urls": []}
    assert outreach.decide_action(hackathon_record, CONFIG)[0] == "none"


def test_drafts_use_public_identity_only() -> None:
    record = {
        "key": "acme/1",
        "text": "Acme is hiring AI engineers with experience in RAG systems and Python.\nApply now",
        "kind": "job",
        "display_name": "Grace Hopper",
        "handle": "acme",
        "roles": ["AI Engineer"],
        "company": "Acme",
    }
    draft = outreach.build_draft(record, "dm", TEMPLATES, CONFIG)
    assert "ML engineer" in draft, draft
    assert "https://your-portfolio.example" in draft, draft
    assert "{" not in draft, draft
    lowered = draft.lower()
    for phrase in FORBIDDEN_IN_DRAFTS:
        assert phrase not in lowered, f"draft leaked {phrase!r}: {draft}"
    reply = outreach.build_draft(record, "reply", TEMPLATES, CONFIG)
    assert "ML engineer" in reply
    assert not [phrase for phrase in FORBIDDEN_IN_DRAFTS if phrase in reply.lower()]


def test_draft_drops_unfilled_lines() -> None:
    record = {"key": "k/1", "text": "Hiring AI interns", "kind": "job", "display_name": "", "handle": "h", "roles": [], "company": ""}
    draft = outreach.build_draft(record, "reply", TEMPLATES, CONFIG)
    assert "Interested in the role opening" in draft, draft
    assert "\n\n\n" not in draft


def test_seen_store_roundtrip_and_seed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        store = storage.SeenStore(tmp_path / "seen.json")
        assert store.load() == 0
        store.add("acme/1", handle="acme")
        store.add("acme/1", handle="acme")
        assert len(store) == 1
        store.save()
        reloaded = storage.SeenStore(tmp_path / "seen.json")
        assert reloaded.load() == 1
        assert "acme/1" in reloaded

        legacy = tmp_path / "legacy.md"
        legacy.write_text(
            "## Post by A @a (2026)\n- **Tweet Link:** https://x.com/a/status/123/analytics\n",
            encoding="utf-8",
        )
        seeded = storage.SeenStore(tmp_path / "seed.json")
        assert seeded.seed_from_markdown(legacy) == 1
        assert "a/123" in seeded


def test_markdown_digest_groups_by_day() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        digest = storage.MarkdownDigest(Path(tmp) / "jobs.md", "Jobs")
        digest.append({
            "scraped_at": "2026-10-02 09:00:00",
            "company": "Acme",
            "roles": ["AI Engineer"],
            "handle": "acme",
            "tweet_url": "https://x.com/acme/status/1",
            "text": "We are hiring.",
            "score": 7,
            "signals": ["hiring_action"],
            "apply_urls": ["https://boards.greenhouse.io/acme"],
        })
        content = digest.path.read_text(encoding="utf-8")
        assert "### 2026-10-02" in content
        assert "Acme — AI Engineer" in content
        assert "boards.greenhouse.io/acme" in content


def test_scorer_accepts_ai_jobs_and_rejects_noise() -> None:
    scorer = filters.Scorer(CONFIG)
    good = [
        "We are hiring AI engineers who treat evals as the actual work. SF only. $200K-$250K + equity.",
        "Eka Care is hiring Agentic AI Interns! Work on real AI systems in healthcare. Looking for candidates with Agent/RAG project.",
        "Machine Learning Engineers wanted in Bangalore. Stipend 25k/month. Apply now.",
        "Registration is open for the Bharatiya Antariksh Hackathon 2026. Prize pool 5L. Deadline: June 30",
    ]
    bad = [
        "Everyone is hiring. My feed is filled with founders and companies hiring.",
        "Congrats to everyone who qualified for the Amazon HackOn 48-Hour Hackathon!",
        "Comment \"GO\" and I'll DM you the full guide.",
        "Keycard Shell is the first modular hardware wallet. Get your starter kit today.",
        "22 lpa",
    ]
    for text in good:
        assert scorer.verdict(scorer.score_text(text, has_links=True))[0], text
    for text in bad:
        assert not scorer.verdict(scorer.score_text(text, has_links=True))[0], text


def test_hackathon_gate_requires_a_call_to_action() -> None:
    scorer = filters.Scorer(CONFIG)
    keep = [
        "Registration is open for the Bharatiya Antariksh Hackathon 2026. Prize pool 5L. Deadline June 30. Apply now.",
        "ISRO launches Bharatiya Antariksh Hackathon (BAH) 2026. The third edition features problem statements in AI/ML.",
        "Six weeks. $60,000+ in prizes. The PayPal AI Hackathon: Build What is Next with PayPal and AI is live.",
    ]
    drop = [
        "Your October crypto calendar is here. From major crypto events and token unlocks to key macro data, there is a lot to watch.",
        "Heading to New York for @RippleSwell 2026? The side events Luma calendar is now live, and it is the place for every event.",
        "holy moly, we won the 1st prize on the hackathon with a 3D tea shop using three.js. Huge thank you to the sponsors!",
        "Less than 6 hours left in Steve Arena Season 1. Put your agent to work and compete for 15M $STEVE and Superteam payouts.",
    ]
    for text in keep:
        assert scorer.verdict(scorer.score_text(text, has_links=True))[0], text
    for text in drop:
        assert not scorer.verdict(scorer.score_text(text, has_links=True))[0], text


def test_relative_hrefs_from_the_dom_are_accepted() -> None:
    assert extract.status_key("/jack/status/1750000000000000000") == "jack/1750000000000000000"
    assert extract.canonical_status_url("/jack/status/1750000000000000000/analytics") == "https://x.com/jack/status/1750000000000000000"
    assert extract.absolutize("/i/bookmarks") == "https://x.com/i/bookmarks"


def test_company_extraction_handles_labelled_fields() -> None:
    assert extract.extract_company("Company: RiNVENT CoE\nRole: Fresher Assistant", "aakshayy12") == "RiNVENT CoE"
    assert extract.extract_company("Flex is hiring for Associate Software Engineer", "nitesh_singh5") == "Flex"
    assert extract.extract_company("Acme is hiring AI engineers", "poster") == "Acme"
    assert extract.extract_company("We are hiring interns at @acmehq", "poster") == "acmehq"
    assert extract.extract_company("nothing useful here", "fallback") == "fallback"


def test_apply_url_resolution_beats_the_href() -> None:
    links = [("https://t.co/abc123", "https://jobfound.org/job/flex-is-hiring")]
    unresolved = extract.build_posting("Flex is hiring for Associate Software Engineer", links, "n", resolve=False)[0]
    assert unresolved.apply_urls == ["https://t.co/abc123"], unresolved.apply_urls
    direct = extract.build_posting(
        "Flex is hiring for Associate Software Engineer",
        [("https://jobfound.org/job/flex", "")],
        "n",
        resolve=False,
    )[0]
    assert direct.apply_urls == ["https://jobfound.org/job/flex"], direct.apply_urls
    resolved = extract.build_posting("Flex is hiring", [("https://t.co/plain", "")], "n", resolve=False)[0]
    assert resolved.apply_urls == ["https://t.co/plain"], resolved.apply_urls


def test_multi_job_posts_get_their_own_apply_link() -> None:
    text = (
        "Weekday is hiring for Forward Deployment Engineer\nExpected Salary: 8-14 LPA\nApply here:\n"
        "https://jobfound.org/job/weekday-forward-deployment\n\n"
        "Poshmark is hiring for Machine Learning Engineer\nExpected Salary: 20-30 LPA\nApply here:\n"
        "https://jobfound.org/job/poshmark-ml-engineer"
    )
    postings = extract.build_posting(text, [], "aggregator", resolve=False)
    assert len(postings) == 2
    assert postings[0].companies == ["Weekday"], postings[0].companies
    assert postings[1].companies == ["Poshmark"], postings[1].companies
    assert postings[0].apply_urls == ["https://jobfound.org/job/weekday-forward-deployment"], postings[0].apply_urls
    assert postings[1].apply_urls == ["https://jobfound.org/job/poshmark-ml-engineer"], postings[1].apply_urls
    assert postings[0].comp == ["8-14 LPA"] and postings[1].comp == ["20-30 LPA"]


def test_url_shape_validation_rejects_junk() -> None:
    assert extract.best_target("http://B.Tech/MCA", "", resolve=False) is None
    assert extract.best_target("https://x.com/a/status/1", "", resolve=False) is None
    assert extract.best_target("https://t.co/x", "https://jobfound.org/job/a", resolve=False) == "https://t.co/x"
    assert extract.best_target("https://t.co/x", "https://jobfound.org/job/a …", resolve=False) == "https://t.co/x"
    assert extract.best_target("https://jobfound.org/job/flex", "", resolve=False) == "https://jobfound.org/job/flex"
    assert extract.best_target("https://t.co/x", "http://B.Tech/MCA", resolve=False) == "https://t.co/x"


def test_wrapped_link_text_is_rejoined() -> None:
    wrapped = ("https://jobslinking.com/ehs-officer-hc", "bs/", "…")
    links = extract.dedupe_links([("https://t.co/jhg8Q7z04b", "\n".join(wrapped))])
    assert links[0][1] == "https://jobslinking.com/ehs-officer-hcbs/", links[0][1]
    posting = extract.build_posting(
        "HCBS is hiring\nApply: https://jobslinking.com/ehs-officer-hcbs/",
        [("https://jobslinking.com/ehs-officer-hcbs/", "")],
        "n",
        resolve=False,
    )[0]
    assert posting.apply_urls == ["https://jobslinking.com/ehs-officer-hcbs/"], posting.apply_urls


def test_competitions_alone_are_not_hackathons() -> None:
    scorer = filters.Scorer(CONFIG)
    job = "Schonfeld is hiring a Quantitative Developer Intern. Batch 2027/2028. Stipend 6-8 Lakh/month. Location Hong Kong."
    assert scorer.classify(job) != "hackathon"
    assert scorer.classify("Registration is open for the ISRO hackathon 2026") == "hackathon"


def test_truncated_text_urls_are_replaced_by_the_resolved_dom_url() -> None:
    text = (
        "GIVA is hiring for Frontend Developer\nExpected Salary: 6-8 LPA\nApply here:\n"
        "https://jobfound.org/job/giva-is-hi\n\n"
        "OATI is hiring for AI Engineer\nExpected Salary: 5-7 LPA\nApply here:\n"
        "https://jobfound.org/job/oati-is-hi"
    )
    dom = [
        ("https://t.co/aaa", ""),
        ("https://t.co/bbb", ""),
    ]
    extract._RESOLVED["https://t.co/aaa"] = "https://jobfound.org/job/giva-is-hiring-for-frontend-developer-bengaluru"
    extract._RESOLVED["https://t.co/bbb"] = "https://jobfound.org/job/oati-is-hiring-for-ai-engineer-mohali"
    try:
        postings = extract.build_posting(text, dom, "aggregator", resolve=True)
    finally:
        extract._RESOLVED.pop("https://t.co/aaa", None)
        extract._RESOLVED.pop("https://t.co/bbb", None)
    assert postings[0].apply_urls == ["https://jobfound.org/job/giva-is-hiring-for-frontend-developer-bengaluru"], postings[0].apply_urls
    assert postings[1].apply_urls == ["https://jobfound.org/job/oati-is-hiring-for-ai-engineer-mohali"], postings[1].apply_urls


def test_betting_and_va_spam_never_passes_any_kind() -> None:
    scorer = filters.Scorer(CONFIG)
    spam = [
        "Most betting platforms compete on the same things: More markets. More odds. More promotions. Enter our contest for a chance to win prizes!",
        "REMOTE: CUSTOMER SUPPORT, SALES & VIRTUAL ASSISTANT N300,000/month. No experience needed, work from home immediately.",
    ]
    for text in spam:
        assert scorer.score_text(text, has_links=True).hard_reject, text


def test_posting_key_collapses_reposts_of_the_same_job() -> None:
    base = {"company": "MatriceAI", "roles": ["ML Engineer"], "comp": ["12-18 LPA"], "apply_urls": ["https://tinyurl.com/4mrkkmzu"]}
    repost = {"handle": "someone_else", "company": "Matrice AI", "roles": ["ML Engineer"], "comp": [], "apply_urls": ["https://tinyurl.com/4mrkkmzu/"]}
    assert extract.posting_key(base) == extract.posting_key(repost)
    different = {"company": "Acme", "roles": ["ML Engineer"], "comp": [], "apply_urls": []}
    assert extract.posting_key(different) != extract.posting_key(base)
    assert extract.posting_key(different).startswith("cr:acme")


def test_role_match_handles_hyphens_and_plurals() -> None:
    scorer = filters.Scorer(CONFIG)
    assert scorer.role_match("Hiring Front-End Developers") == "front end developer"
    assert scorer.role_match("We need Machine Learning Engineers") == "machine learning engineer"
    assert scorer.role_match("Founding AI Eng. role") == "eng."
    assert scorer.role_match("Purely unrelated gardening post") is None


def test_query_list_is_wellformed() -> None:
    queries = CONFIG.queries
    assert len(queries) >= 5
    for query in queries:
        assert query.get("q") and query.get("name")
        assert "filter:replies" in query["q"], query["name"]


def test_no_year_of_study_in_shipped_config_or_templates() -> None:
    forbidden_keys = ("grad_year", "graduation_year", "year_of_study", "sgpa =", "cgpa =")
    config_text = (ROOT / "config.toml").read_text(encoding="utf-8").lower()
    for key in forbidden_keys:
        assert key not in config_text, f"config.toml declares {key!r}"
    for path in (ROOT / "outreach_templates.toml", ROOT / "README.md"):
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8").lower()
        for phrase in ("year of study", "graduation year", "sgpa", "cgpa", "b.tech", "university"):
            assert phrase not in text, f"{path.name} leaked {phrase!r}"