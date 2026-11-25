"""fetcher 测试。模拟测试默认运行；真实网络测试仅在 AINEWS_LIVE=1 时运行。"""
from __future__ import annotations

import gzip
import json
import os
import socket
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from ainews import fetcher
from ainews.fetcher import fetch_feed, filter_by_window, parse_date, parse_feed

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / "fixtures"


def _load(name: str) -> bytes:
    return (FIX / name).read_bytes()


class ParseRssTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items, cls.invalid = parse_feed(_load("rss_sample.xml"))
        cls.by_link = {i["link"] or i["guid"]: i for i in cls.items}

    def test_counts(self):
        self.assertEqual(len(self.items), 5)
        self.assertEqual(self.invalid, 2)

    def test_keys(self):
        keys = {"guid", "title", "link", "summary", "published_raw", "published_at", "date_status"}
        for it in self.items:
            self.assertEqual(set(it), keys)

    def test_title_entities_and_guid_vs_link(self):
        it = self.items[0]
        self.assertEqual(it["title"], "大模型发布：GPT & Claude 对比")  # <深度> 被当作标签去除
        self.assertEqual(it["guid"], "example-1001")
        self.assertEqual(it["link"], "https://example.com/articles/1001?utm=rss")

    def test_summary_html_stripped_script_removed(self):
        s = self.items[0]["summary"]
        self.assertEqual(s, "这是 简介 第一段。 Tom & Jerry 用 AI")
        self.assertNotIn("alert", s)
        self.assertNotIn("<", s)

    def test_escaped_html_summary(self):
        it = self.by_link["https://example.com/articles/1003"]
        self.assertEqual(it["summary"], "Hello & world")

    def test_rfc822_date(self):
        it = self.items[0]
        self.assertEqual(it["published_raw"], "Wed, 25 Nov 2026 16:34:09 +0000")
        self.assertEqual(it["published_at"], "2026-11-25T16:34:09+00:00")
        self.assertEqual(it["date_status"], "ok")

    def test_missing_date(self):
        it = self.by_link["https://example.com/articles/1002"]
        self.assertEqual(it["date_status"], "missing")
        self.assertIsNone(it["published_at"])
        self.assertIsNone(it["published_raw"])

    def test_unparseable_date(self):
        it = self.by_link["https://example.com/articles/1003"]
        self.assertEqual(it["date_status"], "unparseable")
        self.assertIsNone(it["published_at"])
        self.assertEqual(it["published_raw"], "昨天下午")

    def test_naive_dc_date_as_utc_and_content_encoded(self):
        it = self.by_link["https://example.com/articles/1004"]
        self.assertEqual(it["published_at"], "2026-11-25T10:00:00+00:00")
        self.assertEqual(it["summary"], "来自 content:encoded 的 正文")
        self.assertIsNone(it["guid"])

    def test_guid_only_item_valid(self):
        it = self.by_link["example-1005"]
        self.assertIsNone(it["link"])
        self.assertEqual(it["summary"], "")
        self.assertEqual(it["published_at"], "2026-11-24T13:00:00+00:00")


class ParseAtomTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items, cls.invalid = parse_feed(_load("atom_sample.xml"))

    def test_counts(self):
        self.assertEqual(len(self.items), 4)
        self.assertEqual(self.invalid, 1)

    def test_alternate_link_and_published_millis(self):
        it = self.items[0]
        self.assertEqual(it["link"], "https://example.org/posts/1")
        self.assertEqual(it["guid"], "urn:uuid:aaaa-0001")
        self.assertEqual(it["title"], "OpenAI & 微软")
        self.assertEqual(it["published_raw"], "2026-11-25T08:30:00.123Z")  # published 优先于 updated
        self.assertEqual(it["published_at"], "2026-11-25T08:30:00+00:00")
        self.assertEqual(it["summary"], "摘要 A & B")

    def test_updated_offset_and_content(self):
        it = self.items[1]
        self.assertEqual(it["link"], "https://example.org/posts/2")
        self.assertEqual(it["published_at"], "2026-11-25T17:44:51+00:00")
        self.assertEqual(it["summary"], "Content used as summary")

    def test_first_href_fallback_and_missing(self):
        it = self.items[2]
        self.assertEqual(it["link"], "https://example.org/posts/3")
        self.assertEqual(it["date_status"], "missing")

    def test_bad_date(self):
        self.assertEqual(self.items[3]["date_status"], "unparseable")
        self.assertEqual(self.items[3]["published_raw"], "not a date")


