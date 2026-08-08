#!/usr/bin/env python3
"""ingest_books.py — autonomous playbook ingestion (docs/assistant-app-implementation.md §7).

Drop trading books in playbook/books_inbox/, run one command, done — no input
needed. Safety comes from reversibility + quarantine, not from asking:

  playbook/books_inbox/      drop .pdf / .txt / .md here
  playbook/books_ingested/   processed originals move here (+ manifest of hashes)
  playbook/incoming/         --review: extractions awaiting approval
  playbook/conflicts.md      quarantined contradictions (never merged into rules)
  playbook/ingest_reports/   one markdown report per run
  playbook/.snapshots/<ts>/  pre-run copy of the mutable playbook files (revertible)

    ./pipeline/ingest_books.py            # full-auto: extract → merge → report
    ./pipeline/ingest_books.py --review   # extract only; park in incoming/
    ./pipeline/ingest_books.py --apply    # merge previously-approved incoming/ items

Per run: snapshot first; for each NEW inbox file (hash not in the manifest),
extract mechanical rules with Fable (the §5 ingestion prompt + a strict schema),
merge each entry into its mapped per-strategy file (smc/fib/ema/general.md) under
the "Ingested" section with provenance, code-dedupe by (name, source); entries
flagged conflicts_with_system go to conflicts.md, never the rule body. A run
report is written and the processed book is moved to books_ingested/ (idempotent
by file hash). master_playbook.md and lessons.md are NEVER machine-written.

Runs on the subscription transport (no Batches API) — one Fable session per book,
sequential; meant to run overnight (§8b). stdlib + pypdf (PDFs) + the assistant
transport.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from pipeline.assistant.config import (PLAYBOOK_DIR, PROMPTS_DIR, MODEL_ALIAS,  # noqa: E402
                                       MODEL_API_ID, load_config, setup_auth)
from pipeline.assistant.transport import make_transport                          # noqa: E402
from pipeline.assistant import app                                               # noqa: E402

INBOX = PLAYBOOK_DIR / "books_inbox"
INGESTED = PLAYBOOK_DIR / "books_ingested"
INCOMING = PLAYBOOK_DIR / "incoming"
SNAPSHOTS = PLAYBOOK_DIR / ".snapshots"
REPORTS = PLAYBOOK_DIR / "ingest_reports"
CONFLICTS = PLAYBOOK_DIR / "conflicts.md"
MANIFEST = INGESTED / "manifest.json"

STRAT_FILE = {"smc": "smc.md", "fib": "fib.md", "ema": "ema.md", "general": "general.md"}
MUTABLE = ["smc.md", "fib.md", "ema.md", "general.md"]   # never master_playbook / lessons
INGEST_ANCHOR = "## Ingested (book material)"


# --------------------------------------------------------------------------- #
def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_manifest() -> dict:
    if MANIFEST.exists():
        try:
            return json.loads(MANIFEST.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def write_manifest(m: dict):
    INGESTED.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(m, indent=2) + "\n", encoding="utf-8")


def book_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in (".txt", ".md"):
        return path.read_text(encoding="utf-8", errors="replace")
    if ext == ".pdf":
        try:
            import pypdf
        except ImportError:
            raise RuntimeError("pypdf not installed — cannot read PDFs "
                               "(pip install --user pypdf), or convert to .txt")
        r = pypdf.PdfReader(str(path))
        return "\n".join((pg.extract_text() or "") for pg in r.pages)
    raise RuntimeError(f"unsupported book type {ext!r} (use .txt/.md/.pdf)")


def snapshot() -> Path:
    """Copy the mutable playbook files before any write — every run is revertible."""
    dst = SNAPSHOTS / _now()
    dst.mkdir(parents=True, exist_ok=True)
    for name in MUTABLE + ["conflicts.md"]:
        f = PLAYBOOK_DIR / name
        if f.exists():
            shutil.copy2(f, dst / name)
    return dst


# ---- extraction (Fable) -----------------------------------------------------
def extract(text: str, book: str, transport, max_chars=400_000) -> tuple[list[dict], dict]:
    """One Fable session → list of rule entries. Chunks only if the text is huge
    (Fable's 1M context handles a whole book; chunk defensively above max_chars)."""
    system = (PROMPTS_DIR / "ingestion_system.md").read_text(encoding="utf-8")
    chunks = [text[i:i + max_chars] for i in range(0, len(text), max_chars)] or [""]
    entries, usage = [], {}
    for ci, chunk in enumerate(chunks, 1):
        tag = f" (part {ci}/{len(chunks)})" if len(chunks) > 1 else ""
        user = (f"BOOK: {book}{tag}\n\nExtract the mechanical rules from the text "
                f"below into the JSON schema (an object with an `entries` array). "
                f"Text:\n\n{chunk}")
        reply = transport.judge(system=system, images_b64=[], user_text=user, tier="tier2")
        usage = reply.usage or usage
        d = app.extract_json(reply.text)
        got = (d or {}).get("entries", []) if isinstance(d, dict) else []
        for e in got:
            if _valid_entry(e):
                entries.append(e)
        print(f"  extract{tag}: {len(got)} raw, {len(entries)} valid so far"
              + (f"  [err: {reply.error}]" if reply.error else ""))
    return entries, usage


def _valid_entry(e) -> bool:
    return (isinstance(e, dict)
            and e.get("maps_to") in STRAT_FILE
            and isinstance(e.get("rule"), str) and e["rule"].strip()
            and isinstance(e.get("source"), str) and e["source"].strip()
            and isinstance(e.get("name"), str) and e["name"].strip())


# ---- merge ------------------------------------------------------------------
def _entry_md(e: dict) -> str:
    tags = " ".join(e.get("tags") or [])
    return (f"\n### {e['name'].strip()}\n"
            f"- **Maps to:** {e['maps_to']}\n"
            f"- **Tags:** {tags}\n"
            f"- **Market condition:** {e.get('market_condition', '?')}\n"
            f"- **The rule:** {e['rule'].strip()}\n"
            f"- **Invalidation:** {e.get('invalidation', '?')}\n"
            f"- **Source:** {e['source'].strip()}\n")


def _dedupe_key(e: dict) -> str:
    return (e['name'].strip().lower() + "|" + e['source'].strip().lower())


def merge_entries(entries: list[dict]) -> dict:
    """Append non-conflicting entries to their mapped files (code-dedupe by
    name+source); route conflicts to conflicts.md. Returns per-file counts."""
    counts = {"merged": 0, "quarantined": 0, "dup": 0, "by_file": {}}
    # group by target file
    per_file: dict[str, list[dict]] = {}
    conflicts: list[dict] = []
    for e in entries:
        if e.get("conflicts_with_system"):
            conflicts.append(e)
        else:
            per_file.setdefault(STRAT_FILE[e["maps_to"]], []).append(e)

    for fname, es in per_file.items():
        f = PLAYBOOK_DIR / fname
        text = f.read_text(encoding="utf-8") if f.exists() else ""
        seen = text.lower()
        add = []
        for e in es:
            # code-dedupe: same rule name AND same source already in this file
            if (e["name"].strip().lower() in seen
                    and e["source"].strip().lower() in seen):
                counts["dup"] += 1
                continue
            add.append(e)
        if not add:
            continue
        block = "".join(_entry_md(e) for e in add)
        if INGEST_ANCHOR in text:
            text = text.replace(INGEST_ANCHOR, INGEST_ANCHOR + "\n" + block, 1)
        else:
            text = text.rstrip() + f"\n\n{INGEST_ANCHOR}\n{block}"
        f.write_text(text, encoding="utf-8")
        counts["merged"] += len(add)
        counts["by_file"][fname] = len(add)

    if conflicts:
        head = "" if CONFLICTS.exists() else (
            "# Quarantined conflicts\n\nBook rules that CONTRADICT the system's own "
            "tested material. NOT part of the playbook — the trader reviews them "
            "(or never). Never merged into the rule body.\n")
        with open(CONFLICTS, "a", encoding="utf-8") as fh:
            if head:
                fh.write(head)
            for e in conflicts:
                fh.write(f"\n### {e['name'].strip()}  (conflicts_with_system)\n"
                         f"- **Contradicts:** {e.get('conflict_note', '(unspecified)')}\n"
                         f"- **The rule:** {e['rule'].strip()}\n"
                         f"- **Source:** {e['source'].strip()}\n"
                         f"- **Flagged:** {_now()}\n")
        counts["quarantined"] = len(conflicts)
    return counts


# ---- report -----------------------------------------------------------------
def write_report(book: str, entries: list[dict], counts: dict, usage: dict) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    p = REPORTS / f"{datetime.now(timezone.utc):%Y%m%d}-{Path(book).stem}.md"
    lines = [f"# Ingest report — {book}", f"_{datetime.now(timezone.utc).isoformat()}_\n",
             f"- extracted: **{len(entries)}** valid entries",
             f"- merged: **{counts['merged']}**  ·  quarantined: **{counts['quarantined']}**"
             f"  ·  skipped-duplicate: **{counts['dup']}**",
             f"- per file: " + (", ".join(f"{k} +{v}" for k, v in counts['by_file'].items()) or "none"),
             f"- token usage: in={usage.get('input_tokens')}, out={usage.get('output_tokens')}",
             ""]
    conflicts = [e for e in entries if e.get("conflicts_with_system")]
    if conflicts:
        lines.append("## Quarantined (conflicts_with_system)")
        for e in conflicts:
            lines.append(f"- **{e['name']}** — {e.get('conflict_note', '?')} "
                         f"(source: {e['source']})")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


# ---- orchestration ----------------------------------------------------------
def new_books(manifest: dict) -> list[Path]:
    if not INBOX.exists():
        return []
    out = []
    for f in sorted(INBOX.iterdir()):
        if f.is_file() and f.suffix.lower() in (".txt", ".md", ".pdf"):
            if sha256(f) not in manifest.values():
                out.append(f)
    return out


def run(mode: str, transport):
    for d in (INBOX, INGESTED, INCOMING, REPORTS):
        d.mkdir(parents=True, exist_ok=True)
    manifest = read_manifest()

    if mode == "apply":
        pending = sorted(INCOMING.glob("*.json"))
        if not pending:
            print("nothing in incoming/ to apply.")
            return
        for jf in pending:
            data = json.loads(jf.read_text(encoding="utf-8"))
            entries = data.get("entries", [])
            snapshot()
            counts = merge_entries(entries)
            rep = write_report(data.get("book", jf.stem), entries, counts, data.get("usage", {}))
            jf.unlink()
            print(f"applied {jf.name}: merged {counts['merged']}, "
                  f"quarantined {counts['quarantined']} → report {rep.name}")
        return

    books = new_books(manifest)
    if not books:
        print(f"no new books in {INBOX} (all hashes already in the manifest).")
        return
    print(f"{len(books)} new book(s): {', '.join(b.name for b in books)}")

    for b in books:
        print(f"\n=== {b.name} ===")
        try:
            text = book_text(b)
        except RuntimeError as e:
            print(f"  SKIP: {e}")
            continue
        entries, usage = extract(text, b.name, transport)
        if not entries:
            print("  no valid entries extracted — leaving the book in the inbox.")
            continue

        if mode == "review":
            INCOMING.mkdir(parents=True, exist_ok=True)
            (INCOMING / f"{b.stem}.json").write_text(
                json.dumps({"book": b.name, "entries": entries, "usage": usage},
                           indent=2), encoding="utf-8")
            print(f"  --review: parked {len(entries)} entries in "
                  f"incoming/{b.stem}.json (run --apply to merge).")
            continue

        # full-auto
        snapshot()
        counts = merge_entries(entries)
        rep = write_report(b.name, entries, counts, usage)
        dest = INGESTED / b.name
        shutil.move(str(b), str(dest))
        manifest[b.name] = sha256(dest)
        write_manifest(manifest)
        print(f"  merged {counts['merged']}, quarantined {counts['quarantined']}, "
              f"dup {counts['dup']} → report {rep.name}; book → books_ingested/")


def main():
    ap = argparse.ArgumentParser(description="Autonomous playbook ingestion.")
    ap.add_argument("--review", action="store_true", help="extract only; park in incoming/")
    ap.add_argument("--apply", action="store_true", help="merge approved incoming/ items")
    a = ap.parse_args()
    mode = "apply" if a.apply else ("review" if a.review else "auto")

    cfg = load_config()
    setup_auth(cfg.transport)
    transport = make_transport(cfg.transport, MODEL_ALIAS, MODEL_API_ID)
    run(mode, transport)


if __name__ == "__main__":
    main()
