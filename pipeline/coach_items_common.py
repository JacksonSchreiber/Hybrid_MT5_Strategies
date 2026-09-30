"""coach_items_common.py - shared helpers for the coach 2026-09-30 items (d1_gate_shadow, bank_ladder_detectors,
bar_of_day_study, selection_alpha_exit, d1_features_prereg, concurrent_comovement).

  replay_ex    exit_param_study.replay with the exit bar index and a closed/open flag. check_replay() asserts it returns
               the same R as exit_param_study.replay on every row it is used on.
  prev_day     D1 BAR-INDEX RULE: the last D1 bar dated STRICTLY BEFORE the signal's day (d < day), i.e. the previous
               closed day; -1 when there is none (the row is then excluded, never defaulted to bar 0).
  atr_sma      MT5 iATR: simple 14-bar mean of true range ending at bar j.
  all_rows     the blind record: TrendCont (data/study/trendcont, 7 .dk symbols) + SweepMSS / DeepFib / EMArevQ from the
               long blind AA runs (8 .dk symbols incl. BTCUSD), each tagged with its detector.
"""
from __future__ import annotations
import bisect, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline.exit_mgmt_study import load_rows, MAX_BARS                     # noqa: E402
from pipeline.exit_param_study import replay                                 # noqa: E402
from pipeline.exit_s025_study import aa_rows                                 # noqa: E402

HOLDOUT = "2025"
OTHERS = ("SweepMSS", "DeepFib", "EMArevQ")
ASSET = {"EURUSD": "FX", "GBPUSD": "FX", "USDJPY": "FX", "US100": "indices", "US500": "indices",
         "USOIL": "commodities", "XAUUSD": "commodities", "BTCUSD": "crypto"}
D1_RULE = ("D1 BAR-INDEX RULE: every daily feature uses the last D1 bar dated STRICTLY BEFORE the signal's UTC day "
           "(d < signal date) - the previous closed day; rows with no such bar are excluded. H4: the signal bar is the "
           "bar keyed at signal_time (its OPEN time); the replay starts at the NEXT bar (the EA decides at the signal "
           "bar's close). The >=10 live-card reconciliation of D1 numbers is PENDING (done separately).")


def replay_ex(bars, i0, up, entry, sl, tp1, tp2, bank_at, frac, stop_to):
    """exit_param_study.replay, plus the exit bar index and whether the trade actually closed.
    -> (R, exit_index, closed) or None. closed False = still open at the last bar or MAX_BARS: marked at that close."""
    R = abs(entry - sl)
    if R <= 0: return None
    sgn = 1.0 if up else -1.0
    toR = lambda px: (px - entry) * sgn / R
    doc_R = min(1.0, toR(tp1)) if tp1 else 1.0
    tp2_R = toR(tp2) if tp2 else None
    if bank_at == "doc": bank_R = doc_R
    elif bank_at == "tp1": bank_R = toR(tp1) if (tp1 and tp2_R is not None and toR(tp1) < tp2_R - 1e-9) else None
    else: bank_R = bank_at
    if bank_R is not None and tp2_R is not None and bank_R >= tp2_R - 1e-9: bank_R = None
    if not frac: bank_R = None
    stop_R, banked, locked, rem, hwm = -1.0, False, 0.0, 1.0, 0.0
    end = min(len(bars), i0 + MAX_BARS)
    for j in range(i0, end):
        b = bars[j]
        o_R = toR(b[1])
        hi_R, lo_R = (toR(b[2]), toR(b[3])) if up else (toR(b[3]), toR(b[2]))
        if lo_R <= stop_R:
            return locked + rem * min(stop_R, o_R), j, True
        if bank_R is not None and not banked and hi_R >= bank_R:
            banked, locked, rem = True, frac * bank_R, 1.0 - frac
        if tp2_R is not None and hi_R >= tp2_R:
            return locked + rem * tp2_R, j, True
        hwm = max(hwm, hi_R)
        if hwm >= doc_R: stop_R = max(stop_R, stop_to)
    return locked + rem * toR(bars[end - 1][4]), end - 1, False           # 'end' mark: still open (or MAX_BARS)


def run(x, frac, ex=False):
    """doctrine-family replay of a row: bank `frac` at min(+1R, TP1), stop to entry there (frac 0 = no bank)."""
    args = (x["bars"], x["i0"], x["up"], x["e"], x["s"], x["tp1"], x["tp2"], ("doc" if frac else None), frac, 0.0)
    return replay_ex(*args) if ex else replay(*args)


def check_replay(rows, frac=0.5):
    for x in rows:
        a, b = run(x, frac), run(x, frac, ex=True)
        assert (a is None) == (b is None) and (a is None or abs(a - b[0]) < 1e-12), (x["sym"], x["t"])


def all_rows():
    tc = load_rows()
    for x in tc: x["strat"] = "TrendCont"
    return sorted(tc + aa_rows(set(OTHERS)), key=lambda x: x["t"])


def root(sym): return sym.split(".")[0]


def prev_day(d1_days: list[str], signal_time: str) -> int:
    """index of the last D1 bar dated strictly before the signal's day; d1_days = sorted 'YYYY.MM.DD' strings."""
    return bisect.bisect_left(d1_days, signal_time[:10]) - 1


def atr_sma(bars, j, n=14):
    if j < n + 1: return 0.0
    tr = [max(bars[i][2] - bars[i][3], abs(bars[i][2] - bars[i - 1][4]), abs(bars[i][3] - bars[i - 1][4]))
          for i in range(j - n + 1, j + 1)]
    return sum(tr) / n


def tstat(xs, mu=0.0):
    import statistics as st
    if len(xs) < 2: return 0.0
    sd = st.stdev(xs)
    return (st.mean(xs) - mu) / (sd / len(xs) ** 0.5) if sd > 0 else 0.0


def welch(a, b):
    import statistics as st
    if len(a) < 2 or len(b) < 2: return 0.0
    se = (st.variance(a) / len(a) + st.variance(b) / len(b)) ** 0.5
    return (st.mean(a) - st.mean(b)) / se if se > 0 else 0.0