class ParseMiscTest(unittest.TestCase):
    def test_summary_truncated_500(self):
        body = "字" * 600
        xml = ("<rss version='2.0'><channel><item><title>t</title><link>https://a/b</link>"
               "<description>%s</description></item></channel></rss>" % body).encode("utf-8")
        items, _ = parse_feed(xml)
        self.assertEqual(len(items[0]["summary"]), 501)
        self.assertTrue(items[0]["summary"].endswith("…"))

    def test_empty_title_with_link_valid(self):
        xml = b"<rss version='2.0'><channel><item><link>https://a/b</link></item></channel></rss>"
        items, inv = parse_feed(xml)
        self.assertEqual((len(items), inv, items[0]["title"]), (1, 0, ""))

    def test_rejects_html_and_malformed_and_doctype(self):
        with self.assertRaises(ValueError):
            parse_feed(_load("not_a_feed.html"))
        with self.assertRaises(Exception):
            parse_feed(_load("malformed.xml"))
        bomb = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss><channel></channel></rss>'
        with self.assertRaises(ValueError):
            parse_feed(bomb)
        with self.assertRaises(ValueError):
            parse_feed(b"<html><body/></html>")

    def test_parse_date_variants(self):
        cases = {
            "Wed, 25 Nov 2026 18:08:53 GMT": "2026-11-25T18:08:53+00:00",
            "Wed, 25 Nov 2026 10:00:00 +0800": "2026-11-25T02:00:00+00:00",
            "2026-11-25T17:44:57Z": "2026-11-25T17:44:57+00:00",
            "2026-11-25T17:44:57.999Z": "2026-11-25T17:44:57+00:00",
            "2026-11-25T12:44:57-05:00": "2026-11-25T17:44:57+00:00",
            "2026-11-25T12:44:57+0800": "2026-11-25T04:44:57+00:00",
            "2026-11-25 10:00:00": "2026-11-25T10:00:00+00:00",
            "2026-11-25": "2026-11-25T00:00:00+00:00",
        }
        for raw, want in cases.items():
            self.assertEqual(parse_date(raw), (want, "ok"), raw)
        self.assertEqual(parse_date(None), (None, "missing"))
        self.assertEqual(parse_date("   "), (None, "missing"))
        self.assertEqual(parse_date("2026-13-45T00:00:00Z"), (None, "unparseable"))
        self.assertEqual(parse_date("garbage"), (None, "unparseable"))


class WindowTest(unittest.TestCase):
    START = datetime(2026, 11, 23, 1, 0, 0, tzinfo=timezone.utc)
    END = datetime(2026, 11, 26, 1, 0, 0, tzinfo=timezone.utc)

    @staticmethod
    def _it(name, dt=None, status="ok"):
        return {"guid": name, "title": name, "link": None, "summary": "",
                "published_raw": None, "published_at": dt.isoformat() if dt else None,
                "date_status": status}

    def test_boundaries(self):
        s, e, one = self.START, self.END, timedelta(seconds=1)
        items = [
            self._it("eq_start", s), self._it("eq_end", e),
            self._it("before", s - one), self._it("after", e + one),
            self._it("mid", s + timedelta(hours=1)),
            self._it("missing", None, "missing"), self._it("bad", None, "unparseable"),
        ]
        kept, stats = filter_by_window(items, s, e)
        self.assertEqual([k["guid"] for k in kept], ["eq_start", "eq_end", "mid", "missing", "bad"])
        self.assertEqual(stats, {"total": 7, "in_window": 3, "out_of_window": 2, "no_date_kept": 2})

    def test_with_parsed_feed(self):
        items, _ = parse_feed(_load("rss_sample.xml"))
        start = datetime(2026, 11, 25, 0, 0, tzinfo=timezone.utc)
        kept, stats = filter_by_window(items, start, start + timedelta(days=1))
        self.assertEqual(stats, {"total": 5, "in_window": 2, "out_of_window": 1, "no_date_kept": 2})
        self.assertEqual(len(kept), 4)


