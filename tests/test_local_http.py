from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from local_http import LocalHTTPClient, UnsafeLocalRequest, local_origin


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.respond()

    def do_POST(self):
        self.respond()

    def respond(self):
        size = int(self.headers.get("Content-Length", 0))
        self.server.seen.append({
            "path": self.path, "auth": self.headers.get("Authorization"),
            "cookie": self.headers.get("Cookie"), "crumb": self.headers.get("X-Test-Crumb"),
            "body": self.rfile.read(size),
        })
        if self.path in self.server.redirects:
            code, location = self.server.redirects[self.path]
            self.send_response(code)
            self.send_header("Location", location)
            self.send_header("Set-Cookie", "unit_session=dummy; Path=/")
            self.end_headers()
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

    def log_message(self, *args):
        pass


class LocalHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.servers = [ThreadingHTTPServer(("127.0.0.1", 0), Handler) for _ in range(2)]
        cls.threads = []
        for server in cls.servers:
            server.seen = []
            server.redirects = {}
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            cls.threads.append(thread)
        cls.origin, cls.sink = cls.servers
        cls.url = f"http://127.0.0.1:{cls.origin.server_port}"
        cls.other = f"http://127.0.0.1:{cls.sink.server_port}"

    @classmethod
    def tearDownClass(cls):
        for server in cls.servers:
            server.shutdown()
            server.server_close()
        for thread in cls.threads:
            thread.join()

    def setUp(self):
        for server in self.servers:
            server.seen.clear()
            server.redirects.clear()

    def client(self, cookies=False):
        return LocalHTTPClient(self.url, username="unit-user", password="dummy-password", cookies=cookies)

    def test_authenticated_same_origin_request(self):
        with self.client().open(self.url + "/ok") as response:
            self.assertTrue(json.load(response)["ok"])
        self.assertTrue(self.origin.seen[-1]["auth"].startswith("Basic "))
        self.assertEqual(self.sink.seen, [])

    def test_same_origin_redirect_retains_auth_and_session(self):
        self.origin.redirects["/redirect"] = (302, self.url + "/final")
        with self.client(cookies=True).open(self.url + "/redirect") as response:
            self.assertEqual(response.status, 200)
        self.assertEqual([item["path"] for item in self.origin.seen], ["/redirect", "/final"])
        self.assertEqual(self.origin.seen[0]["auth"], self.origin.seen[1]["auth"])
        self.assertIn("unit_session=dummy", self.origin.seen[1]["cookie"])

    def test_cross_port_redirects_send_nothing_to_destination(self):
        for code in (301, 302, 303, 307, 308):
            with self.subTest(code=code):
                self.origin.redirects["/redirect"] = (code, self.other + "/sink")
                with self.assertRaises(UnsafeLocalRequest):
                    self.client(cookies=True).open(self.url + "/redirect")
                self.assertEqual(self.sink.seen, [])

    def test_cross_host_and_scheme_redirects_are_blocked(self):
        destinations = [
            f"http://localhost:{self.origin.server_port}/sink",
            "http://example.invalid/sink",
            f"https://127.0.0.1:{self.origin.server_port}/sink",
            f"http://dummy-user:dummy-password@127.0.0.1:{self.origin.server_port}/sink",
        ]
        for destination in destinations:
            with self.subTest(destination=destination):
                self.origin.redirects["/redirect"] = (302, destination)
                with self.assertRaises(UnsafeLocalRequest) as error:
                    self.client().open(self.url + "/redirect")
                self.assertNotIn("dummy-password", str(error.exception))
                self.assertEqual(self.sink.seen, [])

    def test_post_body_and_crumb_cannot_leave_origin(self):
        self.origin.redirects["/redirect"] = (302, self.other + "/sink")
        request = urllib.request.Request(
            self.url + "/redirect", data=b"dummy-request-body",
            headers={"X-Test-Crumb": "dummy-crumb"},
        )
        with self.assertRaises(UnsafeLocalRequest):
            self.client(cookies=True).open(request)
        self.assertEqual(self.origin.seen[-1]["crumb"], "dummy-crumb")
        self.assertEqual(self.sink.seen, [])

    def test_direct_cross_origin_requests_are_rejected_before_network(self):
        for request in (self.other + "/sink", urllib.request.Request(self.other + "/sink", data=b"dummy")):
            with self.assertRaises(UnsafeLocalRequest):
                self.client().open(request)
        self.assertEqual(self.origin.seen, [])
        self.assertEqual(self.sink.seen, [])

    def test_invalid_urls_and_base_paths_are_rejected(self):
        for url in [
            "https://127.0.0.1", "file:///etc/hosts", "http://example.invalid",
            "http://127.0.0.1:0", "http://127.0.0.1:70000",
            "http://@127.0.0.1", "http://user:dummy-password@127.0.0.1",
            "http://127.0.0.1/path", "http://127.0.0.1?query=1",
            "http://127.0.0.1#fragment", "http://127.0.0.1/\r\nheader",
        ]:
            with self.subTest(url=url), self.assertRaises(UnsafeLocalRequest):
                LocalHTTPClient(url)
        self.assertEqual(local_origin("http://localhost"), local_origin("http://LOCALHOST:80/"))

    def test_environment_proxy_configuration_is_not_used(self):
        with mock.patch("urllib.request.getproxies", side_effect=AssertionError("Proxy lookup is forbidden")):
            with self.client().open(self.url + "/ok") as response:
                self.assertEqual(response.status, 200)
        self.assertEqual(self.sink.seen, [])

    def test_jenkins_helper_uses_scoped_client(self):
        spec = importlib.util.spec_from_file_location("jenkins_client_test", ROOT / "scripts/jenkins-client.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix="local-http-test-") as temporary:
            state = Path(temporary)
            (state / "admin_password").write_text("dummy-password")
            with mock.patch.object(module, "STATE", state), mock.patch.object(module, "BASE", self.url):
                self.origin.redirects["/redirect"] = (302, self.other + "/sink")
                with self.assertRaises(UnsafeLocalRequest):
                    module.request(module.client(), "/redirect")
        self.assertEqual(self.sink.seen, [])
