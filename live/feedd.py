"""
feedd.py - the MetaTrader5 feed in its own process (127.0.0.1:8081). The MetaTrader5 package can block the calling
process while the terminal is busy/restarting; isolating it here means the web app only ever waits on a short HTTP
timeout. Endpoints: /bars?symbol=&tf=&n=  /tick?symbol=  /positions  /orders  /status  /health
"""
from __future__ import annotations
import json, os, sys, urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live import common as C
from live import mt5feed

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _j(self, obj, code=200):
        b = json.dumps(obj, separators=(",", ":")).encode(); self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        u = urllib.parse.urlsplit(self.path); q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        try:
            if u.path == "/health": return self._j({"ok": True})
            if u.path == "/status": return self._j(mt5feed.status())
            if u.path == "/bars":
                sym = q.get("symbol", ""); tf = q.get("tf", "h4"); n = max(1, min(2000, int(q.get("n", "500") or 500)))
                if not C.safe_key(sym) or tf not in mt5feed.TF: return self._j({"error": "bad request"}, 400)
                return self._j({"bars": mt5feed.bars(sym, tf, n), "tick": mt5feed.tick(sym)})
            if u.path == "/tick": return self._j({"tick": mt5feed.tick(q.get("symbol", ""))})
            if u.path == "/positions": return self._j({"positions": mt5feed.positions()})
            if u.path == "/deals":
                try: pid = int(q.get("position", "0"))
                except ValueError: return self._j({"error": "bad request"}, 400)
                return self._j({"deals": mt5feed.position_costs(pid)})
            if u.path == "/orders":
                sym = q.get("symbol") or None
                if sym and not C.safe_key(sym): return self._j({"error": "bad request"}, 400)
                return self._j({"orders": mt5feed.orders(sym)})
            return self._j({"error": "not found"}, 404)
        except Exception as e:
            return self._j({"error": repr(e)}, 500)

def main():
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("--config"); a = ap.parse_args()
    cfg = C.load_config(a.config); log = C.Log("feed", cfg["logs_dir"]); mt5feed.configure_url("")   # the daemon talks to the terminal itself, never to itself
    port = int((cfg.get("feed") or {}).get("port") or 8081)
    srv = ThreadingHTTPServer(("127.0.0.1", port), H); srv.daemon_threads = True
    log(f"feed daemon on 127.0.0.1:{port} (terminal {mt5feed.TERMINAL_PATH})")
    srv.serve_forever()

if __name__ == "__main__": main()
