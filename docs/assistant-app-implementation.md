# Blind Trading Assistant — API Application Implementation Guide

Revised from the trader's draft (2026-08-08). This supersedes the draft; differences are
deliberate and explained inline. Hand this to the implementing engineer as-is.

**What this builds:** a standalone 2-tier advisory service (Sonnet triage → Fable deep
analysis) plus a playbook ingestion pipeline, replacing the Claude-Code-session advisor in
`training/advisor/` with a faster, always-consistent API application. The Claude Code
advisor folder stays as a fallback and as the source of the library content.

## Corrections applied to the draft (read first)

1. **Model IDs updated.** `claude-sonnet-4-6` is a previous-generation model. Use
   `claude-sonnet-5` (Tier 1) and `claude-fable-5` (Tier 2 + ingestion). Exact strings, no
   date suffixes.
2. **The caching bug.** The draft put `cache_control` on the *vector-retrieved rules* —
   dynamic per-request content. Prompt caching is a byte-exact **prefix** match, so that
   cache would never hit. Corrected: the stable system prompt + master playbook are cached
   in `system`; per-request content (images, triage report, retrieved excerpts) comes after.
3. **News window fixed.** The draft's "news within 30 minutes" is a day-trading rule. This
   system holds H4 swings; the trader's guide rule is **~12 hours** for high-impact events.
4. **A Tier 0 added.** News windows, Friday cutoffs, correlation caps, and one-setup-per-
   symbol checks are all computable in plain code from data the backend already has — free,
   deterministic, sub-millisecond. Don't spend a model call to discover CPI is in 3 hours.
5. **Vector DB deferred to v2.** Retrieval here is almost fully deterministic: the signal
   already names which strategy fired, so the backend knows exactly which playbook sections
   to load — no semantic search needed. Also: Anthropic has no embeddings endpoint (a vector
   DB adds a third-party embedding dependency, e.g. Voyage AI), and prompt caching makes
   "cache the whole master playbook" cheap (reads bill at ~0.1×). Qdrant remains the v2
   path if the ingested corpus ever outgrows a cacheable playbook.
6. **Strict JSON via structured outputs**, not "parse the text and hope" —
   `client.messages.parse()` / `output_config.format` guarantees schema-valid output.
7. **Blindness restored.** The draft dropped the core constraint of this role: the models
   must never learn the symbol or date (they could recall what happened next). Enforced in
   the input pipeline, not just the prompt (see §4).
8. **Verdict vocabulary aligned.** `TAKE / SKIP / ADJUST` (not EXECUTE), confidence
   low/med/high, "changes my mind" — matching the training program, the coach, and the
   quick-reference guide. ADJUST = exactly one level moved. The draft's "Execution
   Blueprint" is removed: the detector already computes entry/SL/TP and sizing; the
   assistant advises on the *proposed* levels, it does not invent new ones, and risk is
   never above the system's 1%.
9. **Authority hierarchy added.** Coach lessons (from real results) > quick-reference
   checklists / strategy specs > distilled library > ingested book content. Books are
   supporting evidence and never override the system's own tested rules.
10. **R:R floors corrected.** Not a flat 1:2 — the system's floors are 2.0 (SMC/Fib) and
    1.3 blended (EMA), already enforced by the detectors. The assistant doesn't re-derive
    them.
