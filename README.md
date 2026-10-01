# xmaxxing

Finds AI/ML, software and internship openings posted on X, scores them against your profile, and drafts the outreach you'd actually want to send.

Everything is config-driven: queries, scoring weights, role gate and outreach wording all live in `config.toml` and `outreach_templates.toml`.

## Install

```powershell
.\run.ps1 selftest
```

The first run creates `.venv`, installs `playwright` and fetches Chromium. Afterwards:

```powershell
.\run.ps1            # scrape for 30 minutes (config default)
.\run.ps1 run -Minutes 60
```

Python 3.12 is required. On this machine Python is invoked as `py`, which `run.ps1` handles.

## Commands

| Command | What it does |
| --- | --- |
| `.\run.ps1 login` | Opens a browser, you log in once, the session is saved to `auth.json` |
| `.\run.ps1 run` | Scrapes Search (default) or the home feed and writes results |
| `.\run.ps1 run --dry-run` | Scores everything, writes nothing - use this to tune |
| `.\run.ps1 review --open-browser` | Walks the action queue, prefills a draft, you hit send |
| `.\run.ps1 tune` | Measures the scorer against your own pass/reject labels |
| `.\run.ps1 selftest` | Offline scorer + parser checks, no network |
| `py -m pytest tests -q` | Full test suite |

Useful `run` flags: `--source search|home`, `--minutes N`, `--reply-links N` (opens the reply thread of the top N posts to recover apply links hidden in replies), `--no-resolve`, `--profile DIR`, `--auth FILE`.

## How a post is judged

1. **Hard negatives** (`xmaxxing/filters.py`) - the legacy denylist, kept intact: job seekers, engagement bait, paid internships, memes, market/news noise, giveaways.
2. **Weighted signals** (`[scoring]` in `config.toml`) - hiring language, role nouns, AI stack terms, apply CTAs, compensation, eligibility, minus promo/engagement/news/promo weights.
3. **Threshold** - `[scoring] threshold`, currently 4.
4. **Profile gate** - `[profile] target_roles`. Jobs must match a target role; hackathons are routed separately and skip the gate. Hyphen, space and plural variants are handled (`Front-End Developers` matches `front end developer`).

Every rejection is written to `rejected.jsonl` with the reason and the score breakdown, so the rules can be tuned from data instead of guesswork.

## Output

| File | Contents |
| --- | --- |
| `jobs_and_hackathons.md` / `hackathons.md` | Human digests grouped by day |
| `jobs.jsonl` / `hackathons.jsonl` | One JSON object per posting: company, roles, locations, comp, eligibility, deadline, apply URLs, score |
| `rejected.jsonl` | Everything filtered out, with reasons |
| `seen.json` | Tweet keys already processed (dedupe across runs) |
| `actions.jsonl` | Outreach queue with drafts |
| `labels.jsonl` | Your pass/reject verdicts, the input to `tune` |
| `logs/` | Per-run logs |

Multi-job posts ("X is hiring ... Y is hiring ...") are split into separate postings, so one tweet with six roles becomes six rows.

`t.co` links are resolved to real destinations. Posts whose only call to action is "link in replies" or "Telegram post" get their reply thread harvested with `--reply-links N`.

## Outreach policy

Review-first, by design:

1. `run` builds `actions.jsonl` with a draft per post. Nothing is sent.
2. `review` shows each item. `o` opens the post and prefills the draft so **you** read and send it. `y` / `n` record a verdict, Enter skips, `q` quits.
3. `tune` replays your verdicts and reports precision, recall and which signals fire on the posts you rejected. Tune `[scoring]` from that.

Rules the queue enforces: posts that already have a real apply link become `apply` (no message sent over a form), hackathons are excluded entirely, and only posts with an explicit invite become `reply` or `dm`.

Identity used in drafts is the public one only: `ML engineer`, portfolio `https://your-portfolio.example`. Wording lives in `outreach_templates.toml`, with `{role}`, `{company}`, `{their_requirement}`, `{proof}` and `{portfolio}` slots. A test asserts drafts never mention academic status.

## Safety

`auth.json` holds live session cookies - treat it as a password. `.gitignore` excludes it, every browser profile directory, the resume PDF, and all runtime output. Do not commit them.

## Layout

```
config.toml              queries, scoring weights, profile gate, selectors
outreach_templates.toml  reply / DM wording
run.ps1                  venv bootstrap + entry point
xmaxxing/
  browser.py             context launch, stealth, login detection
  sources.py             search pagination and home-feed scrolling
  dom.py                 per-article extraction
  extract.py             pure parsing: urls, text, comp, deadline, fields
  filters.py             hard negatives, weighted scorer, role gate
  outreach.py            action queue, reply-link harvest, review, tune
  storage.py             seen store, jsonl, markdown digests
  cli.py                 commands
tests/                   offline corpus replay + unit tests
```