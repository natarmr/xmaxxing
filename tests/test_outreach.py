from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xmaxxing import config as config_module, outreach, style

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_CONFIG = config_module.load(ROOT / "config.example.toml")
TEMPLATES = outreach.load_templates(ROOT / "outreach_templates.toml")


# --- action ledger -------------------------------------------------------


def _write_actions(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_resolving_an_action_takes_it_out_of_the_queue() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "actions.jsonl"
        _write_actions(path, [{"key": "a/1", "status": "pending"}, {"key": "b/2", "status": "pending"}])
        assert outreach.resolve_action("a/1", "sent", path) is True
        pending = outreach.load_actions(path, status="pending")
        assert [row["key"] for row in pending] == ["b/2"], pending
        assert outreach.pending_count(path) == 1


def test_resolve_persists_the_timestamp_and_extras() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "actions.jsonl"
        _write_actions(path, [{"key": "a/1", "status": "pending", "draft": "hi"}])
        outreach.resolve_action("a/1", "sent", path, note="sent from the dashboard", sent_url="https://x.com/a/status/2")
        row = outreach.load_actions(path, status=None)[0]
        assert row["status"] == "sent"
        assert row["resolved_at"], row
        assert row["note"] == "sent from the dashboard"
        assert row["sent_url"] == "https://x.com/a/status/2"
        assert row["draft"] == "hi", "other fields must survive the rewrite"


def test_resolve_rejects_an_unknown_status() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "actions.jsonl"
        _write_actions(path, [{"key": "a/1", "status": "pending"}])
        try:
            outreach.resolve_action("a/1", "teleported", path)
        except ValueError:
            pass
        else:
            raise AssertionError("unknown status must raise")


def test_resolving_a_missing_key_is_a_no_op() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "actions.jsonl"
        _write_actions(path, [{"key": "a/1", "status": "pending"}])
        assert outreach.resolve_action("zz/9", "sent", path) is False
        assert outreach.load_actions(path, status="pending"), "file must be untouched"


def test_resolved_count_drives_the_auto_send_gate() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "actions.jsonl"
        assert outreach.resolved_count(path) == 0
        _write_actions(path, [
            {"key": "a/1", "status": "pending"},
            {"key": "b/2", "status": "sent"},
            {"key": "c/3", "status": "dismissed"},
        ])
        assert outreach.resolved_count(path) == 2
        assert outreach.pending_count(path) == 1


def test_rewrite_survives_a_reload() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "actions.jsonl"
        rows = [{"key": f"a/{i}", "status": "pending", "n": i} for i in range(5)]
        _write_actions(path, rows)
        outreach.resolve_action("a/2", "sent", path)
        assert not list(Path(tmp).glob("*.tmp")), "temp file must be replaced, not left behind"
        reloaded = outreach.load_actions(path, status=None)
        assert len(reloaded) == 5, "rewrite must not drop rows"
        assert reloaded[2]["status"] == "sent" and reloaded[2]["n"] == 2


# --- deterministic tone: variant picking ---------------------------------


def test_pick_index_is_stable_for_a_seed() -> None:
    first = [outreach.pick_index(4, "acme/123") for _ in range(5)]
    assert len(set(first)) == 1, first
    assert 0 <= outreach.pick_index(3, "acme/123") < 3


def test_pick_index_handles_degenerate_counts() -> None:
    assert outreach.pick_index(0, "x") == 0
    assert outreach.pick_index(-1, "x") == 0


def test_template_variants_keeps_text_first_and_dedupes() -> None:
    entry = {"text": "base", "variants": ["alt one", "base", "alt two", "", 5]}
    assert outreach.template_variants(entry) == ["base", "alt one", "alt two"]


def test_draft_picks_the_same_variant_for_the_same_key() -> None:
    templates = {"reply_post": {"text": "base {role}", "variants": ["first {role}", "second {role}"]}}
    record = {"key": "acme/9", "text": "Acme is hiring", "kind": "job", "roles": ["AI Engineer"], "handle": "acme"}
    first = outreach.build_draft(record, "reply", templates, EXAMPLE_CONFIG)
    second = outreach.build_draft(record, "reply", templates, EXAMPLE_CONFIG)
    assert first == second
    assert first.strip() in {"base AI Engineer", "first AI Engineer", "second AI Engineer"}, first


def test_different_keys_can_reach_different_variants() -> None:
    templates = {"reply_post": {"text": "a", "variants": ["b", "c", "d"]}}
    picked = set()
    for index in range(40):
        record = {"key": f"k/{index}", "text": "hiring", "kind": "job", "handle": "h"}
        picked.add(outreach.build_draft(record, "reply", templates, EXAMPLE_CONFIG).strip())
    assert len(picked) > 1, f"variant selection is not varying: {picked}"


# --- deterministic tone: style profile -----------------------------------


CORPUS = """\
hey - saw your post, I build mostly in Python and LLM land
want to talk this week?

Hey! Quick note on the role. I work in Python, mostly retrieval and evals.
Happy to chat.

hi - keen on the opening, I do similar work daily
reach out if useful

Hey there. Short one: I've shipped a retrieval service with an eval set I
measured, and a small agent harness.
Let me know if you want the details.
"""


def test_style_profile_measures_a_corpus() -> None:
    profile = style.analyse(CORPUS.strip().split("\n\n"))
    assert profile.samples == 4, profile
    assert profile.usable is True
    assert profile.mean_chars > 0
    assert profile.opens_with_greeting is True
    assert profile.closes_with_signoff is True
    assert profile.contraction_rate > 0


def test_style_profile_is_empty_without_a_corpus() -> None:
    empty = style.analyse([])
    assert empty.usable is False
    assert empty.samples == 0


def test_a_thin_corpus_is_not_trusted() -> None:
    assert style.analyse(["hi there"]).usable is False


def test_style_load_messages_reads_blank_line_blocks(tmp_path=None) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "mine.txt"
        path.write_text("first message here\n\n> quoted second\nsecond line\n\n\nthird", encoding="utf-8")
        messages = style.load_messages(path)
    assert messages == ["first message here", "quoted second\nsecond line", "third"], messages


def test_style_load_messages_tolerates_a_missing_file() -> None:
    assert style.load_messages(None) == []
    assert style.load_messages("does-not-exist.txt") == []


def test_style_apply_is_a_no_op_without_a_profile() -> None:
    draft = "Interested in the role.\n"
    assert style.apply(draft, style.StyleProfile()) == draft


def test_style_apply_never_introduces_a_banned_word() -> None:
    """The forbidden-word check runs on the post-transform string, so prove it."""
    profile = style.analyse(
        [
            "hey - keen on this, I build in python!",
            "hi! yes, let me know",
            "hey there, happy to talk this week",
            "hi - keen, let me know if useful",
        ]
    )
    draft = outreach.build_draft(
        {"key": "a/1", "text": "Acme is hiring", "kind": "job", "roles": ["AI Engineer"], "handle": "acme"},
        "reply",
        TEMPLATES,
        EXAMPLE_CONFIG,
        profile,
    )
    lowered = draft.lower()
    for phrase in ("student", "university", "college", "graduat", "sgpa", "cgpa", "b.tech", "year of study"):
        assert phrase not in lowered, f"style transform leaked {phrase!r}: {draft}"
    assert "{" not in draft, draft


# --- reply links reach the action decision --------------------------------


def test_reply_links_change_the_action_decision() -> None:
    """--reply-links exists to stop you messaging people who posted a form.

    The harvested URLs must reach decide_action, or a post saying "link in
    replies" counts as having no apply link and gets messaged anyway.
    """
    record = {
        "key": "acme/1",
        "text": "Acme is hiring AI interns. Reply with your portfolio",
        "kind": "job",
        "handle": "acme",
        "roles": ["AI Intern"],
        "company": "Acme",
        "apply_urls": [],
    }
    assert outreach.decide_action(record, EXAMPLE_CONFIG)[0] == "reply"
    assert len(outreach.build_actions([record], TEMPLATES, EXAMPLE_CONFIG)) == 1

    reply_map = {"acme/1": ["https://boards.greenhouse.io/acme/jobs/1"]}
    suppressed = outreach.build_actions([record], TEMPLATES, EXAMPLE_CONFIG, reply_map)
    assert suppressed == [], f"a recovered apply link must suppress the outreach: {suppressed}"

    probe = dict(record, reply_urls=reply_map["acme/1"])
    action, reason = outreach.decide_action(probe, EXAMPLE_CONFIG)
    assert action == "apply", (action, reason)
    assert reason == "real_apply_link_exists", reason


def test_an_unresolved_reply_link_does_not_suppress_outreach() -> None:
    record = {
        "key": "acme/2",
        "text": "DM me about the AI intern role",
        "kind": "job",
        "handle": "acme",
        "apply_urls": [],
    }
    reply_map = {"acme/2": ["https://t.co/stillunresolved"]}
    assert len(outreach.build_actions([record], TEMPLATES, EXAMPLE_CONFIG, reply_map)) == 1


def test_reply_links_are_carried_onto_queued_actions() -> None:
    record = {
        "key": "acme/3",
        "text": "DM me about the intern role",
        "kind": "job",
        "handle": "acme",
        "roles": ["Intern"],
        "company": "Acme",
        "apply_urls": [],
    }
    reply_map = {"acme/3": ["https://twitter.com/acme/status/2"]}
    actions = outreach.build_actions([record], TEMPLATES, EXAMPLE_CONFIG, reply_map)
    assert len(actions) == 1
    assert actions[0]["reply_urls"] == reply_map["acme/3"], actions[0]
    assert actions[0]["status"] == "pending"
    assert actions[0]["draft"], "a queued action must carry a draft"