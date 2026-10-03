# AGENTS.md

Python 3.11+ CLI (`xmaxxing`) that scrapes X (Twitter) with Playwright, scores posts against a
profile, and builds a review-first outreach queue. Has a local web dashboard. No linter, no
typechecker config.

## Commands (verified on this machine)

The two interpreters are **not** interchangeable:

| Purpose | Command | Interpreter |
| --- | --- | --- |
| Tests | `py -m pytest tests -q` | system `py` — has pytest, **no playwright** |
| One test | `py -m pytest tests/test_parsers.py -q -k role_match` | system `py` |
| Corpus report | `py tests/test_corpus.py` | system `py` — exits non-zero on a noise leak |
| Offline scorer/parser checks (no network) | `.\.venv\Scripts\python.exe -m xmaxxing selftest` | `.venv` — has playwright, **no pytest** |
| Local dashboard | `.\.venv\Scripts\python.exe -m xmaxxing dashboard` | `.venv` |
| Anything live | `.\.venv\Scripts\python.exe -m xmaxxing run --minutes 10 --dry-run` | `.venv` |

- `selftest` exits non-zero if any of its 10 expectations fail.
- `--config` is a **top-level** flag: `python -m xmaxxing --config path.toml run`, not
  `run --config`. Relative `[paths]` values resolve against the config file's directory.
- `run.ps1` is fixed and forwards arguments, but invoke the module directly anyway - it is
  Windows-only and the venv-split above is the real reason.

### Never run these unattended

`run`, `login`, and `review` need a live logged-in X session and open a real Chromium window
(`[run] headless = false`). `login`/`review` block on stdin. `--dry-run` writes nothing at all
(including `seen.json`). `run` is time-boxed by `--minutes`; **Ctrl+C now requests a graceful
stop** rather than a traceback, because `cli.cmd_run` installs a SIGINT handler.

### Never run these unattended

`run`, `login`, and `review` need a live logged-in X session and open a real Chromium window
(`[run] headless = false`). `login`/`review` block on stdin, and `browser.ensure_login` loops
**forever** if the session is rejected. Prefer `--dry-run`, which writes nothing at all
(including `seen.json`). `run` is time-boxed by the `--minutes` wall clock, not by post count.

## Architecture: one runner, two frontends

`runner.Runner.run()` owns the whole pipeline; `cli.cmd_run` and the dashboard's Start button both
call it. **Don't grow a second code path** — two runners means two definitions of "stopped".
Supporting modules: `control.RunControl` (time budget + stop flag), `events.EventBus` (fan-out to
SSE subscribers), `dashboard.DashboardState` (owns the single in-flight run, reads output files),
`server.py` (loopback HTTP), `sender.py` (send policy + verification), `style.py` (tone).

Playwright's sync API is greenlet-based and **not thread-safe**. A run therefore lives in one
worker thread that owns its own `sync_playwright()`; nothing from that thread may escape it. Only
one run at a time — the persistent context locks the profile dir, enforced by `DashboardState.busy`
and not by the browser.

Cancellation is `control.should_stop()`, polled in `sources.iter_search`/`iter_home` and in
`browser.ensure_login`. Stop latency is **seconds, not instant** — the loop may be inside a 4s
sleep or a 6s retry wait. Don't promise instant stops in the UI.

### Localhost threat model (`server.py`)

The server binds `127.0.0.1` only and gates every `/api/*` on a per-process token
(`DashboardState.token`) sent as `X-Xmaxxing-Token`. Both matter: **any web page you visit can
`POST http://127.0.0.1:PORT/api/start`**, because a simple request needs no preflight. The custom
header forces a preflight the browser then refuses, and a mismatched `Origin` is rejected. Keep all
three defences if you touch this file.

## Pipeline order (`xmaxxing/runner.py`)

`seen` dedupe → promoted check → whole-post score + verdict → multi-job split into segments →
**re-score and re-verify each segment independently** → `posting_key` dedupe vs. existing
jsonl → append jsonl + markdown digest → optional reply-thread link harvest → action queue →
conditional auto-send.

Consequences worth remembering:

- Scoring is **per segment**. A segment failing its own gate is dropped, and no segment inherits
  the post header's kind (a job can't become a hackathon because the post mentioned one).
- `record_wrote_job` gates everything downstream: a post whose only surviving segments are
  hackathons never reaches `actions.jsonl`.
- `posting_key` = first apply URL (lowercased, trailing `/` stripped), else
  `company|role0|comp`. This is what collapses reposts of the same job.
- Actions are deduped against existing `actions.jsonl` keys before being appended — a duplicate
  row means messaging someone twice.
- `t.co` resolution (`extract.resolve_short_url`, cached in `extract._RESOLVED`) is a network
  call inside parsing. Pass `resolve=False` / `--no-resolve` to keep parsing offline.

## Outreach state machine

`actions.jsonl` rows carry `status`: `pending → sent | skipped | dismissed | attempted | failed`.
**Always resolve rows back into the file** (`outreach.resolve_action`, atomic tmp+replace). Without
that the queue is write-only and `review` re-offers the same items forever — that was a real bug.

