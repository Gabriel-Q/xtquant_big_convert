# coding: utf-8
"""Download jobs must not need a redis TRANSPORT, only a redis.

On a zmq deployment the async download API answered

    RuntimeError: download jobs require a Redis client

for submit_download_history_data, get_download_status and wait_download --
because handlers.download_job_redis_client was built only when the transport
was redis.

The worker on the other side of that None was running the whole time.
_pump_download_jobs runs on every adjust tick regardless of transport and gets
its client from _exec_event_redis, which exists precisely because "_rpc_service
has none, which is the zmq-transport case". So queued jobs would have been
processed; there was simply no way to queue one.

Both stores now take the same client, and _exec_event_redis is the one that
builds it -- not a second builder, because it caches. Its docstring records
why: a fresh client per call leaked a connection pool per event, and redis-py's
__del__ then raised an AttributeError that Python swallows as "Exception
ignored in", visible only in the QMT panel.
"""

import inspect
import os
import sys
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))

from bigqmt_signal_trader.redis_rpc import BigQmtRpcHandlers

import bigqmt_signal_trader_strategy as strategy


class FakeRedis(object):
    pass


class WiringTest(unittest.TestCase):
    def setUp(self):
        self.source = inspect.getsource(strategy._build_rpc_service)

    def test_both_stores_take_the_same_client(self):
        self.assertIn("handlers.download_job_redis_client = _store_redis", self.source)
        self.assertIn("handlers.order_identity_redis_client = _store_redis", self.source)

    def test_the_client_falls_back_past_the_transport(self):
        self.assertIn(
            "_store_redis = response_redis_client or redis_client "
            "or _exec_event_redis(config)", self.source)

    def test_it_does_not_build_a_second_client_of_its_own(self):
        """_exec_event_redis caches; a private builder would not.

        (build_redis_client does appear in _build_rpc_service -- that is the
        redis-transport branch above, which is fine and predates this.)
        """
        self.assertNotIn("_store_redis = ", self.source.replace(
            "_store_redis = response_redis_client or redis_client "
            "or _exec_event_redis(config)", ""))
        self.assertFalse(hasattr(strategy, "_build_identity_redis_client"))

    def test_the_pump_uses_the_same_helper(self):
        """Which is why the worker was running while the door was locked."""
        pump = inspect.getsource(strategy._pump_download_jobs)

        self.assertIn("_exec_event_redis(config)", pump)

    def test_the_pump_runs_on_every_adjust_tick(self):
        adjust = inspect.getsource(strategy.adjust)

        self.assertIn("_pump_download_jobs", adjust)


class HandlerTest(unittest.TestCase):
    def _handlers(self, redis_client):
        handlers = BigQmtRpcHandlers.__new__(BigQmtRpcHandlers)
        handlers.download_job_redis_client = redis_client
        return handlers

    def test_a_client_is_handed_straight_back(self):
        redis_client = FakeRedis()

        self.assertIs(self._handlers(redis_client)._download_job_redis(), redis_client)

    def test_no_redis_at_all_still_says_so_plainly(self):
        """A deployment with no redis configured has no job store, and saying
        that is right -- what was wrong was saying it on zmq deployments that
        did have one."""
        with self.assertRaises(RuntimeError) as caught:
            self._handlers(None)._download_job_redis()

        self.assertIn("require a Redis client", str(caught.exception))

    def test_the_three_download_methods_all_go_through_it(self):
        source = inspect.getsource(BigQmtRpcHandlers)

        for method in ("_handle_get_download_status", "_handle_wait_download",
                       "_handle_submit_download_history_data2"):
            body = source.split("def %s" % method, 1)[1].split("\n    def ", 1)[0]
            self.assertIn("_download_job_redis()", body, method)


if __name__ == "__main__":
    unittest.main()
