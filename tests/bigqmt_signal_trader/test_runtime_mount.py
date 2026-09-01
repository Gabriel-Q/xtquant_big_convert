"""Mount-behavior tests for bigqmt_signal_trader_redis_rpc_runtime.

QMT mounts a strategy file by exec'ing it into a namespace that carries the
injected global functions (passorder, download_history_data, ...). Those
names live ONLY in that exec namespace -- the strategy module's
_EXTRA_QMT_GLOBAL_FUNCS resolution checks its own globals and builtins and
never sees them, so without an explicit capture inside the mounted file every
trade/download/query RPC is a silent no-op (verified live 2026-09-01:
get_ipo_info -> NotImplementedError, download_history_data -> False).
"""

import os
import sys
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

RUNTIME_FILE = os.path.join(SRC, "bigqmt_signal_trader_redis_rpc_runtime.py")


def _noop(*args, **kwargs):
    return None


class RuntimeMountBehaviorTest(unittest.TestCase):
    def setUp(self):
        import bigqmt_signal_trader_strategy as strategy_module

        self._strategy = strategy_module
        self._saved_qmt_api = dict(strategy_module._qmt_api)

    def tearDown(self):
        strategy_module = self._strategy
        strategy_module._qmt_api.clear()
        strategy_module._qmt_api.update(self._saved_qmt_api)

    def _mount(self, injected):
        """Exec the runtime source the way QMT does: into a namespace that
        carries the injected global functions."""
        namespace = dict(injected)
        source = open(RUNTIME_FILE, "rb").read().decode("utf-8")
        exec(compile(source, RUNTIME_FILE, "exec"), namespace)
        return namespace

    def test_qmt_mount_binds_injected_globals(self):
        injected = {
            "passorder": _noop,
            "get_trade_detail_data": _noop,
            "download_history_data": _noop,
            "down_history_data": _noop,
            "get_ipo_data": _noop,
        }
        ns = self._mount(injected)

        # QMT's callback protocol: the mounted module must expose these.
        for name in ("init", "handlebar", "adjust", "order_callback", "deal_callback"):
            self.assertTrue(callable(ns.get(name)), "missing callback %s" % name)

        # The injected funcs must have been captured into the strategy's
        # qmt_api table (that is what the RPC handlers look up).
        for name, func in injected.items():
            self.assertIs(self._strategy._qmt_api.get(name), func,
                          "%s was not bound by the mounted runtime" % name)

    def test_plain_module_load_binds_nothing(self):
        """A plain import / DRYRUN loader namespace has none of the injected
        names -- the capture must skip silently and bind nothing."""
        ns = self._mount({})
        self.assertTrue(callable(ns.get("init")))
        for name in ("passorder", "download_history_data", "get_ipo_data"):
            self.assertIsNone(self._strategy._qmt_api.get(name))


if __name__ == "__main__":
    unittest.main()
