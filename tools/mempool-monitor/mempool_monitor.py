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
MAX_TX_LOOKUP = int(os.environ.get("MAX_TX_LOOKUP", "50"))

_state = {"last_poll": 0, "mempool_tx_count": 0, "tx_by_fee": [], "error": None}

def rpc(method, params=None):
    payload = {"jsonrpc": "1.0", "id": "mempool_monitor", "method": method, "params": params or []}
    r = requests.post(RPC_URL, data=json.dumps(payload),
                      auth=HTTPBasicAuth(RPC_USER, RPC_PASS),
                      headers={"Content-Type": "application/json"}, timeout=10)
    r.raise_for_status()
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
                    fee = entry.get("fees", {}).get("base", entry.get("fee"))
                    txs.append({"txid": txid, "fee": fee, "size": entry.get("size")})
                except Exception:
                    # skip individual lookup failures
                    continue
            txs_sorted = sorted(txs, key=lambda x: -x.get("fee", 0))
            _state.update({"last_poll": int(time.time()), "mempool_tx_count": info["tx_count"], "tx_by_fee": txs_sorted, "error": None})
        except Exception as e:
            _state["error"] = str(e)
        time.sleep(POLL_INTERVAL)

class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            payload = {"last_poll": _state.get("last_poll"), "mempool_tx_count": _state.get("mempool_tx_count"), "error": _state.get("error")}
            self._send_json(payload)
        elif self.path == "/top":
            self._send_json({"top_txs": _state.get("tx_by_fee", [])})
        elif self.path == "/health":
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
    server = HTTPServer(("0.0.0.0", HTTP_PORT), SimpleHandler)
    server.serve_forever()

if __name__ == "__main__":
    t = threading.Thread(target=poll_mempool_loop, daemon=True)
    t.start()
    run_server()
