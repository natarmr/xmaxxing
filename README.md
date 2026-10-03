# xmaxxing

Finds AI/ML, software and internship openings posted on X, scores them against your profile, and drafts the outreach you'd actually want to send.

Everything is config-driven: queries, scoring weights, role gate and outreach wording all live in `config.toml` and `outreach_templates.toml`.

## Install

```powershell
git clone https://github.com/natarmr/xmaxxing
cd xmaxxing
Copy-Item config.example.toml config.toml   # then edit [profile]
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

Or `pip install -e .` for the `xmaxxing` entry point.

`config.toml` is gitignored - it holds your identity. The repo only ships `config.example.toml`,
so the only thing you must edit is `[profile]`: `headline`, `portfolio`, `github` and
`proof_points`. Those are the values that get substituted into every draft you send.

Python 3.11+ is required (`tomllib`). `run.ps1` bootstraps the venv on Windows if you prefer it.

## Dashboard

```powershell
.\.venv\Scripts\python.exe -m xmaxxing dashboard
```

A local web UI on `127.0.0.1`. Press **Start** and it opens a browser window to scrape; press
**Stop** and it finishes the current step and shuts down. Live counters, and tabs for jobs,
hackathons, the outreach queue, rejections with their reason and signal breakdown, and reply
links. New outreach items raise a browser notification - click it to jump to the item.

```powershell
.\.venv\Scripts\python.exe -m xmaxxing dashboard --port 0 --no-open
```

Everything stays on your machine. The server binds loopback only and every API call needs a
per-process token that is printed in the URL on startup, so a random web page you visit cannot
drive your scraper.

## Commands

```powershell
$py = ".\.venv\Scripts\python.exe"