`sender.SendPolicy` decides what may auto-send, and it is deliberately inert by default:
`mode = "review"`. `auto_after` requires the reviewed count; `auto_after_sends` caps a run. Only
`reply` is sendable — DM and follow always queue. `--dry-run` never sends. The stop check sits
**between typing and pressing send**; that's what makes Stop a kill switch.

`SendOutcome` distinguishes `sent` (verified in the thread) from `attempted` (pressed, unconfirmed).
Never record an unverified post as sent — retrying a reply that landed is how you double-message.
The UI renders scraped text via `textContent` and scheme-checks every `href`; a `javascript:` URL in
a tweet must never become a link.

## `config.example.toml` is the shipped source of truth

Queries, scoring weights, threshold, role gate, DOM selectors, output paths, and the `[outreach]`
send policy all live in `config.toml`; outreach wording lives in `outreach_templates.toml`. Three
traps:

- **`config.toml` is gitignored — it holds the user's identity.** Only `config.example.toml` is
  tracked, and it ships placeholders. Change scoring/queries/selectors in **both**; change `[profile]
  identity` only in `config.example.toml`. Tests load `config.example.toml`, never `config.toml`,
  so a local tuning never changes CI results.
- `outreach_templates.toml` is resolved as `config.path.parent / "outreach_templates.toml"` —
  hardcoded, **not** overridable via `[paths]`.
- X's markup is scraped, so breakage shows up as "0 articles found / everything rejected", not an
  exception. When extraction regresses, check `[selectors]` in `config.example.toml` before touching
  `dom.py`.
- Changing scoring is a data exercise, not a guess: rejections with reason + signal breakdown
  land in `rejected.jsonl`, and `review` verdicts land in `labels.jsonl` for `tune` to replay.

## Tests

Fully offline, ~93 tests, ~14s. The two committed corpora under `tests/fixtures/` are
**synthetic fixtures** — never delete, and never paste real scraped posts into them (they're
committed, and `test_fixture_corpora_never_leak_a_real_handle` enforces it):

- `tests/fixtures/noise.md` — 29 noise posts; **0** may be accepted.
- `tests/fixtures/legacy_keeps.md` — 24 posts in the old `## Post by …` digest format; **>=20**
  must still be detected.

Both are read by `tests/test_corpus.py`, which is also runnable as a script
(`py tests/test_corpus.py`) for a precision/recall report and a non-zero exit on any leak.

Run tests with the **system `py`**, which has pytest and no playwright. Two traps:

- Don't rewrite source files with PowerShell `Set-Content -Encoding UTF8` — it adds a BOM and
  silently replaces non-ASCII characters (`—`, `₹`, `…`), breaking tests that assert on them.
- Tests inject into the module-level `extract._RESOLVED` cache to fake `t.co` resolution — clean up
  after yourself if you add tests there.

## Hard constraints (asserted by tests — don't regress them)

- Drafts use the **public identity only**: whatever `[profile] headline` and `[profile] portfolio`
  say. There is deliberately no field for academic status, and `tests/test_parsers.py` fails if a
  composed draft ever mentions it (student, university, college, b.tech, cgpa/sgpa, …). **Those
  checks must run on the final composed string, after every transform** — a style or template
  pass that runs afterwards can reintroduce a banned word and the suite won't see it.
- `config.example.toml` must not reintroduce `grad_year` / `graduation_year` / `year_of_study` /
  `sgpa` / `cgpa` keys; `outreach_templates.toml` and `README.md` are grepped for the same words.
- `config.example.toml` must ship **placeholders only** —
  `test_example_config_ships_placeholders_only` enforces it structurally (no hardcoded handles).
- `outreach_templates.toml` must not set top-level `headline`/`portfolio`; they're always filled
  from `[profile]`. `dm_founder` and `follow_up` are currently **never rendered** —
  `build_draft` picks only `dm_recruiter` or `reply_post`.
- Outreach is review-first by design: `run` only writes drafts, `review --open-browser` prefills
  the composer and **a human sends it**. Auto-send exists but is gated behind
  `[outreach] mode != "review"`, covers replies only, verifies before claiming `sent`, and stops
  between typing and pressing. Don't widen any of those without asking.

## Safety

`auth.json` holds live session cookies and `x_user_data*/` holds a real browser profile — treat
both as passwords. Gitignored: `config.toml`, all runtime output (`*.jsonl`, `seen.json`, `logs/`,
the markdown digests `jobs_and_hackathons.md` / `hackathons.md`), and `*.pdf`. Keep changes scoped
to code, `config.example.toml`, templates, and tests. `.github/workflows/ci.yml` fails the build if
`config.toml` or any runtime output is ever tracked.

## Conventions

- Root `x_scraper.py` is the pre-rewrite legacy script. It is not imported by `xmaxxing`; read it
  only to understand where `filters.LEGACY_NEGATIVE_PATTERNS` came from.
- `run.ps1` bootstraps the venv but does not forward CLI arguments (see above); the module is
  invoked directly.
- Commits use Conventional Commits prefixes (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).