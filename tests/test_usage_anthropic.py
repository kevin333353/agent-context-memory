import unittest

try:
    from scripts.usage import anthropic_usage as au
except ImportError:
    au = None


SSE = """event: message_start
data: {"type":"message_start","message":{"id":"msg_1","model":"claude-opus-4-8","usage":{"input_tokens":412,"cache_creation_input_tokens":100,"cache_read_input_tokens":5888,"output_tokens":1,"service_tier":"standard"}}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hi"}}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":335}}

event: message_stop
data: {"type":"message_stop"}
"""


class AnthropicUsageTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(au, "scripts.usage.anthropic_usage is missing")

    def test_streaming_sse_extracts_all_fields(self):
        rec = au.record_from_sse_text(SSE, ts_utc="2026-07-14T00:00:00Z", latency_ms=1200)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.source, "claude")
        self.assertEqual(rec.ingest, "proxy")
        self.assertEqual(rec.model, "claude-opus-4-8")
        self.assertEqual(rec.input_tokens, 412)
        self.assertEqual(rec.cache_creation_tokens, 100)
        self.assertEqual(rec.cache_read_tokens, 5888)
        self.assertEqual(rec.output_tokens, 335)  # from message_delta, not the seed 1
        self.assertEqual(rec.service_tier, "standard")
        self.assertEqual(rec.latency_ms, 1200)

    def test_non_streaming_message(self):
        msg = {
            "model": "claude-sonnet-5",
            "usage": {
                "input_tokens": 50,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "output_tokens": 20,
            },
        }
        rec = au.record_from_message(msg, ts_utc="2026-07-14T00:00:00Z")
        self.assertEqual(rec.model, "claude-sonnet-5")
        self.assertEqual(rec.input_tokens, 50)
        self.assertEqual(rec.output_tokens, 20)

    def test_no_usage_returns_none(self):
        self.assertIsNone(au.record_from_message({"model": "x"}, ts_utc="t"))
        self.assertIsNone(au.record_from_sse_text("event: ping\ndata: {}\n", ts_utc="t"))

    def test_malformed_sse_lines_are_skipped(self):
        blob = (
            'data: {not json}\n\n'
            'data: {"type":"message_start","message":{"model":"m","usage":'
            '{"input_tokens":7,"output_tokens":1}}}\n\n'
        )
        rec = au.record_from_sse_text(blob, ts_utc="t")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.input_tokens, 7)

    def test_accumulator_ignores_unrelated_events(self):
        acc = au.SSEUsageAccumulator()
        acc.feed_event({"type": "ping"})
        acc.feed_event({"type": "content_block_start", "index": 0})
        self.assertIsNone(acc.to_record(ts_utc="t"))


if __name__ == "__main__":
    unittest.main()
