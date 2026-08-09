import os
import sys
import threading
import time
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from bigqmt_signal_trader.whole_quote_session import WholeQuoteClientSession


class FakeRpc:
    """Records control RPCs and returns canned subscribe responses."""

    def __init__(self):
        self.calls = []
        self._lock = threading.Lock()

    def __call__(self, method, params):
        with self._lock:
            self.calls.append((method, dict(params)))
        if method == "subscribe_whole_quote":
            codes = sorted(str(c).upper() for c in params.get("codes") or [])
            return {"combo_key": ",".join(codes), "topic": ",".join(codes)}
        return {}

    def methods(self):
        with self._lock:
            return [m for m, _ in self.calls]


class FakePushChannel:
    """Client-side push channel stand-in: lets tests inject server pushes."""

    def __init__(self):
        self.subscriptions = []  # list of (topics_tuple, on_msg)
        self.started = False
        self.stopped = False
        self._on_msg = None

    def start_subscriber(self, topics, on_msg):
        self.started = True
        self._on_msg = on_msg
        self.subscriptions.append(tuple(topics))

    def inject(self, topic, data):
        if self._on_msg is not None:
            self._on_msg(topic, data)

    def stop(self):
        self.stopped = True


class WholeQuoteSessionTest(unittest.TestCase):
    def _session(self, **kwargs):
        rpc = FakeRpc()
        channel = FakePushChannel()
        session = WholeQuoteClientSession(
            rpc_call=rpc,
            push_channel=channel,
            client_id="client-test",
            heartbeat_interval_seconds=kwargs.pop("heartbeat_interval_seconds", 0.05),
            **kwargs,
        )
        return session, rpc, channel

    def test_subscribe_sends_rpc_and_returns_sub_id(self):
        session, rpc, _channel = self._session()
        sub_id = session.subscribe_whole_quote(["SH", "SZ"], callback=lambda d: None)
        self.assertIsNotNone(sub_id)
        self.assertIn("subscribe_whole_quote", rpc.methods())

    def test_subscribe_starts_push_channel_with_topic(self):
        session, _rpc, channel = self._session()
        session.subscribe_whole_quote(["SH", "SZ"], callback=lambda d: None)
        self.assertTrue(channel.started)
        self.assertIn(("SH,SZ",), channel.subscriptions)

    def test_incoming_push_invokes_callback(self):
        session, _rpc, channel = self._session()
        received = []
        session.subscribe_whole_quote(["SH"], callback=received.append)
        channel.inject("SH", {"000001.SZ": {"lastPrice": 10.5}})
        self.assertEqual(received, [{"000001.SZ": {"lastPrice": 10.5}}])

    def test_two_subscriptions_same_combo_share_one_push(self):
        session, _rpc, channel = self._session()
        got_a, got_b = [], []
        session.subscribe_whole_quote(["SH", "SZ"], callback=got_a.append)
        session.subscribe_whole_quote(["sz", "sh"], callback=got_b.append)
        channel.inject("SH,SZ", {"000001.SZ": {"lastPrice": 1.0}})
        self.assertEqual(len(got_a), 1)
        self.assertEqual(len(got_b), 1)

    def test_unsubscribe_stops_callback_and_sends_rpc(self):
        session, rpc, channel = self._session()
        received = []
        sub_id = session.subscribe_whole_quote(["SH"], callback=received.append)
        session.unsubscribe_quote(sub_id)
        self.assertIn("unsubscribe_whole_quote", rpc.methods())
        channel.inject("SH", {"x": 1})
        self.assertEqual(received, [])

    def test_keepalive_sent_for_active_subscriptions(self):
        session, rpc, _channel = self._session(heartbeat_interval_seconds=0.05)
        session.subscribe_whole_quote(["SH"], callback=lambda d: None)
        session.start()
        try:
            deadline = time.time() + 1.5
            while time.time() < deadline and rpc.methods().count("quote_keepalive") < 2:
                time.sleep(0.02)
        finally:
            session.stop()
        self.assertGreaterEqual(rpc.methods().count("quote_keepalive"), 2)

    def test_keepalive_stops_after_unsubscribe(self):
        session, rpc, _channel = self._session(heartbeat_interval_seconds=0.05)
        sub_id = session.subscribe_whole_quote(["SH"], callback=lambda d: None)
        session.start()
        time.sleep(0.15)
        session.unsubscribe_quote(sub_id)
        count_at_unsub = rpc.methods().count("quote_keepalive")
        time.sleep(0.2)
        session.stop()
        self.assertEqual(rpc.methods().count("quote_keepalive"), count_at_unsub)

    def test_replay_resubscribes_all_active(self):
        session, rpc, _channel = self._session()
        session.subscribe_whole_quote(["SH"], callback=lambda d: None)
        session.subscribe_whole_quote(["SZ"], callback=lambda d: None)
        subscribes_before = rpc.methods().count("subscribe_whole_quote")
        session.replay_subscriptions()
        self.assertEqual(rpc.methods().count("subscribe_whole_quote"), subscribes_before + 2)

    def test_client_id_used_in_rpc(self):
        session, rpc, _channel = self._session()
        session.subscribe_whole_quote(["SH"], callback=lambda d: None)
        sub_params = [p for m, p in rpc.calls if m == "subscribe_whole_quote"][0]
        self.assertEqual(sub_params["client_id"], "client-test")


if __name__ == "__main__":
    unittest.main()
