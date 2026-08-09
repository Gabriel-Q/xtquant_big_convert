import os
import sys
import threading
import time
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from bigqmt_signal_trader.quote_push_channel import (
    RedisQuotePushChannel,
    ZmqQuotePushChannel,
    decode_push_payload,
    encode_push_payload,
)


class FakePubSub:
    def __init__(self, redis_client):
        self._redis = redis_client
        self._channels = []
        self._closed = False

    def subscribe(self, *channels):
        self._channels.extend(channels)

    def get_message(self, timeout=0.1):
        # Pull one queued message for a subscribed channel, if any.
        for _ in range(50):
            for channel in self._channels:
                queue = self._redis.messages.setdefault(channel, [])
                if queue:
                    return {"type": "message", "channel": channel, "data": queue.pop(0)}
            time.sleep(0.01)
        return None

    def close(self):
        self._closed = True


class FakeRedis:
    def __init__(self):
        self.messages = {}

    def publish(self, channel, value):
        self.messages.setdefault(channel, []).append(value)
        return 1

    def pubsub(self, ignore_subscribe_messages=True):
        return FakePubSub(self)


class PayloadCodecTest(unittest.TestCase):
    def test_roundtrip(self):
        payload = {"combo_key": "SH,SZ", "data": {"000001.SZ": {"lastPrice": 10.5}}, "ts": 1.5}
        blob = encode_push_payload(payload)
        self.assertEqual(decode_push_payload(blob), payload)

    def test_binary_blob(self):
        # Wire encoding must be bytes (msgpack or utf-8 json), not str.
        blob = encode_push_payload({"a": 1})
        self.assertIsInstance(blob, (bytes, bytearray))


class ZmqPushChannelTest(unittest.TestCase):
    def test_pub_sub_roundtrip_and_topic_filter(self):
        zmq = __import__("zmq")
        ctx = zmq.Context.instance()
        pub_addr = "inproc://quote-push-test-%d" % id(self)

        server = ZmqQuotePushChannel(bind_address=pub_addr, context=ctx)
        server.start_publisher()

        received = []
        done = threading.Event()

        client = ZmqQuotePushChannel(connect_address=pub_addr, context=ctx)

        def on_msg(topic, data):
            received.append((topic, data))
            done.set()

        client.start_subscriber(["SH,SZ"], on_msg)
        try:
            # Give the SUB a moment to connect + apply the subscription filter.
            time.sleep(0.2)
            server.publish("SH,SZ", {"000001.SZ": {"lastPrice": 10.5}})
            server.publish("SH", {"600000.SH": {"lastPrice": 9.9}})  # filtered out
            self.assertTrue(done.wait(2.0), "subscriber did not receive the SH,SZ push")
        finally:
            client.stop()
            server.stop()

        self.assertEqual(len(received), 1)
        topic, data = received[0]
        self.assertEqual(topic, "SH,SZ")
        self.assertEqual(data["000001.SZ"]["lastPrice"], 10.5)


class RedisPushChannelTest(unittest.TestCase):
    def test_pub_sub_roundtrip(self):
        redis_client = FakeRedis()
        server = RedisQuotePushChannel(redis_client, account_id="acct")
        server.start_publisher()

        received = []
        done = threading.Event()
        client = RedisQuotePushChannel(redis_client, account_id="acct")

        def on_msg(topic, data):
            received.append((topic, data))
            done.set()

        client.start_subscriber(["SH,SZ"], on_msg)
        try:
            time.sleep(0.1)
            server.publish("SH,SZ", {"000001.SZ": {"lastPrice": 10.5}})
            self.assertTrue(done.wait(2.0), "redis subscriber did not receive the push")
        finally:
            client.stop()
            server.stop()

        self.assertEqual(received[0][0], "SH,SZ")
        self.assertEqual(received[0][1]["000001.SZ"]["lastPrice"], 10.5)

    def test_channel_name_scoped_by_account(self):
        redis_client = FakeRedis()
        server = RedisQuotePushChannel(redis_client, account_id="acct")
        server.start_publisher()
        server.publish("SH", {"x": 1})
        server.stop()
        self.assertIn("bigqmt:quote_push:acct:SH", redis_client.messages)


if __name__ == "__main__":
    unittest.main()
