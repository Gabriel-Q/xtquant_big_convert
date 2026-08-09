"""Client-side whole-quote subscription session.

Owns the per-process state for ``subscribe_whole_quote``: the local
subscription table, the shared push-channel subscriber thread, and the
keepalive heartbeat thread. One session is shared by every ``subscribe_whole_quote``
call in the process (``BigQmtXtData`` delegates here), so all subscriptions ride
a single push-channel connection and a single heartbeat loop.

The big-QMT whole-quote callback is INCREMENTAL (only changed symbols), so a
subscription does not by itself deliver an initial full snapshot — callers layer
a ``get_full_tick`` prime on top (done in ``BigQmtXtData.subscribe_whole_quote``).
"""

import threading


def _norm_topic(code_list):
    return ",".join(sorted({str(c).strip().upper() for c in (code_list or []) if str(c or "").strip()}))


class WholeQuoteClientSession(object):
    def __init__(self, rpc_call, push_channel, client_id, heartbeat_interval_seconds=3.0, sub_id_func=None):
        """``rpc_call`` is ``client.call``-shaped: fn(method, params) -> dict.
        ``push_channel`` is a QuotePushChannel used purely as a subscriber.
        ``sub_id_func`` (optional) mints subscription ids; defaults to a counter."""
        self._rpc = rpc_call
        self._channel = push_channel
        self.client_id = str(client_id or "")
        self._heartbeat_interval = float(heartbeat_interval_seconds)
        self._sub_id_func = sub_id_func
        self._seq = 0
        self._lock = threading.RLock()
        self._subscriptions = {}  # sub_id -> {"topic": str, "callback": fn, "codes": [...]}
        self._started = False
        self._subscriber_active = False
        self._heartbeat_thread = None

    # -- subscription lifecycle ---------------------------------------------
    def subscribe_whole_quote(self, code_list, callback=None):
        codes = [str(c) for c in (code_list or []) if str(c or "").strip()]
        if not codes:
            raise ValueError("code_list is required")
        with self._lock:
            sub_id = self._next_sub_id()
        result = self._rpc(
            "subscribe_whole_quote",
            {"client_id": self.client_id, "sub_id": sub_id, "codes": codes},
        ) or {}
        topic = str(result.get("topic") or result.get("combo_key") or _norm_topic(codes))
        with self._lock:
            self._subscriptions[sub_id] = {"topic": topic, "callback": callback, "codes": codes}
            self._sync_subscriber_locked()
        return sub_id

    def unsubscribe_quote(self, sub_id):
        with self._lock:
            entry = self._subscriptions.pop(sub_id, None)
        if entry is None:
            return 0
        try:
            self._rpc("unsubscribe_whole_quote", {"client_id": self.client_id, "sub_id": sub_id})
        finally:
            with self._lock:
                self._sync_subscriber_locked()
        return 0

    def has_subscription(self, sub_id):
        with self._lock:
            return sub_id in self._subscriptions

    def replay_subscriptions(self):
        """Re-send subscribe for every active sub_id (server restart recovery).
        Idempotent on the server (keyed by client_id+combo), so replays are safe."""
        with self._lock:
            items = [(sid, dict(entry)) for sid, entry in self._subscriptions.items()]
        for sub_id, entry in items:
            self._rpc(
                "subscribe_whole_quote",
                {"client_id": self.client_id, "sub_id": sub_id, "codes": entry["codes"]},
            )

    # -- heartbeat -------------------------------------------------------------
    def start(self):
        with self._lock:
            if self._started:
                return
            self._started = True
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop, name="bigqmt-quote-keepalive", daemon=True
            )
            self._heartbeat_thread.start()

    def stop(self):
        with self._lock:
            self._started = False
        thread = self._heartbeat_thread
        if thread is not None:
            thread.join(timeout=1.0)
        self._heartbeat_thread = None

    def _heartbeat_loop(self):
        import time

        while True:
            with self._lock:
                if not self._started:
                    return
                sub_ids = list(self._subscriptions.keys())
            for sub_id in sub_ids:
                try:
                    self._rpc("quote_keepalive", {"client_id": self.client_id, "sub_id": sub_id})
                except Exception:
                    pass
            time.sleep(self._heartbeat_interval)

    # -- push routing ------------------------------------------------------------
    def _on_push(self, topic, data):
        with self._lock:
            callbacks = [
                entry["callback"]
                for entry in self._subscriptions.values()
                if entry["topic"] == topic and entry["callback"] is not None
            ]
        for callback in callbacks:
            try:
                callback(data)
            except Exception:
                pass

    def _sync_subscriber_locked(self):
        """(Re)start the push-channel subscriber to cover exactly the active
        topics. Caller holds the lock. No-op when nothing is subscribed."""
        topics = sorted({entry["topic"] for entry in self._subscriptions.values()})
        if not topics:
            return
        # Restart with the full active topic set so newly-added topics get covered.
        self._channel.start_subscriber(topics, self._on_push)
        self._subscriber_active = True

    def _next_sub_id(self):
        if self._sub_id_func is not None:
            return self._sub_id_func()
        self._seq += 1
        return self._seq
