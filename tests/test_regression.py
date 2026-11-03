"""Regression: the old entry points (web UI, Telegram bot, old config, .env loading) still work."""

import base64
import http.client
import io
import json
import os
import threading
import tomllib
import urllib.parse
from http.server import ThreadingHTTPServer

from tests.helpers import PROJECT, FixtureCase, scripted


class WebTests(FixtureCase):
    def setUp(self):
        super().setUp()
        import web
        self.web = web
        self.cwd = os.getcwd()
        os.chdir(self.tmp)                         # web serves reviews/ relative to the working folder
        os.environ["APP_PASSWORD"] = "test-pass"
        web.AGENT = self.make_agent()
        web.CFG = self.cfg
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        os.chdir(self.cwd)
        self.web.AGENT = None
        super().tearDown()

    def req(self, method, path, body=None, cookie=None, form=False):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        headers = {"Cookie": cookie} if cookie else {}
        data = None
        if body is not None:
            data = urllib.parse.urlencode(body) if form else json.dumps(body)
            headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
        c.request(method, path, body=data, headers=headers)
        r = c.getresponse()
        raw = r.read()
        return r.status, r.getheader("Set-Cookie"), raw

    def login(self):
        status, cookie, _ = self.req("POST", "/login", {"password": "test-pass"}, form=True)
        self.assertEqual(status, 303)
        return cookie.split(";")[0]

    def test_auth_and_pages(self):
        self.assertEqual(self.req("GET", "/api/reviews")[0], 401)
        self.assertEqual(self.req("POST", "/login", {"password": "wrong"}, form=True)[0], 401)
        ck = self.login()
        status, _, page = self.req("GET", "/", cookie=ck)
        self.assertEqual(status, 200)
        for word in ("复盘 Review", "学习 Study", "决策 Decision", "记忆 Memory"):
            self.assertIn(word, page.decode())
        self.assertEqual(self.req("GET", "/api/reviews", cookie=ck)[0], 200)

    def test_review_explain_study_memory_over_http(self):
        ck = self.login()
        with open(self.chart, "rb") as f:
            img = "data:image/png;base64," + base64.b64encode(f.read()).decode()
        status, _, raw = self.req("POST", "/api/review", {"image": img, "note": "MYM 5分钟"}, cookie=ck)
        self.assertEqual(status, 200, raw)
        meta = json.loads(raw)["meta"]
        self.assertEqual(meta["annotations"][0]["label"], "#1")
        status, _, png = self.req("GET", f"/reviews/{meta['id']}/review.png", cookie=ck)
        self.assertEqual((status, png[:4]), (200, b"\x89PNG"))
        status, _, raw = self.req("POST", "/api/explain", {"case_id": meta["id"], "annotation_id": "H2_001",
                                                           "text": "为什么是 H2？"}, cookie=ck)
        self.assertEqual(status, 200, raw)
        self.assertIn("1. 含义", json.loads(raw)["text"])
        status, _, raw = self.req("POST", "/api/discuss", {"case_id": meta["id"], "text": "整体逻辑是什么？"}, cookie=ck)
        self.assertEqual(status, 200, raw)
        self.assertIn("我的推理过程", json.loads(raw)["text"])
        case = json.loads(self.req("GET", f"/api/case/{meta['id']}", cookie=ck)[2])
        self.assertGreaterEqual(len(case["discussion"]), 4)
        self.assertIn("current_view", case)
        status, _, raw = self.req("POST", "/api/study", {"text": "解释 H2", "depth": "brief"}, cookie=ck)
        self.assertEqual(status, 200, raw)
        self.assertIn("[BROOKS SOURCE]", json.loads(raw)["text"])
        hist = json.loads(self.req("GET", "/api/study/history", cookie=ck)[2])["messages"]
        self.assertEqual(hist[-2]["content"], "解释 H2")
        mem = json.loads(self.req("GET", "/api/memory", cookie=ck)[2])
        cid = mem["candidates"][0]["id"]
        self.assertEqual(self.req("POST", f"/api/memory/candidate/{cid}/approve", {}, cookie=ck)[0], 200)
        self.assertEqual(json.loads(self.req("GET", "/api/memory", cookie=ck)[2])["profile"][0]["confirmed_by"], "web-user")
        self.assertEqual(self.req("POST", "/api/memory/rule", {"rule": "先背景后形态"}, cookie=ck)[0], 200)
        case = json.loads(self.req("GET", f"/api/case/{meta['id']}", cookie=ck)[2])
        self.assertEqual(case["case_id"], meta["id"])
        self.assertEqual(self.req("GET", "/api/case/../../etc", cookie=ck)[0], 404)


