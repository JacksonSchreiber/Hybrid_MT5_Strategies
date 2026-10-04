"""progress.py - trader 2026-10-03: a live progress bar for long scripts (build, pack, upload).
In a terminal it redraws one line (bar, %, done/total, rate, ETA); when the output is a log / pipe it prints one line every `every`
percent instead, so a background run's log (or a monitor tailing it) shows the same progress."""
from __future__ import annotations
import sys, time


class Bar:
    def __init__(self, total, label="", unit="", every=5, stream=None):
        self.total = max(1, total); self.label = label; self.unit = unit; self.every = every
        self.s = stream or sys.stderr; self.tty = self.s.isatty(); self.t0 = time.time(); self.n = 0; self.last_pct = -1

    @staticmethod
    def _fmt(sec):
        sec = int(max(0, sec)); return f"{sec // 3600}:{sec // 60 % 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"

    def update(self, k=1, note=""):
        self.n = min(self.total, self.n + k); el = time.time() - self.t0; f = self.n / self.total
        rate = self.n / el if el > 0 else 0.0; eta = (self.total - self.n) / rate if rate > 0 else 0
        line = (f"{self.label} [{'#' * int(f * 30):<30}] {f * 100:5.1f}%  {self.n:,}/{self.total:,}{(' ' + self.unit) if self.unit else ''}"
                f"  {rate:,.1f}/s  elapsed {self._fmt(el)}  left {self._fmt(eta)}{('  ' + note) if note else ''}")
        if self.tty:
            self.s.write("\r" + line + " " * 4); self.s.flush()
        else:
            pct = int(f * 100) // self.every * self.every
            if pct != self.last_pct or self.n == self.total:
                self.last_pct = pct; self.s.write(line + "\n"); self.s.flush()

    def done(self, note=""):
        if self.n < self.total: self.update(self.total - self.n, note)
        if self.tty: self.s.write("\n"); self.s.flush()
