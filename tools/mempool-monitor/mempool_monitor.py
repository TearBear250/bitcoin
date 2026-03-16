#!/usr/bin/env python3
"""
Simple mempool monitor:
- connects to bitcoind RPC
- polls mempool transactions periodically
- exposes a tiny HTTP JSON API with aggregated mempool stats
- designed to run out-of-band (no Bitcoin Core changes)
"""
import os
import time
import threading
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from requests.auth import HTTPBasicAuth
import requests

RPC_URL = os.environ.get("BITCOIND_RPC_URL", "http://127.0.0.1:8332/")
RPC_USER = os.environ.get("BITCOIND_RPC_USER", "user")
RPC_PASS = os.environ.get("BITCOIND_RPC_PASS", "pass")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "10"))
HTTP_PORT = int(os.environ.get("HTTP_PORT", "8080"))
# Bind address: defaults to 127.0.0.1 (loopback only). Set HTTP_BIND=0.0.0.0
# only if you are behind a firewall/reverse-proxy and understand the exposure.
HTTP_BIND = os.environ.get("HTTP_BIND", "127.0.0.1")
MAX_TX_LOOKUP = int(os.environ.get("MAX_TX_LOOKUP", "50"))

_state = {"last_poll": 0, "mempool_tx_count": 0, "tx_by_fee": [], "error": None}
_state_lock = threading.Lock()

def rpc(method, params=None):
    payload = {"jsonrpc": "1.0", "id": "mempool_monitor", "method": method, "params": params or []}
    try:
        r = requests.post(RPC_URL, data=json.dumps(payload),
                          auth=HTTPBasicAuth(RPC_USER, RPC_PASS),
                          headers={"Content-Type": "application/json"}, timeout=10)
        r.raise_for_status()
    except requests.Timeout:
        raise RuntimeError(f"RPC call '{method}' to {RPC_URL} timed out")
    except requests.RequestException as exc:
        raise RuntimeError(f"RPC call '{method}' to {RPC_URL} failed: {exc}") from exc
    return r.json()["result"]

def poll_mempool_loop():
    while True:
        try:
            txids = rpc("getrawmempool")  # list of txids
            info = {"tx_count": len(txids)}
            txs = []
            for txid in txids[:MAX_TX_LOOKUP]:
                try:
                    entry = rpc("getmempoolentry", [txid])
                    # bitcoind >= 0.19 nests fees under "fees.base"; older
                    # versions expose a top-level "fee" field.
                    fee = entry.get("fees", {}).get("base", entry.get("fee"))
                    txs.append({"txid": txid, "fee": fee, "size": entry.get("size")})
                except Exception:
                    # skip individual lookup failures
                    continue
            txs_sorted = sorted(txs, key=lambda x: -x.get("fee", 0))
            with _state_lock:
                _state.update({"last_poll": int(time.time()), "mempool_tx_count": info["tx_count"], "tx_by_fee": txs_sorted, "error": None})
        except Exception as e:
            with _state_lock:
                _state["error"] = str(e)
        time.sleep(POLL_INTERVAL)

class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            with _state_lock:
                payload = {"last_poll": _state.get("last_poll"), "mempool_tx_count": _state.get("mempool_tx_count"), "error": _state.get("error")}
            self._send_json(payload)
        elif self.path == "/top":
            with _state_lock:
                top = list(_state.get("tx_by_fee", []))
            self._send_json({"top_txs": top})
        elif self.path == "/health":
            with _state_lock:
                ok = _state.get("error") is None
            self._send_json({"ok": ok})
        else:
            self.send_response(404)
            self.end_headers()
    def _send_json(self, obj):
        b = json.dumps(obj, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)
    def log_message(self, format, *args):
        # silence default logging or customize here
        return

def run_server():
    server = HTTPServer((HTTP_BIND, HTTP_PORT), SimpleHandler)
    server.serve_forever()

if __name__ == "__main__":
    t = threading.Thread(target=poll_mempool_loop, daemon=True)
    t.start()
    run_server()
