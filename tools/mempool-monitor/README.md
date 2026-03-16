# Mempool Monitor (prototype)

Purpose
- Lightweight out-of-band monitor for Bitcoin mempool intended for neutral market-monitoring services.
- Safe to run alongside Bitcoin Core (no Core changes required).
- Exposes a simple HTTP API and can be extended to publish signed events to downstream systems (BTMs, dashboards, ML systems).

Quick start
1. Run bitcoind with RPC enabled (rpcuser/rpcpassword and rpcallowip configured).
2. Set environment variables:
   - BITCOIND_RPC_URL (default: http://127.0.0.1:8332/)
   - BITCOIND_RPC_USER, BITCOIND_RPC_PASS
   - OPTIONAL: POLL_INTERVAL (seconds), HTTP_PORT, MAX_TX_LOOKUP
3. Run:
   ```
   python3 mempool_monitor.py
   ```
4. Endpoints:
   - Health: GET http://localhost:8080/health
   - Summary: GET http://localhost:8080/
   - Top transactions: GET http://localhost:8080/top

Design notes &amp; integration
- Keep this project separate from Bitcoin Core to avoid consensus/policy conflicts.
- Integration into your fork:
  - Option A: Add this as a subdirectory (tools/mempool-monitor) in your fork — included as a helper tool.
  - Option B: Keep as a separate repo and add as a submodule in git.
- For BTMs and production deployments:
  - Use mutual TLS, signed events (HSM-backed keys), authentication and rate-limiting.
  - Use message queue (Kafka, NATS) or webhook with signed payloads for distribution.
  - Implement observability (metrics, logs) and replayable audit logs.

Security &amp; privacy
- Do not send private keys or wallet data to this service.
- Use secure channels (mTLS) and store signing keys in hardware or secure enclaves.
- Consider privacy implications of broadcasting transaction-level metadata.

Extensibility
- Add transaction graph analysis, fee-bumping detection, replace-by-fee signals, and heuristics for market-impact scoring.
- Add plugin hooks to publish events to downstream consumers.
- Add authentication and RBAC for endpoints.

License
- This prototype is licensed under MIT by default. Change as needed.
