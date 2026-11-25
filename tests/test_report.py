from __future__ import annotations

import copy
import os
import re
import tempfile
import unittest
from pathlib import Path

from ainews.report import (STATUS_LABELS, md_escape, md_url, render_html,
                           render_index, render_markdown, safe_url)

SAMPLE_PATH = Path(os.environ.get("AINEWS_SAMPLE_PATH")
                   or os.path.join(tempfile.gettempdir(), "ainews_sample_report.html"))

CATS = ["模型与研究", "产品与工具", "行业与政策", "待确认"]


def _src(sid, name, status, new=0, error=None):
    return {"source_id": sid, "source_name": name, "status": status,
            "window_display": "2026-11-25 09:00 SGT — 2026-11-26 09:00 SGT",
            "fetched_count": 20, "in_window_count": 5 if status != "failed" else 0,
            "relevant_count": 3 if status != "failed" else 0, "new_count": new, "error": error}


def _art(**kw):
    a = {"id": "x", "title": "T", "link": "https://example.com/a", "source_id": "s1",
         "source_name": "Source", "published_display": "2026-11-26 08:30 SGT",
         "date_status": "ok", "collected_display": "2026-11-26 09:00 SGT",
         "summary": "summary", "category": "模型与研究", "category_reason": "命中：大模型",
         "is_new": False}
    a.update(kw)
    return a


def make_report():
    run1 = {"run_id": "r1", "started_display": "2026-11-26 06:00 SGT",
            "finished_display": "2026-11-26 06:01 SGT",
            "window_display": "2026-11-23 09:00 SGT — 2026-11-26 06:00 SGT",
            "mode": "initial", "new_count": 2,
            "sources": [_src("s1", "机器之心", "ok_new", 2)]}
    run2 = {"run_id": "r2", "started_display": "2026-11-26 09:00 SGT",
            "finished_display": "2026-11-26 09:01 SGT",
            "window_display": "2026-11-26 06:00 SGT — 2026-11-26 09:00 SGT",
            "mode": "incremental", "new_count": 1,
            "sources": [
                _src("s1", "机器之心", "ok_new", 1),
                _src("s2", "TechCrunch AI", "ok_no_new"),
                _src("s3", "Bad <script>alert(2)</script> Feed", "failed",
                     error="HTTP 503: <b>Service Unavailable</b>"),
            ]}
    arts = {
        "模型与研究": [
            _art(id="a1", title="<script>alert(1)</script> GPT-5 发布",
                 link="https://example.com/a?x=1&y=2", is_new=True,
                 summary='Summary with " onmouseover="alert(3)" and | pipe'),
            _art(id="a2", title='Evil " onmouseover="alert(4)', link="javascript:alert(5)",
                 summary="", date_status="missing", published_display="日期缺失"),
        ],
        "产品与工具": [
            _art(id="a3", title="New [tool] (beta) *star* _under_ `code` <tag> | pipe",
                 link="https://example.com/path with space/(x)", source_name="TechCrunch AI",
                 date_status="unparseable", published_display="日期无法解析（原文：yesterday）",
                 summary="line1\nline2", category="产品与工具"),
        ],
        "行业与政策": [],
        "待确认": [_art(id="a4", title="", link=None, category="待确认")],
    }
    return {"report_date": "2026-11-26", "generated_at": "2026-11-26 09:01 SGT",
            "timezone": "Asia/Singapore", "categories": CATS,
            "articles_by_category": arts, "total_count": 4,
            "latest_run": run2, "runs": [run1, run2],
            "sources": [{"id": "s1", "name": "机器之心", "website": "https://jiqizhixin.com",
                         "rss_url": "https://jiqizhixin.com/rss", "language": "zh"}]}


def empty_report(all_failed: bool):
    r = make_report()
    r["articles_by_category"] = {c: [] for c in CATS}
    r["total_count"] = 0
    if all_failed:
        srcs = [_src("s1", "A", "failed", error="timeout"), _src("s2", "B", "failed", error="DNS")]
    else:
        srcs = [_src("s1", "A", "ok_no_new"), _src("s2", "B", "ok_no_new")]
    r["latest_run"] = dict(r["latest_run"], sources=srcs, new_count=0)
    r["runs"] = [r["latest_run"]]
    return r


def hrefs(doc):
    return re.findall(r'href="([^"]*)"', doc)