11. **Tiering rebalanced (trader's 2026-08-08 direction).** Tier 1 (Sonnet) is the
    primary decision-maker issuing full TAKE/SKIP/ADJUST verdicts; Fable is reserved for
    rare escalations (leaning-action AND genuinely unresolved) and runs as a speed-tuned
    *reviewer* of Sonnet's draft — effort `medium`, bounded output, narrow excerpts, only
    the named open questions — not a from-scratch analyst.

---

## 1. Architecture

```
                    ┌──────────────────────────────────────────────────┐
                    │              INGESTION PIPELINE (offline)        │
                    │  books_inbox/ ──► Fable (Batches API, 50% cost)  │
                    │        ──► strategy extractions + provenance     │
                    │        ──► AUTO-MERGE + consolidate (default)    │
                    │            [--review flag = manual gate]         │
                    │        ──► playbook/ files (snapshot-revertible) │
                    └──────────────────────────────────────────────────┘

  [ redacted D1 + H4 PNGs, signal metadata, econ calendar ]
         │
         ▼
  ┌─────────────────────┐   hard rule fails        ┌──────────────────┐
  │ TIER 0 — code       │ ────────────────────────► │ SKIP (<50 ms)    │
  │ mechanical prefilter│                           │ + named rule     │
  └─────────┬───────────┘                           └──────────────────┘
            ▼
  ┌─────────────────────┐   decides the vast majority   ┌────────────────────┐
  │ TIER 1 — Sonnet 5   │ ────────────────────────────► │ TAKE / SKIP /      │
  │ PRIMARY decision-   │                               │ ADJUST (quick,     │
  │ maker: thinking off,│                               │ target < 8 s)      │
  │ effort low, cached  │                               └────────────────────┘
  │ checklists          │   rare: leaning-action AND genuinely unresolved
  └─────────────────────┘ ──────────┐  ──► notify user: "escalating,
                                    │        draft is <take|adjust>"
                                    ▼
  ┌──────────────────────────────────────────────┐
  │ TIER 2 — Fable 5 (rare, speed-tuned)         │
  │ resolves ONLY the named open questions,      │
  │ reviewing Sonnet's draft verdict — effort    │
  │ medium, bounded output, cached playbook,     │
  │ narrow excerpts, streaming, fallbacks on     │
  └──────────────────┬───────────────────────────┘
                     ▼
  [ TAKE / SKIP / ADJUST + confidence + why + changes-my-mind ]
  [ every request + verdict logged to JSONL for the coach     ]
```

The human always decides. This service advises; it never places, modifies, or approves a
trade.

## 2. Tier 0 — mechanical prefilter (plain code)

Runs before any model call, on data the backend has natively (it is not blind — only the
models are). Auto-SKIP with the named rule when:

- a high-impact event for either currency of the pair falls within the configurable
  horizon (default **12h**) — computed from the already-normalized
  `Common\Files\econ_events.csv` the harness pipeline produces;
- entry time is late Friday (weekend gap on an H4 hold);
- a same-direction position is open in the same correlation cluster **and** total open
  risk would exceed the caps (3% concurrent / 2% per idea) — cluster table lives in the
  quick-reference guide, encode it as data;
- the symbol already has a live setup (one-setup-per-symbol).

Tier 0 also computes the **blind calendar encoding** passed onward: never event names,
currencies, or dates — only
`{"high_impact_ahead": bool, "hours_until": float, "affects": "base|quote|both", "recent_event_bias": "with|against|none"}`.

## 3. Playbook store & retrieval (v1 — no vector DB)

```
playbook/
  master_playbook.md        # distilled, cache-resident: 3 strategy checklists, regime
                            # table, edit rules, candlestick tells — built from
                            # training/quick-reference.html + training/advisor/library/
  lessons.md                # auto-extracted from quick-reference.html "Lessons" tab
                            # (parse the <!-- COACH:* --> anchored blocks) — refreshed
                            # whenever the coach edits the guide; HIGHEST authority
  smc.md | fib.md | ema.md  # per-strategy deep sections incl. ingested book material,
                            # tagged by pattern (#sweep-quality, #trap-liquidity, ...)
  general.md                # cross-strategy ingested material (structure, psychology)
```

**Retrieval is deterministic:** the signal metadata names the strategy → load that
strategy's file; Tier 1's `detected_patterns[]` and `open_questions[]` select tagged
sections within it (simple tag/heading match). Cap the assembled excerpt bundle (~6–10K
tokens); log what was dropped.

**Caching layout (order is tools → system → messages; stable content first):**

```python
system=[
    {"type": "text", "text": ROLE_PROMPT},               # frozen
    {"type": "text", "text": MASTER_PLAYBOOK},           # changes rarely
    {"type": "text", "text": COACH_LESSONS,              # changes ~weekly; a change
     "cache_control": {"type": "ephemeral", "ttl": "1h"}},  # rebuilds cache once — fine
]
# volatile per-request content (images, triage JSON, excerpts) goes in messages, AFTER
```

Never interpolate timestamps, session IDs, or per-request anything into `system`. Use the
1h TTL: decisions during a session are often >5 min apart, and the write premium (2× vs
1.25×) pays back after the third read. Verify with `usage.cache_read_input_tokens` — zero
across repeated calls means a silent invalidator. Cache minimums: 512 tokens (Fable),
1024 (Sonnet 5) — the playbook clears both easily.

## 4. Blind input pipeline

- **Screenshots:** captured by the harness (`ChartScreenShot`, engineer item C), then
  programmatically cropped: title bar band and time-axis band removed (fixed pixel
  regions for the standard tester window size). Depends on **item F2**
  (`InpBlindLabels`) so overlay label text carries no dates.
- Downscale Tier 1 copies to ≤1568 px long edge (latency/cost); Tier 2 may receive the
  original (both models accept up to 2576 px; a full-res image can cost up to ~4784
  image tokens vs ~1600 at 1568 px).
- **Signal metadata passed to models:** strategy fired, proposed entry/SL/TP *as relative
  R-distances and chart-visible prices*, session + day-of-week (never the date), cluster
  exposure summary, Tier 0's calendar encoding. **Never:** symbol, date, event names.
- Prompts additionally instruct: if a model recognizes the chart anyway, disregard
  recalled history and continue from the visible chart only.

## 4b. Market-context brief ("second advisor")

A separate, **non-blind** researcher produces a redacted market-context brief the blind
tiers consume — regime age/character, volatility percentile, macro backdrop in
base/quote terms, event density, instrument quirks — with symbol, dates, institutions,
and absolute prices censored. Interactive version exists as the `market-brief` skill
(`.claude/skills/market-brief/SKILL.md`); it writes
`training/advisor/inbox/market-brief.md`. Two modes with different integrity rules:

- **Live mode** (Phase 3): free research (web + data). No lookahead exists; the only
  constraint is the redaction contract (in the skill file — reuse it verbatim).
- **Historical mode** (training): **computed, never authored.** An LLM that lived
  through a period cannot describe it without hindsight, and web sources on past periods
  are retrospective — so historical briefs are a deterministic template filled from
  cutoff-truncated data, with no model-authored content and no web. Macro/quirk sections
  are omitted (not computable ⇒ not included).

**Engineer deliverable: `pipeline/export_d1_stats.py --symbol S --asof DATE`** — emits
JSON computed only from data ≤ DATE: D1 trend direction/age/character (swing sequence),
position in trailing 1y/2y range, realized-vol percentile vs trailing year, ATR
percentile, mean-reversion vs trend persistence stats, and event-density counts from the
dated econ CSV. Source the OHLC from the MT5 custom symbols (export step) or the
Dukascopy pipeline. The skill (and later the app) fills the template from this JSON.

App integration: the brief is a per-window/per-week artifact, not per-decision —
generated ahead of a session, injected as one text block in the Tier 1/Tier 2 user turn
(after the cached prefix), and flagged in the JSONL (`brief_present: true`) so the coach
can A/B brief-assisted vs. bare decisions.

## 5. System prompts

Keep these in versioned files, not inline strings. Contents (write them from
`training/advisor/CLAUDE.md` + `.claude/agents/deep-analyst.md`, which already encode the
tested behavior):

**Tier 1 (Sonnet 5) — the primary decision-maker.** Blindness rules; the quick-reference
Start-Here checks and per-strategy checklists (in the cached playbook). It delivers the
full `TAKE / SKIP / ADJUST` verdict for the vast majority of signals. It **escalates only
when both hold**: (a) its verdict would be TAKE or ADJUST, and (b) the checklist is
genuinely unresolved — steps in real conflict, or a chart that doesn't fit the playbook's
categories.

Two rules keep escalation rare and cheap:
- **Never escalate to confirm a SKIP.** A low-confidence skip is a skip — a missed trade
  costs 0R, and Fable can't improve on free.
- **Unsure between two adjustments = escalate**, don't guess (edits are where the coach
  data shows R is won and lost).

