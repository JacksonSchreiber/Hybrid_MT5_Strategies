#!/usr/bin/env python3
"""build_playbook.py — assemble the advisory app's playbook/ store from the
tested source material (Build step 2 of docs/assistant-app-implementation.md).

Turns the human-facing training assets into the machine-facing playbook the
2-tier assistant loads:

  quick-reference.html  ──►  master_playbook.md   (cache-resident core:
                                                   regime table, 3 strategy
                                                   checklists, edit rules,
                                                   candle tells)
                        ──►  lessons.md            (the Lessons tab, parsed at
                                                   the <!-- COACH:* --> anchors;
                                                   HIGHEST authority)
                        ──►  smc.md / fib.md /     (per-strategy deep sections:
                             ema.md                 the strategy tab + mapped
                                                    library material, tagged)
                        ──►  general.md            (cross-strategy: Rules tab +
                                                   general library material)
                        ──►  clusters.json         (correlated-families table +
                                                   risk caps + news horizon —
                                                   structured DATA for Tier 0)
  advisor/library/*.md  ──►  folded into the per-strategy / general files

AUTHORITY (highest first, per the spec's §9 hierarchy): lessons.md >
master_playbook.md (guide/specs) > per-strategy library > ingested books.
lessons.md and master_playbook.md are the protected core — ingestion never
machine-writes them; only this builder (re)generates them from the sources.

stdlib only (html.parser, json, argparse, pathlib). No network, no API key.

The OneDrive source path below is NOT in the git repo and NOT under version
control (deliberately — small files, Windows/phone sync). It is a single
fail-loud constant: if the training folder moves, this aborts with the
expected path rather than emitting a silent empty playbook.
"""
import argparse
import json
import sys
from html.parser import HTMLParser
from pathlib import Path

# --- source (OneDrive, out-of-repo) — fail loud if it moves --------------------
TRAINING_DIR = Path(
    "/mnt/c/Users/jacks/OneDrive/Trading/hybrid_project/training"
)
QUICK_REF = TRAINING_DIR / "quick-reference.html"
LIBRARY_DIR = TRAINING_DIR / "advisor" / "library"

# --- output (in-repo, git-tracked so ingestion snapshots are revertible) -------
PLAYBOOK_DIR = Path(__file__).resolve().parent.parent / "playbook"

# Which distilled library file feeds which playbook file. Files not listed
# (INDEX.md, full-texts/) are left as deep source, not inlined into the cache.
LIBRARY_MAP = {
    "smc.md": ["smc-ict-concepts.md", "price-action-structure.md"],
    "fib.md": ["fibonacci-retracement.md"],
    "ema.md": ["mean-reversion-and-regime.md"],
    "general.md": [
        "chart-reading-protocol.md",
        "multi-timeframe-analysis.md",
        "candlestick-patterns.md",
        "risk-and-trade-management.md",
    ],
}

# Risk caps + the news horizon are stated in prose in the guide (Start Here
# step 2, Rules "Correlated families" + "Reading the event lines"). Tier 0 needs
# them as numbers, so they are encoded here with their guide provenance. Keep in
# sync with quick-reference.html if the guide's caps ever change.
RISK_RULES = {
    "news_horizon_hours": 12,          # "High-impact event within ~12h → SKIP"
    "ftmo_daily_loss_pct": 5.0,        # $1,250 on $25k
    "ftmo_total_loss_pct": 10.0,       # $2,500 on $25k
    "system_open_risk_cap_pct": 3.0,   # "≤3% open+armed at once"
    "per_idea_cap_pct": 2.0,           # "one family = one idea = max 2%"
    "source": "quick-reference.html (Start Here step 2; Rules — Correlated families)",
}