class HtmlTests(unittest.TestCase):
    def setUp(self):
        self.report = make_report()
        self.doc = render_html(self.report)

    def test_no_unescaped_script(self):
        self.assertNotIn("<script", self.doc.lower())
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", self.doc)

    def test_attribute_injection_escaped(self):
        self.assertNotIn('" onmouseover="', self.doc)
        self.assertIn("&quot; onmouseover=&quot;", self.doc)
        self.assertNotRegex(self.doc, r"<[^>]*\sonmouseover=")

    def test_javascript_not_href(self):
        for h in hrefs(self.doc):
            self.assertFalse(h.lower().startswith("javascript"), h)
        self.assertIn("链接无效", self.doc)

    def test_links_safe_attrs(self):
        anchors = re.findall(r"<a [^>]*>", self.doc)
        ext = [a for a in anchors if "http" in a]
        self.assertTrue(ext)
        for a in ext:
            self.assertIn('target="_blank"', a)
            self.assertIn('rel="noopener noreferrer"', a)
        self.assertIn('href="https://example.com/a?x=1&amp;y=2"', self.doc)

    def test_meta(self):
        self.assertIn("default-src 'none'; style-src 'unsafe-inline'; img-src 'none'", self.doc)
        self.assertIn('http-equiv="Content-Security-Policy"', self.doc)
        self.assertIn('<meta name="viewport"', self.doc)
        self.assertIn("prefers-color-scheme: dark", self.doc)

    def test_structure(self):
        self.assertIn("AI 行业新闻日报 · 2026-11-26", self.doc)
        for c in CATS:
            self.assertIn(f"<h2>{c} ", self.doc)
        self.assertIn("本类暂无条目", self.doc)
        for label in STATUS_LABELS.values():
            self.assertIn(label, self.doc)
        self.assertIn("HTTP 503: &lt;b&gt;Service Unavailable&lt;/b&gt;", self.doc)
        self.assertIn("本次新增", self.doc)
        self.assertIn("（RSS 未提供简介）", self.doc)
        self.assertIn("datewarn", self.doc)
        self.assertIn("<details>", self.doc)
        self.assertIn("内容仅基于 RSS 标题与简介，未阅读原文全文", self.doc)
        self.assertIn("2026-11-26 06:00 SGT — 2026-11-26 09:00 SGT", self.doc)

    def test_category_order(self):
        pos = [self.doc.index(f"<h2>{c} ") for c in CATS]
        self.assertEqual(pos, sorted(pos))

    def test_empty_all_failed(self):
        doc = render_html(empty_report(True))
        self.assertIn("所有来源读取失败", doc)
        self.assertEqual(doc.count("本类暂无条目"), 4)

    def test_empty_ok_no_new(self):
        doc = render_html(empty_report(False))
        self.assertIn("时间范围内没有 AI 相关的新内容", doc)
        self.assertNotIn("所有来源读取失败", doc)

    def test_does_not_mutate(self):
        before = copy.deepcopy(self.report)
        render_html(self.report)
        render_markdown(self.report)
        self.assertEqual(before, self.report)

    @unittest.skipUnless(os.environ.get("AINEWS_WRITE_SAMPLE") == "1", "set AINEWS_WRITE_SAMPLE=1")
    def test_write_sample(self):
        SAMPLE_PATH.parent.mkdir(parents=True, exist_ok=True)
        SAMPLE_PATH.write_text(self.doc, encoding="utf-8")
        self.assertTrue(SAMPLE_PATH.exists())


class MarkdownTests(unittest.TestCase):
    def setUp(self):
        self.md = render_markdown(make_report())

    def test_structure(self):
        self.assertIn("# AI 行业新闻日报 · 2026-11-26", self.md)
        for c in CATS:
            self.assertIn(f"## {c}（", self.md)
        self.assertIn("本类暂无条目", self.md)
        for label in STATUS_LABELS.values():
            self.assertIn(label, self.md)
        self.assertIn("未阅读原文全文", self.md)

    def test_links(self):
        self.assertIn("(https://example.com/a?x=1&y=2)", self.md)
        self.assertIn("(https://example.com/path%20with%20space/%28x%29)", self.md)
        self.assertNotIn("](javascript", self.md)
        self.assertIn("链接无效", self.md)

    def test_escaping(self):
        self.assertIn(r"New \[tool\] \(beta\) \*star\* \_under\_ \`code\` \<tag\> \| pipe", self.md)
        self.assertIn(r"\<script\>", self.md)
        self.assertNotIn("<script>", self.md)

    def test_table_pipe_escaped(self):
        rows = [l for l in self.md.splitlines() if "Bad" in l and l.startswith("|")]
        self.assertEqual(len(rows), 1)
        # 8 列 -> 9 个未转义竖线
        self.assertEqual(len(re.findall(r"(?<!\\)\|", rows[0])), 9)

    def test_summary_newline_collapsed(self):
        self.assertIn("- 简介：line1 line2", self.md)

    def test_empty_messages(self):
        self.assertIn("所有来源读取失败", render_markdown(empty_report(True)))
        self.assertIn("没有 AI 相关的新内容", render_markdown(empty_report(False)))


class HelperTests(unittest.TestCase):
    def test_safe_url(self):
        self.assertEqual(safe_url(" https://a.com/x "), "https://a.com/x")
        for bad in ["javascript:alert(1)", " JavaScript:alert(1)", "data:text/html,x",
                    "java\nscript:x", "//a.com", "http://", None, "", "ftp://a.com"]:
            self.assertIsNone(safe_url(bad), bad)

    def test_md_helpers(self):
        self.assertEqual(md_escape("a|b\nc"), r"a\|b c")
        self.assertEqual(md_url("https://a.com/(x y)"), "https://a.com/%28x%20y%29")
        self.assertIsNone(md_url("javascript:x"))


class IndexTests(unittest.TestCase):
    def test_index(self):
        entries = [
            {"date": "2026-11-24", "total_count": 3, "html": "2026-11-24.html", "md": "2026-11-24.md"},
            {"date": "2026-11-26", "total_count": 12, "html": "2026-11-26.html", "md": "2026-11-26.md"},
            {"date": "<script>x</script>", "total_count": 0, "html": "javascript:alert(1)",
             "md": "../etc"},
        ]
        doc = render_index(entries)
        self.assertNotIn("<script", doc.lower())
        self.assertIn('href="latest.html"', doc)
        self.assertIn("default-src 'none'; style-src 'unsafe-inline'; img-src 'none'", doc)
        self.assertIn('<meta name="viewport"', doc)
        self.assertLess(doc.index("2026-11-26.html"), doc.index("2026-11-24.html"))
        self.assertIn('href="2026-11-26.md"', doc)
        self.assertIn("12 条", doc)
        for h in hrefs(doc):
            self.assertNotIn("javascript", h)
            self.assertNotIn("..", h)

    def test_index_empty(self):
        self.assertIn("暂无历史日报", render_index([]))


if __name__ == "__main__":
    unittest.main()
