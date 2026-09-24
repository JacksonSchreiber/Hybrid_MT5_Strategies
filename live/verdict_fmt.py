"""verdict_fmt.py - parse the advisor's verdict block into fields the web app can render (coach 2026-09-24).

The guide fixes the shape: SUMMARY / VERDICT / Checks - Start Here / Checks - <strategy> steps / Quality / Why /
Changes my mind / Notes. This module is deliberately forgiving - a model that drifts from the format must degrade to
"render what parsed, raw text under more", never to a blank card - and it reports what it could not find so the
runner can log which model is drifting.
"""
from __future__ import annotations
import re

GLYPH = {"✓": "ok", "✔": "ok", "v": "ok", "x": "no", "✗": "no", "✘": "no", "×": "no", "?": "maybe", "-": "na"}
VERDICTS = ("TAKE (DEFAULT)", "TAKE", "SKIP", "ADJUST", "WAIT")
_LABEL = re.compile(r"^(SUMMARY|VERDICT|Quality|Why|Changes my mind|Notes)\s*:", re.I)
_CHECKS = re.compile(r"^Checks\s*[—–-]\s*(.+?)\s*:\s*(.*)$", re.I)
_STEP = re.compile(r"(\d{1,2})\s*([✓✔✗✘×?xXvV-])")            # v/V: some transports mangle the tick glyph
_CHIP = re.compile(r"(regime|news|correlation)\s*([✓✔✗✘×?xXvV-])", re.I)


def _norm(g: str) -> str: return GLYPH.get(g, GLYPH.get(g.lower(), "maybe"))


def verdict_of(line: str) -> str | None:
    u = " ".join(line.upper().split())
    for v in VERDICTS:
        if u.startswith(v) or f" {v}" in u[:60]: return "TAKE (default)" if v == "TAKE (DEFAULT)" else v
    return None


def parse(text: str | None) -> dict:
    """-> {summary, verdict, confidence, mode, start[], strategy, steps[], quality, quality_why, why,
           changes, notes, missing[], synthesised}"""
    out: dict = {"summary": None, "verdict": None, "confidence": None, "mode": None, "start": [], "strategy": None,
                 "steps": [], "quality": None, "quality_why": None, "why": "", "changes": "", "notes": "",
                 "missing": [], "synthesised": False, "raw": text or ""}
    if not text: out["missing"] = ["everything"]; return out
    cur, buf = None, []

    def flush():
        if cur and buf:
            v = " ".join(x.strip() for x in buf if x.strip()).strip()
            if cur == "why": out["why"] = v
            elif cur == "changes": out["changes"] = v
            elif cur == "notes": out["notes"] = (out["notes"] + "\n" + v).strip()
            elif cur == "summary": out["summary"] = v
    for ln in (text or "").splitlines():
        m = _LABEL.match(ln.strip())
        ck = _CHECKS.match(ln.strip())
        if m:
            flush(); buf = []
            lab = m.group(1).lower(); rest = ln.split(":", 1)[1].strip()
            if lab == "summary": cur = "summary"; buf = [rest]
            elif lab == "verdict":
                cur = None
                out["verdict"] = verdict_of(rest)
                c = re.search(r"confidence\s*:\s*(low|medium|high)", rest, re.I)
                out["confidence"] = c.group(1).lower() if c else None
                out["mode"] = "deep" if re.search(r"\bdeep\b", rest, re.I) else ("quick" if re.search(r"\bquick\b", rest, re.I) else None)
            elif lab == "quality":
                cur = None
                q = re.match(r"\s*([ABC-])\s*[—–-]?\s*(.*)$", rest)
                if q: out["quality"], out["quality_why"] = q.group(1), q.group(2).strip()
            elif lab == "why": cur = "why"; buf = [rest]
            elif lab == "changes my mind": cur = "changes"; buf = [rest]
            elif lab == "notes": cur = "notes"; buf = [rest]
        elif ck:
            flush(); buf = []; cur = None
            head, body = ck.group(1), ck.group(2)
            if re.search(r"start\s*here", head, re.I):
                out["start"] = [(n.lower(), _norm(g)) for n, g in _CHIP.findall(body)]
            else:
                out["strategy"] = re.sub(r"\s*steps\s*$", "", head, flags=re.I).strip() or None
                out["steps"] = [(int(n), _norm(g)) for n, g in _STEP.findall(body)]
        elif cur: buf.append(ln)
        elif ln.strip() and out["verdict"] and not out["why"]: pass        # stray prose before a label: ignored
    flush()
    if not out["verdict"]:                                                 # last resort: a bare verdict word anywhere
        for ln in (text or "").splitlines():
            v = verdict_of(ln)
            if v: out["verdict"] = v; break
    for k in ("summary", "verdict", "confidence"):
        if not out[k]: out["missing"].append(k)
    if not out["start"]: out["missing"].append("start-here checks")
    if not out["steps"]: out["missing"].append("strategy steps")
    for k in ("why", "changes"):
        if not out[k]: out["missing"].append(k)
    if not out["summary"] and (out["verdict"] or out["why"]):              # synthesise, and say so
        first = re.split(r"(?<=[.!?])\s", out["why"].strip())[0] if out["why"] else ""
        out["summary"] = " — ".join(x for x in (out["verdict"], first) if x)[:240]
        out["synthesised"] = True
    return out


def decisive_step(p: dict) -> int | None:
    """the step the Why line names, else the first failing step - what the card highlights."""
    m = re.search(r"\bstep\s*(\d{1,2})\b", (p.get("why") or "") + " " + (p.get("summary") or ""), re.I)
    if m:
        n = int(m.group(1))
        if any(s == n for s, _ in p.get("steps") or []): return n
    for n, g in p.get("steps") or []:
        if g == "no": return n
    return None

# What each numbered check actually means, so nothing the trader reads is a bare number (trader ruling 2026-09-24).
# TrendCont is the one checklist the guide numbers explicitly; the others name their checks in prose, so an unknown
# number is dropped from the sentence rather than shown.
STEP_NAMES = {
    "trendcont": {1: "the D1 staircase", 2: "the pullback's character", 3: "depth against the EMA20",
                  4: "the resume candle", 5: "room to the first target", 6: "events, correlation and late-Friday"},
}


def step_name(strategy: str | None, n: int) -> str | None:
    return STEP_NAMES.get((strategy or "").strip().lower().replace(" ", ""), {}).get(n)


def plainify(text: str | None, strategy: str | None) -> str:
    """Turn 'Step 5 fails' into 'room to the first target fails' - and where the checklist is not numbered in the
    guide, drop the reference rather than print a number the trader has to look up."""
    if not text: return ""
    def sub(m):
        paren = (m.group(4) or "").strip()                    # "step 4 (trigger candle)" already names itself
        return paren or step_name(strategy, int(m.group(2))) or ""
    out = re.sub(r"\b(step|item)\s*#?\s*(\d{1,2})\b(\s*\(([^)]{3,40})\))?", sub, text, flags=re.I)
    out = re.sub(r"\(\s*\)", "", out)                       # an emptied bracket
    out = re.sub(r"\s{2,}", " ", out).strip()
    out = re.sub(r"^[,;:\s-]+", "", out)
    return out[:1].upper() + out[1:] if out[:1].islower() and not out[:4].isupper() else out