# ============================================================================
#  Minimal HTML → Markdown (drops svg/figure/script/style; keeps the text that
#  carries the decision logic: headings, paragraphs, steps, chips, tables,
#  details, verdict/stripe callouts, lesson lists).
# ============================================================================
class _MdParser(HTMLParser):
    SKIP = {"script", "style", "svg", "figure", "figcaption"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []          # list of markdown line-chunks
        self.skip_depth = 0    # >0 while inside a SKIP subtree
        self.tagstack = []
        self.classes = []      # class attr per open tag (parallel to tagstack)
        # table state
        self.in_table = False
        self.in_row = False
        self.in_cell = False
        self.row = []
        self.cell = []
        self.rows = []
        self.header_done = False
        # inline text buffer for block elements
        self.buf = []
        # step numbering: the .step .q row holds <span class="n">N</span> +
        # <b>question</b>; we accumulate both into one bold "Step N. question"
        # line, emitted when the .q div closes.
        self.in_step_n = False
        self.in_q = False

    # -- helpers ---------------------------------------------------------------
    def _cls(self, attrs):
        d = dict(attrs)
        return d.get("class", "")

    def _flush(self):
        text = "".join(self.buf).strip()
        self.buf = []
        return " ".join(text.split())

    def _emit(self, s):
        self.out.append(s)

    # -- tag handlers ----------------------------------------------------------
    def handle_starttag(self, tag, attrs):
        cls = self._cls(attrs)
        if tag in self.SKIP:
            self.skip_depth += 1
        self.tagstack.append(tag)
        self.classes.append(cls)
        if self.skip_depth:
            return

        if tag == "div" and "q" in cls.split():
            self.in_q = True
            self.buf = []
        elif tag in ("h2", "h3", "h4"):
            self._emit("\n" + self._flush())  # flush any pending inline
            self.buf = []
        elif tag == "p":
            self.buf = []
        elif tag in ("b", "strong"):
            if not self.in_q:            # inside a step question, whole line is bold
                self.buf.append("**")
        elif tag in ("i", "em"):
            if not self.in_q:
                self.buf.append("_")
        elif tag == "li":
            self.buf = []
        elif tag == "summary":
            self.buf = []
        elif tag == "span" and "chip" in cls:
            # branch chip: prefix with its polarity
            pol = ("YES" if "yes" in cls else "NO" if "no" in cls
                   else "WARN" if "warn" in cls else "")
            self.buf.append(f"[{pol}] " if pol else "")
        elif tag == "span" and "n" in cls.split():
            self.in_step_n = True
        elif tag == "table":
            self.in_table = True
            self.rows = []
            self.header_done = False
        elif tag == "tr":
            self.in_row = True
            self.row = []
        elif tag in ("td", "th"):
            self.in_cell = True
            self.cell = []

    def handle_startendtag(self, tag, attrs):
        # self-closing (e.g. <line/>, <rect/>) inside svg — ignored via skip
        pass

    def handle_endtag(self, tag):
        cls = self.classes.pop() if self.classes else ""
        if self.tagstack:
            self.tagstack.pop()
        if tag in self.SKIP:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth:
            return

        if tag == "div" and "q" in cls.split():
            self.in_q = False
            t = self._flush()
            if t:
                self._emit("\n**" + t + "**")
        elif tag == "div":
            # flush any inline text a non-<p> div accumulated — chiefly the
            # YES/NO/WARN branch chips (the explicit continue/SKIP conditions),
            # which otherwise leak into the next block.
            t = self._flush()
            if t:
                self._emit(t)
        elif tag in ("h2", "h3", "h4"):
            level = {"h2": "## ", "h3": "### ", "h4": "#### "}[tag]
            self._emit("\n" + level + self._flush() + "\n")
        elif tag == "p":
            t = self._flush()
            if t:
                self._emit(t + "\n")
        elif tag in ("b", "strong"):
            if not self.in_q:
                self.buf.append("**")
        elif tag in ("i", "em"):
            if not self.in_q:
                self.buf.append("_")
        elif tag == "span" and "n" in cls.split():
            self.in_step_n = False
        elif tag == "li":
            t = self._flush()
            if t:
                self._emit("- " + t)
        elif tag == "summary":
            t = self._flush()
            if t:
                self._emit("\n**" + t + "**\n")
        elif tag in ("td", "th"):
            self.in_cell = False
            self.row.append(self._flush())
        elif tag == "tr":
            self.in_row = False
            if self.row:
                self.rows.append(self.row)
            self.row = []
        elif tag == "table":
            self.in_table = False
            self._emit(self._render_table(self.rows) + "\n")
            self.rows = []

    def handle_data(self, data):
        if self.skip_depth:
            return
        text = data
        if not text.strip():
            # keep a single space so inline words don't glue together
            if self.buf and not self.buf[-1].endswith(" "):
                self.buf.append(" ")
            return
        if self.in_step_n:
            # the number circle — prefix the step; the question <b> that follows
            # accumulates into the same buffer, emitted as one line at div.q close
            self.buf.append("Step " + text.strip() + ". ")
            return
        self.buf.append(text)

    # -- table rendering -------------------------------------------------------
    @staticmethod
    def _render_table(rows):
        if not rows:
            return ""
        ncol = max(len(r) for r in rows)
        rows = [r + [""] * (ncol - len(r)) for r in rows]
        head = rows[0]
        body = rows[1:]
        def esc(c):
            return c.replace("|", "\\|")
        lines = ["| " + " | ".join(esc(c) for c in head) + " |",
                 "|" + "|".join(["---"] * ncol) + "|"]
        for r in body:
            lines.append("| " + " | ".join(esc(c) for c in r) + " |")
        return "\n".join(lines)


def _section_html(full_html, tab_id):
    """Extract the inner HTML of <section ... id="tab-<tab_id>"> ... </section>.
    The guide's sections don't nest, so a simple open→matching-close scan works."""
    marker = f'id="tab-{tab_id}"'
    i = full_html.find(marker)
    if i < 0:
        raise ValueError(f"tab '{tab_id}' not found in {QUICK_REF}")
    start = full_html.find(">", i) + 1
    # find the section close by counting <section ...> / </section>
    depth = 1
    j = start
    while depth:
        nxt_open = full_html.find("<section", j)
        nxt_close = full_html.find("</section>", j)
        if nxt_close < 0:
            raise ValueError(f"unterminated section for tab '{tab_id}'")
        if 0 <= nxt_open < nxt_close:
            depth += 1
            j = nxt_open + 8
        else:
            depth -= 1
            j = nxt_close + 10
    return full_html[start:full_html.rfind("</section>", 0, j)]


def html_to_md(section_html):
    p = _MdParser()
    p.feed(section_html)
    # collapse >2 blank lines
    text = "\n".join(x.rstrip() for x in p.out)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip() + "\n"


# ============================================================================
#  Lessons — parse the four .lessons blocks by their <!-- COACH:* --> anchors.
# ============================================================================
def parse_lessons(full_html):
    """Return {section_name: [lesson_text, ...]} for each COACH-anchored block.
    Each .lessons <div> (h4 title + <ul><li> items) sits immediately ABOVE its
    <!-- COACH:NAME --> anchor. We split on the anchors and take the preceding
    .lessons block, so the mapping is anchored, not positional."""
    anchors = []
    idx = 0
    while True:
        a = full_html.find("<!-- COACH:", idx)
        if a < 0:
            break
        name_start = a + len("<!-- COACH:")
        name_end = full_html.find(" ", name_start)
        anchors.append((a, full_html[name_start:name_end].strip()))
        idx = a + 1

    result = {}
    prev = 0
    for pos, name in anchors:
        chunk = full_html[prev:pos]
        # last .lessons div in this chunk
        li = chunk.rfind('class="lessons"')
        if li >= 0:
            block = chunk[li:]
            sub = _MdParser()
            sub.feed(block)
            items = [ln[2:].strip() for ln in sub.out if ln.startswith("- ")]
            items = [it for it in items if it and it.lower() != "(none yet)"]
            result[name] = items
        prev = pos
    return anchors, result


# ============================================================================
#  Correlated families table → clusters.json (structured data for Tier 0)
# ============================================================================
def parse_clusters(rules_html):
    """Find the 'Correlated families' table and return its rows as data."""
    # locate the heading, then the first table after it
    h = rules_html.find("Correlated families")
    if h < 0:
        raise ValueError("'Correlated families' table not found")
    tstart = rules_html.find("<table", h)
    tend = rules_html.find("</table>", tstart) + len("</table>")
    rows = _RawRows()
    rows.feed(rules_html[tstart:tend])
    families = []
    for r in rows.rows[1:]:  # skip header
        if len(r) >= 3:
            families.append({"family": r[0], "members": r[1], "trap": r[2]})
    return families


class _RawRows(HTMLParser):
    """Tiny table-row extractor (text only)."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.row = []
        self.cell = []
        self.in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th"):
            self.in_cell = True
            self.cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self.in_cell = False
            self.row.append(" ".join("".join(self.cell).split()))
        elif tag == "tr":
            if self.row:
                self.rows.append(self.row)

    def handle_data(self, data):
        if self.in_cell:
            self.cell.append(data)


# ============================================================================
#  Library
# ============================================================================
def read_library(names):
    out = []
    for n in names:
        f = LIBRARY_DIR / n
        if f.exists():
            out.append((n, f.read_text(encoding="utf-8", errors="replace")))
        else:
            out.append((n, f"_(library file {n} not found)_\n"))
    return out


def library_full_texts():
    ft = LIBRARY_DIR / "full-texts"
    if not ft.is_dir():
        return []
    return sorted(p.name for p in ft.glob("*") if p.is_file())


# ============================================================================
#  Build
# ============================================================================
BANNER = ("<!-- GENERATED by pipeline/build_playbook.py from "
          "training/quick-reference.html + advisor/library/. Do not hand-edit; "
          "re-run the builder. -->\n\n")


def build(verbose=True):
    if not QUICK_REF.exists():
        sys.exit(
            f"ERROR: quick-reference.html not found at:\n  {QUICK_REF}\n"
            f"The training folder moved or OneDrive isn't mounted. Fix "
            f"TRAINING_DIR in {__file__} or restore the path."
        )
    html = QUICK_REF.read_text(encoding="utf-8", errors="replace")
    PLAYBOOK_DIR.mkdir(parents=True, exist_ok=True)

    start_md = html_to_md(_section_html(html, "start"))
    smc_md = html_to_md(_section_html(html, "smc"))
    fib_md = html_to_md(_section_html(html, "fib"))
    ema_md = html_to_md(_section_html(html, "ema"))
    rules_html = _section_html(html, "rules")
    rules_md = html_to_md(rules_html)

    anchors, lessons = parse_lessons(html)
    clusters = parse_clusters(rules_html)
    full_texts = library_full_texts()

    written = []

    # --- master_playbook.md (cache-resident core) ---------------------------
    master = [BANNER,
              "# Master Playbook — cache-resident core\n",
              "The Tier-1 assistant loads this every request. Authority: the "
              "**Lessons** (`lessons.md`) outrank everything here; this guide "
              "and the strategy specs outrank the distilled library; the "
              "library outranks any ingested book. Verdicts are "
              "**TAKE / SKIP / ADJUST** (ADJUST = exactly one level moved). "
              "R:R floors (2.0 SMC/Fib, 1.3 blended EMA) and 1% sizing are "
              "enforced by the detector — never re-derive them.\n",
              "\n## Start Here — regime + news + correlation\n",
              start_md,
              "\n## SMC — Liquidity Sweep + Structure Shift (checklist)\n",
              smc_md,
              "\n## Deep Fib — Buy the Trend at a Discount (checklist)\n",
              fib_md,
              "\n## EMA20 Fade — the Rubber-Band Trade (checklist)\n",
              ema_md,
              "\n## Edit rules + candle tells\n",
              rules_md]
    (PLAYBOOK_DIR / "master_playbook.md").write_text("".join(master), encoding="utf-8")
    written.append("master_playbook.md")

    # --- lessons.md (highest authority) -------------------------------------
    order = ["SMC", "FIB", "EMA", "PROCESS"]
    titles = {"SMC": "SMC", "FIB": "Deep Fib", "EMA": "EMA20 Reversion",
              "PROCESS": "Process / psychology"}
    lm = [BANNER,
          "# Lessons — written by real results (HIGHEST authority)\n",
          "Extracted from the coach's `<!-- COACH:* -->` anchored blocks in "
          "quick-reference.html. These are graded outcomes; they override the "
          "checklists when they conflict. Newest lessons are the most binding.\n"]
    total_lessons = 0
    for key in order:
        lm.append(f"\n## {titles.get(key, key)}\n")
        items = lessons.get(key, [])
        if not items:
            lm.append("_(none yet)_\n")
        for it in items:
            lm.append(f"- {it}\n")
            total_lessons += 1
    (PLAYBOOK_DIR / "lessons.md").write_text("".join(lm), encoding="utf-8")
    written.append("lessons.md")

    # --- per-strategy files -------------------------------------------------
    strat_src = {"smc.md": ("SMC — deep section", smc_md),
                 "fib.md": ("Deep Fib — deep section", fib_md),
                 "ema.md": ("EMA20 Fade — deep section", ema_md)}
    for fname, (title, checklist_md) in strat_src.items():
        parts = [BANNER, f"# {title}\n",
                 "Strategy checklist (from the guide), then the distilled "
                 "library material for this strategy, then ingested book "
                 "material (appended by the ingestion pipeline under its tags).\n",
                 "\n## Checklist\n", checklist_md,
                 "\n## Library\n"]
        for name, body in read_library(LIBRARY_MAP.get(fname, [])):
            parts.append(f"\n<!-- library:{name} -->\n")
            parts.append(body if body.endswith("\n") else body + "\n")
        parts.append("\n## Ingested (book material)\n"
                     "<!-- ingestion appends tagged entries below this line -->\n")
        (PLAYBOOK_DIR / fname).write_text("".join(parts), encoding="utf-8")
        written.append(fname)

    # --- general.md ---------------------------------------------------------
    gparts = [BANNER, "# General — cross-strategy material\n",
              "\n## Rules & Edits (from the guide)\n", rules_md,
              "\n## Library\n"]
    for name, body in read_library(LIBRARY_MAP.get("general.md", [])):
        gparts.append(f"\n<!-- library:{name} -->\n")
        gparts.append(body if body.endswith("\n") else body + "\n")
    if full_texts:
        gparts.append("\n## Deep source (full texts, not inlined)\n"
                      "Open full-texts available to the deep tier at "
                      "`training/advisor/library/full-texts/`:\n")
        for n in full_texts:
            gparts.append(f"- {n}\n")
    gparts.append("\n## Ingested (book material)\n"
                  "<!-- ingestion appends tagged entries below this line -->\n")
    (PLAYBOOK_DIR / "general.md").write_text("".join(gparts), encoding="utf-8")
    written.append("general.md")

    # --- clusters.json (Tier 0 data) ----------------------------------------
    data = {"correlated_families": clusters, "risk_rules": RISK_RULES}
    (PLAYBOOK_DIR / "clusters.json").write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8")
    written.append("clusters.json")

    # --- report -------------------------------------------------------------
    if verbose:
        print(f"playbook written to {PLAYBOOK_DIR}")
        for w in written:
            size = (PLAYBOOK_DIR / w).stat().st_size
            print(f"  {w:22s} {size:6d} bytes")
        print(f"\nCOACH anchors found: {len(anchors)}  "
              f"({', '.join(n for _, n in anchors)})")
        print(f"lessons extracted:  {total_lessons}  "
              f"({', '.join(f'{k}:{len(lessons.get(k, []))}' for k in order)})")
        print(f"correlated families: {len(clusters)}  "
              f"({', '.join(c['family'] for c in clusters)})")
        print(f"full-texts referenced: {len(full_texts)}")
    return {"anchors": len(anchors), "lessons": total_lessons,
            "clusters": len(clusters)}


def main():
    ap = argparse.ArgumentParser(description="Build the advisory app's playbook/ store.")
    ap.add_argument("--quiet", action="store_true", help="suppress the report")
    a = ap.parse_args()
    build(verbose=not a.quiet)


if __name__ == "__main__":
    main()
