#!/usr/bin/env python3
"""upload_combos.py - trader 2026-10-03: put the combo research data (pipeline/combo_pack.py: combos.bin ~1.8 GB + combos.json) on the
box, next to the webapp's static files, with a live progress bar.
  1 split combos.bin into 64 MB parts (local scratch on /home), 2 scp -C each part (resumable: parts already on the box with the right
  size are skipped), 3 join the parts on the box (cmd copy /b), 4 SHA-256 the joined file on the box and compare with the local one,
  5 only then swap it in and write combos.json LAST (the server serves a build only when combos.json names it), 6 clean up.
    python3 provisioning/upload_combos.py [data/combos/<run>]      (default: the newest packed run)
Needs the WireGuard tunnel and the hybridops key (as deploy_live.sh). Never touches anything else on the box.
"""
from __future__ import annotations
import glob, hashlib, json, os, subprocess, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.progress import Bar          # noqa: E402

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
HOST = "hybridops@10.77.0.1"; KEY = os.path.expanduser("~/.ssh/hybrid_vps_ed25519")
STATIC = "C:/ProgramData/hybrid/live/live/static"; UP = STATIC + "/combos_upload"
PART = 64 * 1024 * 1024
SSH = ["ssh", "-i", KEY, "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", HOST]


def ps(cmd: str) -> str:
    r = subprocess.run(SSH + [cmd], capture_output=True, text=True, timeout=1800)
    if r.returncode != 0: raise RuntimeError(f"remote command failed ({r.returncode}): {cmd[:120]}\n{r.stderr[-500:]}")
    return r.stdout.replace("\r", "").strip()


def main(run=None):
    run = run or sorted(d for d in glob.glob(os.path.join(REPO, "data", "combos", "2*")) if os.path.exists(os.path.join(d, "combos.bin")))[-1]
    src = os.path.join(run, "combos.bin"); size = os.path.getsize(src)
    cm = json.load(open(os.path.join(run, "combos_meta.json")))
    desc = {"build": cm["build"], "n_trades": cm["n_trades"], "n_combos": cm["n_combos"], "rec_bytes": cm["rec_bytes"]}
    assert size == cm["n_trades"] * cm["n_combos"] * cm["rec_bytes"], "combos.bin size does not match its meta"
    print(f"build {cm['build']}: {size / 1e9:.2f} GB -> {HOST}:{STATIC}", flush=True)
    free = int(ps(f"(Get-PSDrive C).Free"))
    if free < 2.5 * size: raise SystemExit(f"box C: has only {free / 1e9:.1f} GB free")
    h = hashlib.sha256(); bar = Bar(size, "hash  ", "bytes", every=25)
    with open(src, "rb") as f:
        while (b := f.read(8 * 1024 * 1024)): h.update(b); bar.update(len(b))
    bar.done(); sha = h.hexdigest()
    ps(f"New-Item -ItemType Directory -Force -Path '{UP}' | Out-Null")
    have = {}
    for ln in ps(f"Get-ChildItem '{UP}' -File | ForEach-Object {{ $_.Name + ' ' + $_.Length }}").splitlines():
        if ln.strip(): n_, l_ = ln.rsplit(" ", 1); have[n_] = int(l_)
    nparts = (size + PART - 1) // PART; names = [f"part_{k:03d}" for k in range(nparts)]
    tmp = tempfile.mkdtemp(prefix="combos_up_", dir=os.path.expanduser("~"))
    bar = Bar(size, "upload", "bytes", every=5); t0 = time.time()
    try:
        with open(src, "rb") as f:
            for k, nm in enumerate(names):
                want = min(PART, size - k * PART)
                if have.get(nm) == want: f.seek(want, 1); bar.update(want, "(already there)"); continue
                lp = os.path.join(tmp, nm)
                with open(lp, "wb") as o: o.write(f.read(want))
                for attempt in range(3):
                    r = subprocess.run(["scp", "-q", "-C", "-i", KEY, "-o", "BatchMode=yes", lp, f"{HOST}:{UP}/{nm}"], capture_output=True, text=True)
                    if r.returncode == 0: break
                    time.sleep(5)
                else:
                    raise SystemExit(f"upload of {nm} failed 3 times: {r.stderr[-300:]} (re-run to resume)")
                os.remove(lp); bar.update(want)
    finally:
        for p in glob.glob(os.path.join(tmp, "*")): os.remove(p)
        os.rmdir(tmp)
    bar.done(f"{size / 1e6 / max(1, time.time() - t0):.1f} MB/s")
    print("joining on the box ...", flush=True)
    plus = "+".join(names)
    ps(f"cmd /c \"cd /d {UP.replace('/', chr(92))} && copy /b /y {plus} combos.bin.new >NUL\"")
    rsha = ps(f"(Get-FileHash '{UP}/combos.bin.new' -Algorithm SHA256).Hash").lower()
    if rsha != sha: raise SystemExit(f"SHA-256 MISMATCH on the box ({rsha[:16]} vs local {sha[:16]}) - nothing swapped in; re-run")
    print(f"SHA-256 verified on the box ({sha[:16]}…); swapping in", flush=True)
    ps(f"Move-Item -Force '{UP}/combos.bin.new' '{STATIC}/combos.bin'")
    lj = os.path.join(tempfile.gettempdir(), "combos.json"); json.dump(desc, open(lj, "w"))
    subprocess.run(["scp", "-q", "-i", KEY, "-o", "BatchMode=yes", lj, f"{HOST}:{STATIC}/combos.json"], check=True)
    ps(f"Remove-Item -Recurse -Force '{UP}'")
    print(f"done: build {cm['build']} live on the box ({size / 1e9:.2f} GB)", flush=True)


if __name__ == "__main__": main(sys.argv[1] if len(sys.argv) > 1 else None)
