# CLAUDE.md

Notes for working in this repo. Everything here cost real time to learn; none of
it is obvious from the code.

## What this is

A Python RPC bridge exposing Big QMT (大 QMT) trading APIs to external programs,
plus a MiniQMT-compatible client layer. `passorder` / `get_trade_detail_data`
are globals injected into QMT's own Python process, which is why the bridge
exists: MiniQMT's `XtQuantServer` channel returns `connect() == -1` here.

Two sides, and it matters which one you are editing:

- **Server** — runs *inside* QMT: `src/bigqmt_signal_trader_strategy.py`,
  `src/bigqmt_signal_trader_redis_rpc_runtime.py`, `src/bigqmt_signal_trader/`
- **Client** — runs in the user's own program: `src/bigqmt_signal_trader/xtquant_compat.py`,
  `src/xtquant/` (a shim standing in for the real MiniQMT package)

## Hard constraints

**`get_trade_detail_data` returns EMPTY off the main strategy thread.** This is
the single most important fact in the project. It kills any "background worker
polls orders/positions" design. Order and query RPCs run on the adjust (main)
thread — see `ORDER_METHODS` / `LISTENER_DEFERRED_METHODS` in `redis_rpc.py`.
Never move them to a background thread, and never let `rpc_background_threads`
default to True in any config or template.

**QMT ships Python 3.6** (`bin.x64/python36.dll`). Server-side code must avoid
3.7+ syntax. Notably, module-level `__getattr__` (PEP 562) is 3.7+ and does
nothing there — f-strings are fine (3.6+), walrus and dataclasses are not.

**No star imports anywhere in `src/`.** The single-file builds exec each module
inside a `def _mod_N():` body, where `from X import *` is a SyntaxError. Pinned
by `tests/bigqmt_signal_trader/test_qmt_sandbox_loading.py`, which is the only
place in the suite that compiles a module inside a function.

**QMT enforces an import whitelist.** `socket` has been rejected in the field,
including indirectly (`logging_setup` → `logging.handlers` → `socket`). AST
scanning for direct imports is not enough. Keep setup-time tooling
(`init_config`, which imports `subprocess`/`getpass`) out of anything that runs
in the sandbox.

**QMT's order/deal callbacks run on a C++ thread** entered via
`PyGILState_Ensure`. The first exec of a not-yet-imported module on that thread
fails in the C layer *without setting a Python exception* — it surfaces as
`SystemError: error return without exception set`. Import at module load, never
inside a callback.

## Testing

```bash
python -m pytest tests/ -q
```

Two habits worth keeping:

- **Check collection count, not just pass count.** A merged PR once left 27 test
  modules silently uncollectable; `-q` output looks identical.
  ```bash
  find tests -name "test_*.py" | wc -l
  python -m pytest tests/ --collect-only -q | grep -oE "^tests[^:]*\.py" | sort -u | wc -l
  ```
- **Verify a new test fails against the pre-fix code** (`git stash`, run, pop).
  Tests written from the same wrong premise as the code pass while the premise is
  wrong — that is exactly how PR #88's mis-mapped order types stayed green.

## Deploying to the live terminal

Server-side code goes to `D:\国金证券QMT交易端_lemo\python\`. Sync the package
directories **and the top-level modules** — `bigqmt_signal_trader_redis_rpc_runtime.py`
is a top-level file and is easy to miss. Never overwrite
`bigqmt_signal_trader_local_config.py` or `..._client_config.py`; they hold the
account id and credentials.

QMT keeps strategy modules in `sys.modules` across editor re-runs, so **a deploy
does nothing until the user restarts the strategy**. Ask them to restart, then
check `userdata/log/XtClient_FormulaOutput_YYYYMMDD.log` (not `userdata_mini/`,
which is a stale MiniQMT subprocess) — the reqid suffix increments on a fresh
instance.

**When probing the deployed code, put your temp directory ahead of the QMT
directory on `sys.path`.** The QMT directory contains a real
`bigqmt_signal_trader_local_config.py`; getting the order wrong makes every
probe silently read the live config instead of your fixture, which looks exactly
like the fix not working.

## Releasing

Version in `pyproject.toml`, entry in `CHANGELOG.md`, then tag and push.

**Write release notes to a file and use `--notes-file`.** Backticks in an inline
`--body` are command substitution and get silently eaten — this has corrupted
release notes and an issue comment. Same for `gh issue comment`.

**`gh release create` uploads assets after creating the release, and deletes the
release if an upload fails.** If it times out and moves to the background, do
NOT upload manually — the duplicate returns HTTP 422, the background create
rolls back, and the whole release disappears while the tag stays. Create the
release with no assets, then `gh release upload` one file at a time. Verify
afterwards that both artifacts are attached and that the notes still render.

## Working with contributed PRs

Several arrive AI-generated with passing tests. Two things have needed catching:

- **Constants asserted as literals.** Check every value against
  `src/xtquant/xtconstant.py` by name. PR #88 mapped `33`/`34` as special credit
  financing; they are stock-option operations (`40`/`41` are the credit ones),
  and its tests encoded the same mistake.
- **Contributors' own live settings in templates.** One PR carried a real account
  id plus `rpc_allow_order_methods=True`. Diff config blocks against
  `src/bigqmt_signal_trader_local_config.example.py` before merging.

Fork PRs cannot be pushed to. Merge, then land corrections as a follow-up PR
from a branch in this repo.

## Reporting

Say what was verified and what was not. Several fixes here can only be confirmed
by a reporter with a credit account, a restricted broker sandbox, or live fills.
Record those as known limitations in the release notes rather than implying
coverage — and when a limitation is later resolved by real evidence, say so
explicitly in the next release.