# ------------------------------------------------------------ 本地 HTTP 模拟

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = _load("rss_sample.xml")
        if self.path == "/gzip":
            data = gzip.compress(body)
            self.send_response(200)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/plain")
            self.end_headers()
        elif self.path == "/plain":
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/ua":
            ua = self.headers.get("User-Agent", "").encode()
            xml = b"<rss version='2.0'><channel><item><title>" + ua + b"</title><link>https://a/b</link></item></channel></rss>"
            self.send_response(200)
            self.end_headers()
            self.wfile.write(xml)
        elif self.path == "/html":
            data = _load("not_a_feed.html")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(data)
        elif self.path == "/huge":
            self.send_response(200)
            self.send_header("Content-Length", str(fetcher.MAX_BYTES + 1))
            self.end_headers()
        else:
            self.send_response(500)
            self.end_headers()


class FetchLocalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), _Handler)
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        cls.th = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.th.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def _f(self, path):
        return fetch_feed({"id": "local", "rss_url": self.base + path}, timeout=5)

    def test_gzip(self):
        r = self._f("/gzip")
        self.assertTrue(r["ok"], r["error"])
        self.assertEqual((r["http_status"], len(r["items"]), r["invalid_count"]), (200, 5, 2))
        self.assertEqual(r["source_id"], "local")

    def test_redirect(self):
        r = self._f("/redirect")
        self.assertTrue(r["ok"], r["error"])
        self.assertEqual(len(r["items"]), 5)

    def test_user_agent(self):
        r = self._f("/ua")
        self.assertEqual(r["items"][0]["title"], fetcher.USER_AGENT)

    def test_http_500(self):
        r = self._f("/err")
        self.assertFalse(r["ok"])
        self.assertEqual(r["http_status"], 500)
        self.assertIn("500", r["error"])
        self.assertEqual(r["items"], [])

    def test_html_page(self):
        r = self._f("/html")
        self.assertFalse(r["ok"])
        self.assertEqual(r["http_status"], 200)
        self.assertTrue(r["error"])

    def test_too_large(self):
        r = self._f("/huge")
        self.assertFalse(r["ok"])
        self.assertIn("10MB", r["error"])


class FetchFailureTest(unittest.TestCase):
    def _assert_failed(self, r):
        self.assertFalse(r["ok"])
        self.assertEqual(r["items"], [])
        self.assertEqual(r["invalid_count"], 0)
        self.assertTrue(r["error"])
        self.assertTrue(r["fetched_at"].endswith("+00:00"))

    def test_nonexistent_domain(self):
        r = fetch_feed({"id": "x", "rss_url": "https://no-such-host.invalid/feed"}, timeout=5)
        self._assert_failed(r)
        self.assertIsNone(r["http_status"])

    def test_closed_port(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        r = fetch_feed({"id": "x", "rss_url": "http://127.0.0.1:%d/feed" % port}, timeout=5)
        self._assert_failed(r)

    def test_bad_url_and_bad_source(self):
        self._assert_failed(fetch_feed({"id": "x", "rss_url": "file:///etc/passwd"}))
        self._assert_failed(fetch_feed({"id": "x"}))
        self._assert_failed(fetch_feed(None))


class SourcesConfigTest(unittest.TestCase):
    def test_sources_json(self):
        data = json.loads((ROOT / "config" / "sources.json").read_text(encoding="utf-8"))
        srcs = data["sources"]
        self.assertEqual(len(srcs), 2)
        need = {"id", "name", "website", "rss_url", "language", "focus", "reason", "verified_at", "verify_result"}
        for s in srcs:
            self.assertTrue(need <= set(s), s)
            self.assertTrue(s["rss_url"].startswith("https://"))
            self.assertIn(s["language"], ("zh", "en"))
        self.assertEqual(len({s["id"] for s in srcs}), 2)


@unittest.skipUnless(os.environ.get("AINEWS_LIVE") == "1", "设置 AINEWS_LIVE=1 才运行真实网络测试")
class LiveFetchTest(unittest.TestCase):
    def test_live_sources(self):
        data = json.loads((ROOT / "config" / "sources.json").read_text(encoding="utf-8"))
        for src in data["sources"]:
            with self.subTest(source=src["id"]):
                r = fetch_feed(src)
                dated = sorted((i["published_at"] for i in r["items"] if i["published_at"]), reverse=True)
                print("\n[LIVE] %s ok=%s http=%s items=%d invalid=%d latest=%s err=%s" % (
                    src["id"], r["ok"], r["http_status"], len(r["items"]), r["invalid_count"],
                    dated[0] if dated else None, r["error"]))
                self.assertTrue(r["ok"], r["error"])
                self.assertGreater(len(r["items"]), 0)


if __name__ == "__main__":
    unittest.main()
