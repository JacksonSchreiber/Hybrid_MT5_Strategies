"""
backup.py - build the trader's backup zip on demand (web "Backup" button). Contents = everything needed to rebuild
state on a fresh box (docs/vps-provisioning.md, recovery section): the queue root (journals, signals, tasks/done,
acks, positions, state, config, audit, monitor + web audit; chart PNG cache excluded), the advisor's verdicts log,
notes and verdict records, the calendar store, MT5 presets/templates and the scheduled-task definitions.
"30 days" = files modified inside the window, plus every config/state file regardless of age.
"""
from __future__ import annotations
import glob, io, json, os, subprocess, time, zipfile
from datetime import timedelta
from live import common as C

ALWAYS = ("config", "state", "monitor", "web/web_audit.log")           # under the queue root, age ignored

def _add(z: zipfile.ZipFile, path: str, arc: str, since: float, always: bool) -> int:
    try: st = os.stat(path)
    except OSError: return 0
    if not always and st.st_mtime < since: return 0
    z.write(path, arc); return 1

def build(cfg: dict, days: int = 30) -> tuple[bytes, str, dict]:
    now = C.now_utc(); since = time.time() - days * 86400
    root = cfg["root"]; adv = (cfg.get("advisor") or {}).get("live_dir") or ""; cal = (cfg.get("calendar") or {}).get("dir") or ""
    buf = io.BytesIO(); n = 0; z = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)
    for dirpath, dirnames, files in os.walk(root):
        rel = os.path.relpath(dirpath, root).replace("\\", "/")
        if rel.startswith("web/charts") or rel.startswith("calendar/snapshots"): continue
        always = any(rel == a or rel.startswith(a + "/") for a in ALWAYS)
        for f in files:
            if f.endswith(".tmp"): continue
            p = os.path.join(dirpath, f); a = "live/" + (rel + "/" if rel != "." else "") + f
            n += _add(z, p, a, since, always or a == "live/web/web_audit.log")
    if adv and os.path.isdir(adv):
        for f in ("verdicts.live.log", "notes.live.md", "CLAUDE.md"):
            n += _add(z, os.path.join(adv, f), "advisor/" + f, since, True)
    if cal and os.path.isdir(cal):
        for f in ("forward.json",):
            n += _add(z, os.path.join(cal, f), "calendar/" + f, since, True)
        for f in glob.glob(os.path.join(cal, "config", "*")): n += _add(z, f, "calendar/config/" + os.path.basename(f), since, True)
    # MT5: presets + templates + the terminal's profiles (chart layout) - small and needed for a rebuild
    for term in glob.glob(os.path.join(os.path.dirname(cfg["common_files"]), "*")):
        if not os.path.isdir(os.path.join(term, "MQL5")): continue
        for sub in ("MQL5/Presets", "MQL5/Profiles/Templates"):
            for f in glob.glob(os.path.join(term, sub, "*")):
                if os.path.isfile(f): n += _add(z, f, "mt5/" + sub + "/" + os.path.basename(f), since, True)
    for f in glob.glob(os.path.join(cfg["common_files"], "econ_events.csv")): n += _add(z, f, "mt5/Common/Files/econ_events.csv", since, True)
    # scheduled tasks (XML) + live config
    if os.name == "nt":
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-ScheduledTask hybrid-* | ForEach-Object { '### ' + $_.TaskName; Export-ScheduledTask -TaskName $_.TaskName }"], capture_output=True, text=True, timeout=60).stdout
            z.writestr("tasks/hybrid-tasks.xml.txt", out); n += 1
        except Exception: pass
    if os.path.exists(cfg.get("_path", "")): n += _add(z, cfg["_path"], "live_config.json", since, True)
    manifest = {"built_at": C.now_iso(), "days": days, "from": (now - timedelta(days=days)).strftime("%Y-%m-%d"), "to": now.strftime("%Y-%m-%d"), "files": n, "root": root}
    z.writestr("MANIFEST.json", json.dumps(manifest, indent=1)); z.close()
    name = f"hybrid-backup_{manifest['from']}_to_{manifest['to']}.zip"
    return buf.getvalue(), name, manifest

def record(cfg: dict, manifest: dict, name: str) -> None:
    C.atomic_write_json(os.path.join(cfg["root"], "web", "last_backup.json"), {"ts": manifest["built_at"], "name": name, "files": manifest["files"]})

def last(cfg: dict) -> dict | None:
    return C.load_json(os.path.join(cfg["root"], "web", "last_backup.json"))

def days_since(cfg: dict) -> float | None:
    j = last(cfg); t = C.parse_iso(j["ts"]) if j else None
    return (C.now_utc() - t).total_seconds() / 86400 if t else None
