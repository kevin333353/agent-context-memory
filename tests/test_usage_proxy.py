import http.client
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from scripts.usage import proxy as proxy_module
    from scripts.usage import store as store_module
except ImportError:
    proxy_module = None
    store_module = None


SSE_BYTES = (
    b"event: message_start\n"
    b'data: {"type":"message_start","message":{"model":"claude-opus-4-8",'
    b'"usage":{"input_tokens":412,"cache_creation_input_tokens":100,'
    b'"cache_read_input_tokens":5888,"output_tokens":1,"service_tier":"standard"}}}\n\n'
    b"event: message_delta\n"
    b'data: {"type":"message_delta","usage":{"output_tokens":335}}\n\n'
    b"event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n"
)


class MockUpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self.wfile.write(SSE_BYTES)

    def do_GET(self):
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _serve(server):
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return t


class UsageProxyTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(proxy_module, "scripts.usage.proxy is missing")
        self.tmp = tempfile.TemporaryDirectory()
        self.store = store_module.UsageStore(Path(self.tmp.name) / "usage.sqlite")

        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), MockUpstreamHandler)
        _serve(self.upstream)
        up_port = self.upstream.server_address[1]

        self.proxy = proxy_module.make_server(
            "127.0.0.1", 0, self.store, upstream=f"http://127.0.0.1:{up_port}"
        )
        _serve(self.proxy)
        self.proxy_port = self.proxy.server_address[1]

    def tearDown(self):
        self.proxy.shutdown()
        self.proxy.server_close()
        self.upstream.shutdown()
        self.upstream.server_close()
        self.store.close()
        self.tmp.cleanup()

    def _post(self, path, body=b'{"model":"x"}'):
        c = http.client.HTTPConnection("127.0.0.1", self.proxy_port, timeout=10)
        c.request("POST", path, body=body,
                  headers={"Content-Type": "application/json", "Content-Length": str(len(body))})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def _get(self, path):
        c = http.client.HTTPConnection("127.0.0.1", self.proxy_port, timeout=10)
        c.request("GET", path)
        r = c.getresponse()
        data = r.read()
        status, ctype = r.status, r.getheader("Content-Type")
        c.close()
        return status, ctype, data

    def test_streaming_relay_is_byte_identical(self):
        status, data = self._post("/v1/messages")
        self.assertEqual(status, 200)
        self.assertEqual(data, SSE_BYTES)

    def test_messages_usage_is_recorded(self):
        self._post("/v1/messages")
        self.assertEqual(self.store.count(), 1)
        row = self.store.recent(1)[0]
        self.assertEqual(row["source"], "claude")
        self.assertEqual(row["ingest"], "proxy")
        self.assertEqual(row["model"], "claude-opus-4-8")
        self.assertEqual(row["input_tokens"], 412)
        self.assertEqual(row["cache_read_tokens"], 5888)
        self.assertEqual(row["output_tokens"], 335)
        self.assertIsNotNone(row["latency_ms"])

    def test_non_messages_path_forwards_without_recording(self):
        status, ctype, data = self._get("/v1/models")
        self.assertEqual(status, 200)
        self.assertIn(b'"ok":true', data)
        self.assertEqual(self.store.count(), 0)

    def test_dashboard_served_locally(self):
        status, ctype, data = self._get("/__acm/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b"Agent Context Memory", data)

    def test_dashboard_api_summary(self):
        self._post("/v1/messages")
        status, ctype, data = self._get("/__acm/api/summary")
        self.assertEqual(status, 200)
        self.assertIn("application/json", ctype)
        self.assertIn(b'"requests": 1', data)

    def test_fail_open_when_upstream_down(self):
        dead = proxy_module.make_server(
            "127.0.0.1", 0, self.store, upstream="http://127.0.0.1:1"
        )
        _serve(dead)
        try:
            port = dead.server_address[1]
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            c.request("POST", "/v1/messages", body=b"{}",
                      headers={"Content-Length": "2"})
            r = c.getresponse()
            self.assertEqual(r.status, 502)
            r.read()
            c.close()
            # an error row is recorded, but the client still got a clean 502
            errs = [e for e in self.store.recent(10) if e["status"].startswith("error")]
            self.assertTrue(errs)
        finally:
            dead.shutdown()
            dead.server_close()


if __name__ == "__main__":
    unittest.main()