Output schema (single schema, both cases):

```json
{
  "verdict": "TAKE | SKIP | ADJUST",
  "confidence": "low | medium | high",
  "why": "1-3 plain sentences naming the decisive checklist step(s)",
  "changes_my_mind": "one sentence",
  "adjust": {"level": "...", "direction": "...", "to_structure": "...", "reason": "..."},
  "escalate": false,
  "regime": "trend | range | wild | transition",
  "detected_patterns": ["sweep-quality", "..."],
  "open_questions": ["is the pullback corrective or impulsive?"]
}
```

When `escalate` is true, `verdict` carries Sonnet's **draft**, `open_questions` names the
1–3 things it could not resolve, and the trader is notified immediately ("escalating —
draft is <verdict>") so even deep decisions give instant feedback. Monitor the escalation
rate via the JSONL log — target single-digit percent; if Sonnet escalates more, tighten
the prompt ("escalation is for genuine conflicts, not for reassurance").

**Tier 2 (Fable 5) — rare, speed-tuned reviewer.** Not a from-scratch re-analysis. Fable
receives Sonnet's full draft verdict + the named open questions, and is instructed to
**resolve only those questions** against the charts and a narrow excerpt bundle, then
confirm or overturn the draft — decisive and terse, no full checklist re-walk unless
answering a question requires it. Blindness rules; authority hierarchy (lessons >
checklists > library > books); ADJUST names exactly one level, two needed edits = SKIP;
"still unresolved after review" = SKIP at low confidence (never a coin-flip TAKE). Output
schema:

```json
{
  "verdict": "TAKE | SKIP | ADJUST",
  "confidence": "low | medium | high",
  "why": "1-3 plain sentences naming the decisive checklist step(s)/lesson(s)",
  "changes_my_mind": "one sentence",
  "adjust": {"level": "sl|tp|entry", "direction": "widen|tighten|raise|lower",
             "to_structure": "what visible structure it should clear", "reason": "..."}
}
```

(`adjust` present only for ADJUST verdicts.)

**Ingestion (Fable 5) — playbook builder.** Extract *mechanical, actionable* strategies
and rules from the provided text into the template below; skip narrative/lore; map each
extraction to `smc | fib | ema | general`; include provenance; never contradict the
system's own specs — if a book disagrees with the master playbook, emit it flagged
`conflicts_with_system: true` so the merge step quarantines it instead of merging.

```markdown
### [Rule or Strategy Name]
- **Maps to:** smc | fib | ema | general
- **Tags:** #sweep-quality #trap-liquidity ...
- **Market condition:** trending / ranging / reversal
- **The rule:** [1-3 mechanical sentences]
- **Invalidation:** [what makes it wrong]
- **Source:** [book, chapter/section]
- **Conflicts with system:** true/false [+ what it contradicts]
```

## 6. Execution pipeline (corrected reference implementation, Python)

```python
import anthropic
from pydantic import BaseModel
from typing import Literal, Optional

client = anthropic.Anthropic()

class Adjust(BaseModel):
    level: Literal["sl", "tp", "entry"]
    direction: Literal["widen", "tighten", "raise", "lower"]
    to_structure: str
    reason: str

class Verdict(BaseModel):
    verdict: Literal["TAKE", "SKIP", "ADJUST"]
    confidence: Literal["low", "medium", "high"]
    why: str
    changes_my_mind: str
    adjust: Optional[Adjust] = None
    escalate: bool = False
    regime: Literal["trend", "range", "wild", "transition"]
    detected_patterns: list[str]
    open_questions: list[str] = []

def img_block(png_b64: str) -> dict:
    return {"type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": png_b64}}

async def evaluate_signal(chart_d1_b64, chart_h4_b64, signal_meta, calendar_blind):
    # ---- TIER 0: mechanical prefilter (no model) --------------------------------
    hard_fail = tier0_check(signal_meta, calendar_blind)   # news/Friday/cluster/caps
    if hard_fail:
        return {"verdict": "SKIP", "tier": 0, "why": hard_fail}

    # ---- TIER 1: Sonnet 5 — PRIMARY decision-maker (thinking off, effort low) ----
    t1_resp = client.messages.parse(
        model="claude-sonnet-5",
        max_tokens=1024,
        thinking={"type": "disabled"},          # latency; Sonnet 5 defaults to adaptive
        output_config={"effort": "low"},        # no temperature/top_p — rejected on Sonnet 5
        system=[
            {"type": "text", "text": TIER1_SYSTEM_AND_CHECKLISTS,
             "cache_control": {"type": "ephemeral", "ttl": "1h"}},
        ],
        messages=[{"role": "user", "content": [
            img_block(chart_d1_b64), img_block(chart_h4_b64),
            {"type": "text", "text": render_context(signal_meta, calendar_blind)},
        ]}],
        output_format=Verdict,
    )
    t1 = t1_resp.parsed_output

    if not t1.escalate:                          # the common case — done in one call
        out = t1.model_dump() | {"tier": 1}
        log_jsonl(signal_meta, out, t1_resp.usage)
        return out

    notify_user(f"Escalating (draft: {t1.verdict}, {t1.confidence}) — "
                f"deep review of: {'; '.join(t1.open_questions)}")

    # ---- Retrieval: deterministic, in code — NARROW: only what the questions need
    excerpts = load_playbook_sections(
        strategy=signal_meta["strategy"],
        patterns=t1.detected_patterns,
        questions=t1.open_questions,
        budget_tokens=4000,                      # tight — Fable reviews, not re-derives
    )

    # ---- TIER 2: Fable 5 — rare, speed-tuned review of the draft ----------------
    with client.beta.messages.stream(
        model="claude-fable-5",
        max_tokens=8000,                        # bounded: verdict + short reasoning
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",                    # policy declines re-served automatically
        output_config={
            "effort": "medium",                 # speed lever: Fable low/medium is strong;
                                                # raise only if coach data shows misses
            "format": {"type": "json_schema", "schema": VERDICT_SCHEMA},
        },
        system=[
            {"type": "text", "text": TIER2_ROLE_PROMPT},
            {"type": "text", "text": MASTER_PLAYBOOK},
            {"type": "text", "text": COACH_LESSONS,
             "cache_control": {"type": "ephemeral", "ttl": "1h"}},
        ],
        messages=[{"role": "user", "content": [
            img_block(chart_d1_b64), img_block(chart_h4_b64),
            {"type": "text", "text":
                f"DRAFT VERDICT FROM TRIAGE (confirm or overturn):\n"
                f"{t1.model_dump_json()}\n\n"
                f"RESOLVE ONLY THESE QUESTIONS:\n"
                + "\n".join(f"- {q}" for q in t1.open_questions) + "\n\n"
                f"CONTEXT:\n{render_context(signal_meta, calendar_blind)}\n\n"
                f"PLAYBOOK EXCERPTS FOR THESE QUESTIONS:\n{excerpts}"},
        ]}],
    ) as stream:
        response = stream.get_final_message()

    if response.stop_reason == "refusal":       # whole fallback chain declined (rare)
        out = {"verdict": "SKIP", "tier": 2, "degraded": True,
               "why": f"deep review unavailable (model declined); unresolved draft was "
                      f"{t1.verdict} — treat as skip"}
        log_jsonl(signal_meta, out, response.usage)
        return out

    verdict = parse_json(next(b.text for b in response.content if b.type == "text"))
    verdict["tier"] = 2
    verdict["t1_draft"] = t1.model_dump()       # coach compares draft vs final
    log_jsonl(signal_meta, verdict, response.usage)
    return verdict
```

Implementation notes for the engineer:

- **Fable specifics:** never send a `thinking` parameter (always on; explicit configs
  400). The `fallbacks="default"` + `server-side-fallback-2026-07-01` beta re-runs
  safety-classifier declines on Anthropic's recommended substitute inside the same call —
  keep it on; chart analysis shouldn't trip classifiers, but the handler must exist.
  Fable requires the org to have **30-day data retention** (ZDR orgs get 400s on
  everything — check before debugging payloads). The speed levers on the Fable call, in
  order: `effort: "medium"` (Fable's low/medium often exceed prior models' top settings),
  the 8K `max_tokens` cap, the ~4K excerpt budget, and the review-not-reanalyze prompt
  framing. Even so, keep streaming + the up-front notification — a hard question can
  still take a couple of minutes, and that's the feature, not a bug.
- **Sonnet 5 specifics:** omitting `thinking` runs *adaptive* (it thinks); triage wants
  `{"type": "disabled"}` explicitly. Non-default `temperature`/`top_p`/`top_k` are
  rejected — don't send them.
- **Structured outputs:** unsupported JSON-schema constraints (min/max, lengths,
  recursion) are stripped by the SDK; keep schemas simple (enums + required +
  `additionalProperties: false`). First use of a new schema pays a one-time compilation
  cost; it's cached 24h after.
- **Logging:** every request/verdict/usage to JSONL. The coach correlates these with the
  journal (advisor-assisted vs solo decisions), and it's the audit trail for "is the
  assistant actually adding R?"

## 7. Ingestion workflow — autonomous by default

The trader's requirement: **drop books in a folder, run one command, done — no input from
them.** A `--review` flag exists for later, once the playbook is mature and they want to
gate merges manually. Safety in auto mode comes from reversibility and quarantine, not
from asking.

```
playbook/books_inbox/       # trader drops .pdf / .txt / .md here
playbook/books_ingested/    # processed originals move here (+ manifest of file hashes)
playbook/incoming/          # --review mode only: extractions awaiting approval
playbook/conflicts.md       # quarantined contradictions (never merged into rules)
playbook/ingest_reports/    # one markdown report per run
```

```
./pipeline/ingest_books.py            # full-auto: extract → merge → consolidate → report
./pipeline/ingest_books.py --review   # stop after extraction; park in incoming/
./pipeline/ingest_books.py --apply    # merge previously approved incoming/ items
```

Auto-mode steps per run:

1. **Snapshot first.** Git-commit (or timestamped copy of) `playbook/` before any write.
   Every run is fully revertible — this is what makes zero-input merging acceptable.
2. **Extract.** For each new inbox file (hash not in the manifest): chunk by chapter
   (Fable's 1M context needs no fine chunking), submit via the **Batches API** (50%
   price) with the §5 ingestion prompt + a structured-output schema mirroring the
   template. PDFs as native document blocks (≤32 MB / ≤600 pages) or via the Files API.
3. **Merge (code).** Append each extraction into its mapped per-strategy file
   (`smc.md`/`fib.md`/`ema.md`/`general.md`) under its tag headings, provenance intact.
4. **Consolidate (Fable).** One pass per touched file: integrate and dedupe the new
   entries against existing content. Hard instruction: book-derived entries may be
   merged/rewritten/deduped; entries sourced from the system's own specs, guide, or
   lessons may never be altered or deleted.
5. **Quarantine, don't block.** Entries flagged `conflicts_with_system` go to
   `playbook/conflicts.md` (with what they contradict), never into the rule body. The
   run report lists them — the trader reads it after the fact, or never.
6. **Protected files.** `master_playbook.md` and `lessons.md` are never written by
   ingestion in either mode. Promoting a book rule into the cached core stays a manual
   editorial act; `lessons.md` regenerates only from the guide's `<!-- COACH:* -->`
   blocks.
7. **Report.** `ingest_reports/<date>-<book>.md`: what was extracted, where it merged,
   dedup decisions, quarantined conflicts, token cost. Processed books move to
   `books_ingested/`; re-running the command skips them (idempotent by file hash).

`--review` runs the same extraction, then stops: extractions wait in `incoming/` for the
trader to edit/approve; `--apply` performs steps 3–4 (and 5–7) on the approved set.

v2 (only if the corpus outgrows the cacheable playbook): embed chunks with a third-party
embedding provider into Qdrant/Chroma, keyed by the same tags; retrieval remains
strategy-scoped first, semantic second.

## 8. Latency & cost budget (order of magnitude)

| Path | Model calls | Expected latency | Expected cost/decision |
|---|---|---|---|
| Tier 0 SKIP | 0 | <50 ms | $0 |
| **Tier 1 verdict (the vast majority)** | 1 (Sonnet 5, no thinking, ~400 out) | ~3–8 s | <$0.01 (intro pricing $2/$10 per MTok through 2026-08-31; images ~1600 tok each at 1568 px; playbook mostly cache-reads at ~0.1×) |
| Tier 2 escalation (target: single-digit %) | 2 (Tier 1 + Fable review) | ~30–90 s | ~$0.15–$0.50 (Fable $10/$50 per MTok at effort `medium`, 8K output cap, ~4K excerpt budget; cached playbook reads keep input cheap) |
| Book ingestion | batch | offline | ~50% of list via Batches |

**Quality guardrail for the fast path:** Tier 1 now owns approvals, which trades some
judgment depth for speed. The JSONL log is the check — the coach's baseline-join measures
Tier-1-decided outcomes separately from escalated ones. If Tier 1's bad-approve rate
drifts up, the knob is the escalation criteria (make Sonnet escalate more), then Tier 1
effort (`low`→`medium`), before anything structural. Conversely if Fable overturns
Sonnet's draft <~20% of the time, escalation can be tightened further.

If Tier 1 misses the 8s target in practice, the levers in order: smaller images, shorter
context line, trim the cached checklist block (cache reads are cheap but attention isn't
free), and only then consider `claude-haiku-4-5` for Tier 1 (accepts vision; expect a
quality drop on borderline-vs-clear-cut judgment — measure skip precision before/after).

## 8b. Auth & billing — run on the Claude subscription (trader's directive)

The trader wants this funded by their Claude subscription, **not** pay-per-token API
credits. Facts that shape the design:

- **The Messages API cannot bill to a subscription.** Direct `client.messages.create`
  calls with an API key are Console pay-per-token, full stop.
- **The Claude Agent SDK (and headless `claude -p`) CAN run under the subscription.**
  Mechanism: `claude setup-token` (requires Pro/Max/Team/Enterprise) mints a 1-year OAuth
  token → set it as `CLAUDE_CODE_OAUTH_TOKEN` in the service environment. All models
  (sonnet, fable, opus, haiku) are selectable under subscription auth; usage draws from
  the plan's 5-hour rolling + weekly windows.

**Therefore: build the transport as an interface with two implementations,** selected by
env/config:

| | `transport=agent_sdk` (default — subscription) | `transport=api` (fallback — credits) |
|---|---|---|
| Auth | `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token` | `ANTHROPIC_API_KEY` |
| Tier calls | `claude-agent-sdk` `query()` with `options.model` `"sonnet"` / `"fable"` | `anthropic` SDK per §6 |
| Images | pass PNG file paths in the prompt (harness `Read` renders them) | base64 blocks |
| Strict JSON | instruct + validate against the §5 schemas + one retry on parse failure (no `output_config.format` on this surface) | structured outputs per §6 |
| Caching | harness-managed (keep system prompt byte-stable anyway) | explicit `cache_control` per §3 |
| Refusals | harness handles | `stop_reason` + fallbacks per §6 |
| Ingestion | sequential sessions, run overnight (no Batches API) | Batches at 50% |

**Critical gotcha — silent billing flip:** in the auth precedence, `ANTHROPIC_API_KEY`
**outranks** subscription credentials, and in headless/non-interactive mode it is used
automatically with *no prompt*. If that var is present in the service environment, the
app silently starts billing the API account. The service launcher must `unset
ANTHROPIC_API_KEY` (unless `transport=api` is explicitly chosen), and startup should log
which auth path is active. Do not use `--bare` mode — it doesn't read
`CLAUDE_CODE_OAUTH_TOKEN`.

**Rate-limit reality (set expectations):** subscription windows are shared across ALL the
trader's Claude usage — interactive Claude Code sessions, this service, everything.
Decision traffic is trivial (a handful of Sonnet calls/day, rare short Fable reviews).
**Book ingestion is the heavy consumer** — a full book through Fable can eat a meaningful
share of a 5-hour window, so `ingest_books.py` under subscription auth should run
overnight / when the trader isn't working, process one book per run by default, and back
off gracefully on rate-limit errors rather than burning the window dry.

## 9. Build order

1. Tier 0 prefilter + blind calendar encoding + redaction cropper (depends on harness
   items C and F2).
2. Playbook builder script: distill `quick-reference.html` + advisor library into
   `master_playbook.md` / per-strategy files; `lessons.md` extractor.
3. Tier 1 triage endpoint + JSONL logging. Validate: replay the L0/L1 screenshots and
   check its SKIPs against the coach's graded outcomes.
4. Tier 2 deep path + streaming + refusal handling.
5. Simple front end (local web page or CLI watch mode) that shows: instant Tier 0/1
   answers, the "escalating (leaning X)" notice, streamed deep verdicts.
6. Ingestion pipeline (`ingest_books.py`, auto-merge default + `--review` flag). Note:
   this step has no dependency on the tiers — only on step 2's playbook files — so it can
   be built early or in parallel if the trader wants to start ingesting books sooner.

## 10. Out of scope / guardrails

- No order placement, no MT5 write-path integration — this is an advisor. (Phase 3's
  Telegram approval loop may later *display* these verdicts alongside alerts; design the
  JSONL log so that's a read-only consumer.)
- The assistant never proposes risk >1%, never proposes more than one level edit, and
  never overrides Tier 0 hard rules.
- Blindness is enforced in the input pipeline; prompts are the second line of defense,
  not the first.
