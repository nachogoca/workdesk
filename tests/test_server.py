import http.client
import json
import threading
import unittest

from workdesk.cli import Ctx
from workdesk.demo import FixtureHttp, DEMO_ENV
from workdesk.server import make_server, MAX_BODY
from tests.util import NOW, cfg, mem_db


class ServerCase(unittest.TestCase):
    def setUp(self):
        self.ctx = Ctx(cfg(jira__enabled=True, github__enabled=True, slack__enabled=True), mem_db(), dict(DEMO_ENV),
                       FixtureHttp(), NOW)
        self.srv = make_server(self.ctx, 0, clock=lambda: NOW)
        self.port = self.srv.server_address[1]
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.t.start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.t.join()

    def call(self, method, path, body=None, headers=None, raw=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        hdr = dict(headers or {})
        data = raw if raw is not None else (json.dumps(body) if body is not None else None)
        c.request(method, path, body=data, headers=hdr)
        r = c.getresponse()
        payload = r.read()
        c.close()
        try:
            return r.status, json.loads(payload)
        except ValueError:
            return r.status, payload


class ServerTests(ServerCase):
    def test_page_served_with_security_headers(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.request("GET", "/")
        r = c.getresponse()
        html = r.read().decode()
        self.assertEqual(r.status, 200)
        self.assertIn("Content-Security-Policy", dict(r.getheaders()))
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
        self.assertNotIn("innerHTML", html)
        self.assertNotIn("http://cdn", html)

    def test_sync_then_items(self):
        st, res = self.call("POST", "/api/sync", {})
        self.assertEqual(st, 200)
        self.assertTrue(res["results"]["jira"]["ok"])
        st, data = self.call("GET", "/api/items")
        self.assertEqual(len(data["items"]), 12)
        first = data["items"][0]
        for k in ("rank", "urgency_reason", "importance_reason", "quadrant", "alias", "pomo", "estimate_min"):
            self.assertIn(k, first)
        self.assertEqual(data["sources"]["jira"]["ok"], True)
        self.assertEqual(data["timer"]["running"], False)

    def test_add_state_override_estimate(self):
        st, r = self.call("POST", "/api/add", {"title": "Write docs", "due": "2026-01-16", "kind": "do"})
        self.assertEqual((st, r["alias"]), (200, "m1"))
        iid = r["id"]
        self.assertEqual(self.call("POST", "/api/override", {"id": iid, "field": "urgency", "value": 25})[0], 200)
        self.assertEqual(self.call("POST", "/api/estimate", {"id": iid, "minutes": 30})[0], 200)
        item = self.call("GET", "/api/items")[1]["items"][0]
        self.assertEqual((item["urgency"], item["estimate_min"]), (25, 30))
        self.assertEqual(self.call("POST", "/api/state", {"id": iid, "state": "snoozed", "days": 1})[0], 200)
        self.assertEqual(self.call("GET", "/api/items")[1]["items"], [])
        self.assertEqual(self.call("POST", "/api/state", {"id": iid, "state": "done"})[0], 200)
        self.assertEqual(self.ctx.db.get_item(iid)["state"], "done")

    def test_validation_errors(self):
        self.assertEqual(self.call("POST", "/api/add", {"title": ""})[0], 400)
        self.assertEqual(self.call("POST", "/api/add", {"title": "x", "url": "javascript:alert(1)"})[0], 400)
        self.assertEqual(self.call("POST", "/api/override", {"id": "nope", "field": "urgency", "value": 5})[0], 400)
        self.assertEqual(self.call("POST", "/api/override", {"id": "x", "field": "urgency", "value": 500})[0], 400)
        self.assertEqual(self.call("POST", "/api/add", raw="[1]")[0], 400)
        self.assertEqual(self.call("POST", "/api/add", raw="not json")[0], 400)
        self.assertEqual(self.call("GET", "/api/nothing")[0], 404)

    def test_host_check(self):
        self.assertEqual(self.call("GET", "/api/items", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.call("GET", "/api/items", headers={"Host": "evil.example:8765"})[0], 403)
        self.assertEqual(self.call("GET", "/api/items", headers={"Host": "localhost:1234"})[0], 200)
        self.assertEqual(self.call("GET", "/api/items", headers={"Host": "127.0.0.1"})[0], 200)

    def test_origin_check(self):
        body = {"title": "x"}
        self.assertEqual(self.call("POST", "/api/add", body, {"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.call("POST", "/api/add", body, {"Origin": f"http://127.0.0.1:{self.port + 1}"})[0], 403)
        self.assertEqual(self.call("POST", "/api/add", body, {"Origin": f"http://127.0.0.1:{self.port}"})[0], 200)
        self.assertEqual(self.call("POST", "/api/add", body, {"Origin": f"http://localhost:{self.port}"})[0], 200)
        self.assertEqual(self.call("POST", "/api/add", body)[0], 200)  # no Origin header: allowed

    def test_body_limit(self):
        big = json.dumps({"title": "x" * (MAX_BODY + 10)})
        self.assertEqual(self.call("POST", "/api/add", raw=big)[0], 413)


if __name__ == "__main__":
    unittest.main()