& $py -m xmaxxing login                  # one-time: log in, session saved to auth.json
& $py -m xmaxxing selftest               # offline scorer + parser checks, no network
& $py -m xmaxxing run --minutes 10 --dry-run   # score everything, write nothing
& $py -m xmaxxing run                    # scrape and write results
& $py -m xmaxxing review --open-browser  # walk the queue, prefills a draft, you send
& $py -m xmaxxing tune                   # precision/recall against your own verdicts
& $py -m xmaxxing redraft                # rebuild drafts after editing templates
```

Test suite (fully offline, no browser needed - use the system Python, which has pytest):

```powershell
py -m pytest tests -q
py -m pytest tests/test_parsers.py -q -k role_match    # one test
py tests/test_corpus.py                                # human-readable scorer report
```

Useful `run` flags: `--source search|home`, `--minutes N`, `--reply-links N` (opens the reply thread of the top N posts to recover apply links hidden in replies), `--no-resolve`, `--profile DIR`, `--auth FILE`. `--dry-run` scores everything and writes nothing, including `seen.json`. `--config` is a top-level flag: `python -m xmaxxing --config path.toml run`.

## How a post is judged

1. **Hard negatives** (`xmaxxing/filters.py`) - the legacy denylist, kept intact: job seekers, engagement bait, paid internships, memes, market/news noise, giveaways.
2. **Weighted signals** (`[scoring]` in `config.toml`) - hiring language, role nouns, AI stack terms, apply CTAs, compensation, eligibility, minus promo/engagement/news/promo weights.
3. **Threshold** - `[scoring] threshold`, currently 4.
4. **Profile gate** - `[profile] target_roles`. Jobs must match a target role; hackathons are routed separately and skip the gate. Hyphen, space and plural variants are handled (`Front-End Developers` matches `front end developer`).

Every rejection is written to `rejected.jsonl` with the reason and the score breakdown, so the rules can be tuned from data instead of guesswork. Multi-job posts are split into separate postings and **each scoring is per segment**, so a job can never inherit a hackathon kind from the post header, and a segment that fails its own gate is dropped. `competition`/`contest` only imply a hackathon when paired with a registration or prize signal.

## Output

| File | Contents |
| --- | --- |
| `jobs_and_hackathons.md` / `hackathons.md` | Human digests grouped by day |
| `jobs.jsonl` / `hackathons.jsonl` | One JSON object per posting: company, roles, locations, comp, eligibility, deadline, apply URLs, score |
| `rejected.jsonl` | Everything filtered out, with reasons |
| `seen.json` | Tweet keys already processed (dedupe across runs) |
| `actions.jsonl` | Outreach queue with drafts (reply/dm/follow only - apply-link posts stay out) |
| `reply_links.jsonl` | Apply links recovered from reply threads |
| `labels.jsonl` | Your pass/reject verdicts, the input to `tune` |
| `logs/` | Per-run logs |

Multi-job posts ("X is hiring ... Y is hiring ...") are split into separate postings, so one tweet with six roles becomes six rows.

`t.co` links are resolved to real destinations. X hard-wraps long links, so the visible text can be a truncated prefix; where that happens the resolved URL wins. Posts whose only call to action is "link in replies" or "Telegram post" get their reply thread harvested with `--reply-links N`.

## Outreach policy

Review-first, and the default:

1. `run` builds `actions.jsonl` with a draft per post. Nothing is sent.
2. `review` shows each item. `o` opens the post and prefills the draft so **you** read and send
   it. `y` / `n` record a verdict, Enter skips, `q` quits. Either way the row is marked resolved,
   so the same item doesn't come back next time.
3. `tune` replays your verdicts and reports precision, recall and which signals fire on the posts
   you rejected. Tune `[scoring]` from that.

Rules the queue enforces: posts that already have a real apply link become `apply` (no message sent
over a form), hackathons are excluded entirely, and only posts with an explicit invite become
`reply` or `dm`.

### When you're ready to let it send

`[outreach] mode` in `config.toml`:

| mode | behaviour |
| --- | --- |
| `review` | Never auto-sends. Drafts queue for you. The default. |
| `auto_after` | You send the first `auto_after` items, then replies send themselves. |
| `auto` | Replies send themselves immediately. |

Three things hold regardless of mode:

- **Replies only.** DM and follow actions always queue for review - auto-DM needs a different
  composer and a different navigation path, so it isn't wired up.
- **`--dry-run` never sends**, whatever the mode says.
- **Stop is a real kill switch.** Pressing Stop while a reply is composed leaves the draft typed
  but unsent, and an unverified post is recorded as `attempted`, never `sent`.

Every send is verified against the thread before it is marked `sent`. If it can't be confirmed, or
X shows an error, the row records what happened instead of claiming success - because a retry of
something that actually landed is how you end up messaging someone twice. `auto_after_sends` caps
how many replies go out per run.

Identity used in drafts is whatever you put in `[profile]` - public by design. There is no field
for academic status anywhere in the config, and a test fails the build if a composed draft ever
mentions it. Wording lives in `outreach_templates.toml`, with `{role}`, `{company}`,
`{their_requirement}`, `{proof}`, `{headline}` and `{portfolio}` slots. Note that `{headline}` and
`{portfolio}` are filled from `[profile]`, not from the template file - setting them there does
nothing.

### Matching your voice, deterministically

There is no model in this loop. Two mechanisms, both reproducible:

- **Variants.** A template can carry a `variants = [...]` list alongside its `text`. The one used
  is picked by a hash of the tweet key, so a given post always renders the same way - including
  across a `redraft`.
- **Style profile.** Point `[profile] style_corpus` at a file of your own messages and measurable
  habits are extracted: length, sentence count, contraction rate, dash and ellipsis habits,
  greeting and sign-off. Those steer variant choice and the length budget.

This shapes *form*, not novel phrasing. There is no language model anywhere in the pipeline, so
the scorer, the role gate, dedupe and the send policy are all the same deterministic code they
always were.

## Safety

`auth.json` holds live session cookies - treat it as a password. `.gitignore` excludes it, every
browser profile directory, `config.toml`, and all runtime output. Do not commit them.

Scraping X breaks X's Terms of Service and the account you point this at is yours to lose. Use it
on a personal account, keep the query list short, and don't republish the output.

## Layout

```
config.example.toml        shipped template - copy to config.toml and edit [profile]
outreach_templates.toml    reply / DM wording
run.ps1                    Windows venv bootstrap (does not forward CLI arguments)
xmaxxing/
  browser.py               context launch, stealth, login detection
  sources.py               search pagination and home-feed scrolling
  dom.py                   per-article extraction
  extract.py               pure parsing: urls, text, comp, deadline, fields
  filters.py               hard negatives, weighted scorer, role gate
  outreach.py              action queue, reply-link harvest, review, tune
  storage.py               seen store, jsonl, markdown digests
  config.py                config loading
  cli.py                   commands
tests/
  test_parsers.py          unit tests - offline, no browser
  test_corpus.py           corpus replay - reports precision, exits non-zero on noise leaks
  fixtures/                synthetic corpora the corpus tests read
```