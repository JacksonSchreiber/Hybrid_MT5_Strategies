"""app.py — Tier 0 → Tier 1 evaluation for the blind advisory service.

- Tier 0 (code) hard-fails to SKIP with a named rule, or produces the blind
  calendar encoding (pipeline/tier0.py).
- Tier 1 (Sonnet) is the primary decision-maker: a cached, byte-stable system
  prefix (role prompt + master playbook + lessons) + the blind images + a
  blindness-safe context line → a TAKE/SKIP/ADJUST verdict, parsed and validated,
  with one reformat retry, failing closed to SKIP if it still won't parse.
- Every decision is logged to JSONL for the coach (the log carries the real
  symbol/time in an `audit` block — that never goes to the model).

Tier-2 escalation is stubbed here (definition of done is Tier 0 + Tier 1).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from pipeline import tier0
from pipeline.assistant.config import Config, PLAYBOOK_DIR, PROMPTS_DIR

VALID_VERDICT = {"TAKE", "SKIP", "ADJUST"}
VALID_CONF = {"low", "medium", "high"}


# ---- cached system prefix (role + playbook + lessons) -----------------------
def build_system(tier: str) -> str:
    """The frozen, byte-stable system text — identical across calls so the
    server-side prompt cache actually hits on repeat."""
    role_file = "tier1_system.md" if tier == "tier1" else "tier2_system.md"
    role = (PROMPTS_DIR / role_file).read_text(encoding="utf-8")
    master = (PLAYBOOK_DIR / "master_playbook.md").read_text(encoding="utf-8")
    lessons = (PLAYBOOK_DIR / "lessons.md").read_text(encoding="utf-8")
    return (f"{role}\n\n"
            f"===== MASTER PLAYBOOK (checklists / regime / edits) =====\n{master}\n\n"
            f"===== LESSONS — highest authority, from real graded results =====\n{lessons}\n")


# ---- blindness-safe per-request context -------------------------------------
def render_context(meta: dict, cal: dict) -> str:
    d = "BUY" if meta.get("direction", 0) > 0 else "SELL"
    return "\n".join([
        "SIGNAL CONTEXT — blind (no symbol, no date, no event names):",
        f"- Strategy fired: {meta.get('strategy')}",
        f"- Direction: {d}",
        f"- Session / day-of-week: {meta.get('session', '?')} / {meta.get('day_of_week', '?')}",
        f"- Proposed levels (chart-visible prices): "
        f"entry {meta.get('entry')}, SL {meta.get('sl')}, "
        f"TP1 {meta.get('tp1')}, TP2 {meta.get('tp2')}",
        f"- Risk geometry: SL is {meta.get('sl_r', '?')}R from entry; "
        f"TP1 {meta.get('tp1_r', '?')}R; TP2 {meta.get('tp2_r', '?')}R "
        "(the detector already sized to 1% and cleared the strategy's R:R floor).",
        f"- Correlated exposure: {meta.get('cluster_exposure', 'none reported')}",
        "- Calendar (blind encoding): "
        f"high_impact_ahead={cal['high_impact_ahead']}, hours_until={cal['hours_until']}, "
        f"affects={cal['affects']}, recent_event_bias={cal['recent_event_bias']}",
        "",
        "The two images are the D1 (context) and H4 (setup) charts. Judge from the "
        "visible chart + the playbook only. Respond with ONLY the JSON verdict "
        "object described in your instructions — no prose, no code fence.",
    ])


# ---- verdict parsing --------------------------------------------------------
def extract_json(text: str):
    s = text.find("{")
    if s < 0:
        return None
    depth = 0
    for i in range(s, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[s:i + 1])
                except Exception:
                    return None
    return None


def validate_verdict(d) -> str | None:
    if not isinstance(d, dict):
        return "not a JSON object"
    if d.get("verdict") not in VALID_VERDICT:
        return f"verdict must be one of {sorted(VALID_VERDICT)}"
    if d.get("confidence") not in VALID_CONF:
        return "confidence must be low|medium|high"
    why = d.get("why", "")
    if not isinstance(why, str) or not why.strip():
        return "missing 'why'"
    if d.get("verdict") == "ADJUST" and not isinstance(d.get("adjust"), dict):
        return "ADJUST verdict needs an 'adjust' object"
    return None


# ---- logging ----------------------------------------------------------------
def log_jsonl(cfg: Config, record: dict):
    cfg.log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cfg.log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def _log(cfg, audit, out, usage, transport):
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "transport": transport,
        "tier": out.get("tier"),
        "audit": audit,                     # real symbol/time — NEVER sent to a model
        "verdict": {k: v for k, v in out.items() if not k.startswith("_")},
        "usage": usage,
    }
    log_jsonl(cfg, rec)


# ---- Tier 0 → Tier 1 --------------------------------------------------------
def evaluate(*, signal: tier0.Signal, audit: dict, images_b64: list[str],
             meta: dict, cfg: Config, transport) -> dict:
    # ---- Tier 0: mechanical prefilter (no model) ----
    t0 = tier0.evaluate(signal, horizon_h=cfg.news_horizon_h)
    if t0.skip:
        out = {"verdict": "SKIP", "tier": 0, "confidence": "high",
               "why": t0.reason, "calendar_blind": t0.calendar_blind}
        _log(cfg, audit, out, None, cfg.transport)
        return out

    # ---- Tier 1: Sonnet, primary decision-maker ----
    system = build_system("tier1")
    user_text = render_context(meta, t0.calendar_blind)

    reply = transport.judge(system=system, images_b64=images_b64,
                            user_text=user_text, tier="tier1")
    d = extract_json(reply.text)
    err = validate_verdict(d) if d is not None else "no JSON in reply"

    if err:  # one reformat retry
        reply2 = transport.judge(
            system=system, images_b64=images_b64,
            user_text=user_text + "\n\nYour previous reply did not parse. Return "
                      "ONLY the JSON verdict object — no prose, no code fence.",
            tier="tier1")
        d2 = extract_json(reply2.text)
        err2 = validate_verdict(d2) if d2 is not None else "no JSON in reply"
        if not err2:
            reply, d, err = reply2, d2, None

    if err:  # fail closed
        out = {"verdict": "SKIP", "tier": 1, "degraded": True, "confidence": "low",
               "why": f"could not parse a valid verdict ({err}) — failing closed to SKIP",
               "raw": reply.text[:400]}
    else:
        out = dict(d)
        out["tier"] = 1

    out["_usage"] = {"cache_read": reply.cache_read,
                     "cache_creation": reply.cache_creation,
                     "input": reply.usage.get("input_tokens"),
                     "output": reply.usage.get("output_tokens")}
    if reply.error:
        out["_transport_error"] = reply.error
    _log(cfg, audit, out, reply.usage, cfg.transport)
    return out