class FakeTelegram:
    def __init__(self, png: bytes):
        self.png, self.sent, self.photos = png, [], []

    def send(self, text):
        self.sent.append(text)

    def send_photo(self, data, caption="", as_file=False):
        self.photos.append((caption, as_file))

    def download(self, file_id):
        return self.png

    def incoming(self):
        return []


class TelegramTests(FixtureCase):
    def setUp(self):
        super().setUp()
        self.cwd = os.getcwd()
        os.chdir(self.tmp)                         # Bot keeps news_alerts.db in the working folder
        os.environ.setdefault("OPENAI_API_KEY", "sk-test-dummy")

    def tearDown(self):
        self.bot.store.db.close()
        os.chdir(self.cwd)
        super().tearDown()

    def test_photo_review_commands_and_news_text(self):
        from main import Bot
        with open(self.chart, "rb") as f:
            tg = FakeTelegram(f.read())
        self.bot = Bot(self.cfg, tg, agent=self.make_agent())
        self.bot.handle_incoming({"image": "file1", "text": "MYM 5分钟，我在高点做空"})
        self.assertEqual([p[1] for p in tg.photos], [False, True])        # photo + full-resolution file
        self.assertIn("复盘分析", tg.sent[-1])
        self.bot.handle_incoming({"text": "/explain #1 为什么是 H2？"})
        self.assertIn("含义", tg.sent[-1])
        self.bot.handle_incoming({"text": "/study 解释 H2"})
        self.assertIn("BROOKS SOURCE", tg.sent[-1])
        self.bot.handle_incoming({"text": "/memory"})
        self.assertIn("候选", tg.sent[-1])
        self.bot.handle_incoming({"image": "file1", "text": "/decide"})
        self.assertIn("BIAS", tg.sent[-1])
        called = {}
        self.bot.check_text = lambda text: called.setdefault("news", text)    # plain text still = news check
        self.bot.handle_incoming({"text": "Fed cuts rates by 50bp"})
        self.assertEqual(called["news"], "Fed cuts rates by 50bp")


class CompatibilityTests(FixtureCase):
    def test_old_config_without_coach_keys_still_reviews(self):
        old_cfg_path = os.path.join(PROJECT, "backups", "pre_coach_upgrade_20261102", "config.toml")
        if os.path.isfile(old_cfg_path):
            with open(old_cfg_path, "rb") as f:
                cfg = tomllib.load(f)
        else:
            cfg = {"review_model": "gpt-4o", "review_refine_passes": 1, "docs_folder": "docs"}
        cfg.update(docs_folder=os.path.join(self.tmp, "notes"), knowledge_base_folder="", review_refine_passes=0)
        cfg.pop("canonical_store", None)
        agent = self.make_agent(cfg=cfg)
        self.assertIsNone(agent.kb)                 # no Brooks store configured: still works
        out = agent.handle({"image_path": self.chart, "text": "MYM"})
        self.assertTrue(out["ok"], out.get("error"))
        out = agent.handle({"text": "解释 H2"})
        self.assertTrue(out["ok"], out.get("error"))
        self.assertNotIn("[BROOKS SOURCE]", out["text"])   # without sources nothing may claim Brooks support

    def test_chart_reviewer_legacy_api(self):
        from PIL import Image
        import chart_review as cr
        img, spec = cr.ChartReviewer({**self.cfg, "review_refine_passes": 1}, llm=scripted()).review(Image.open(self.chart))
        self.assertEqual(spec["annotations"][0]["annotation_id"], "H2_001")
        self.assertEqual(img.size, cr.prepare(Image.open(self.chart)).size)

    def test_load_env_replaces_placeholder_variables_only(self):
        from main import load_env
        path = os.path.join(self.tmp, ".env")
        with open(path, "w", encoding="utf-8") as f:
            f.write("TEST_KEY_A=real-value-123\nTEST_KEY_B=from-file\n")
        os.environ["TEST_KEY_A"] = "x"              # leftover 1-character placeholder
        os.environ["TEST_KEY_B"] = "set-by-user"    # a real value wins over .env
        try:
            load_env(path)
            self.assertEqual(os.environ["TEST_KEY_A"], "real-value-123")
            self.assertEqual(os.environ["TEST_KEY_B"], "set-by-user")
        finally:
            os.environ.pop("TEST_KEY_A", None)
            os.environ.pop("TEST_KEY_B", None)
