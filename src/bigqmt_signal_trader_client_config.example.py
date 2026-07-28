# coding: utf-8
"""Client-side private config example for MiniQMT-compatible replacement.

Copy this file to:

    src/bigqmt_signal_trader_client_config.py

Do not commit the real file. It may contain account ids and Redis credentials.
"""

BIGQMT_ACCOUNT_ID = "YOUR_ACCOUNT_ID"
BIGQMT_RPC_TIMEOUT_SECONDS = 6.0

BIGQMT_REDIS_CONFIG = {
    "host": "YOUR_REDIS_HOST",
    "port": 6379,
    "db": 5,
    "username": "",
    "password": "",
}

# Default direct mode calls get_full_tick through RPC. Set enabled=True only when
# you want client-side get_full_tick to read demand-driven Redis snapshots.
BIGQMT_FULL_TICK_CACHE_CONFIG = {
    "enabled": False,
    "demand_ttl_seconds": 10,
    "cache_ttl_seconds": 10,
    "wait_seconds": 3.5,
    "poll_interval_seconds": 0.2,
}

# Client-side LOCAL market-data cache.
#   download_history_data2(codes, period, start_time, ..., callback) pulls bars
#   over RPC once and persists them under `dir`; get_local_data(...) then reads
#   them locally with NO RPC to Big QMT (for offline / repeated local analysis).
#   - dir: cache folder (default ~/.bigqmt_cache), one pickle per (period, code).
#   - fallback_rpc: if True, get_local_data auto-fetches+caches a cache miss;
#     if False (default), a cache-missed code is simply omitted (download first).
BIGQMT_LOCAL_CACHE_CONFIG = {
    "enabled": True,
    "dir": None,            # None -> ~/.bigqmt_cache
    "fallback_rpc": False,
    # Storage format: "auto" (parquet if pyarrow installed, else pickle),
    # "parquet" (columnar/compressed/cross-language — recommended), or "pkl".
    # One file per (period, dividend_type, code); switching format auto-migrates.
    "format": "auto",
}

# FormulaServer direct read fast-path (port 58600).
#   Big QMT's built-in C++ quote/reference service. Routing reads straight to it
#   bypasses the RPC bridge AND the QMT python thread's GIL: ~0.07ms vs ~13ms
#   over redis. Enabled by default; you normally do not need this block.
#
#   Covers reference/history reads only. Account, position, order, trade and
#   五档 (get_full_tick) calls are NOT served by FormulaServer and always go over
#   RPC. Every miss — unmapped method, untranslatable params, server down —
#   falls back to RPC automatically, so an unreachable 58600 changes nothing.
BIGQMT_FORMULA_SERVER_CONFIG = {
    "enabled": True,        # or set BIGQMT_FORMULA_ENABLED=0 in the environment
    # "host": "127.0.0.1",  # FormulaServer binds 0.0.0.0, so cross-machine works
    #                       # if the firewall allows it
    # "port": 58600,        # unset -> read from qmt_root's formulaserver.ini,
    #                       # then fall back to 58600
    # "qmt_root": r"D:\国金证券QMT交易端",
    # "timeout_seconds": 3.0,
    # "methods": [...],     # restrict routing to a subset (default: all mapped)
    # "failure_cooldown_seconds": 30.0,  # pause routing this long after a failure
}
