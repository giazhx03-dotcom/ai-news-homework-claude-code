"""主控模块测试：去重、分类、持久化、增量、失败恢复、日报（全部为模拟数据，不联网）。"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ainews import classify, dedupe, pipeline
from ainews.storage import Store
from ainews.timeutil import iso_utc, parse_iso

NOW = datetime(2026, 11, 26, 1, 0, 0, tzinfo=timezone.utc)  # 新加坡 09:00

SOURCES = [
    {"id": "src_a", "name": "来源A", "website": "https://a.example", "rss_url": "https://a.example/feed", "language": "en"},
    {"id": "src_b", "name": "来源B", "website": "https://b.example", "rss_url": "https://b.example/feed", "language": "zh"},
]


def item(guid, title, link, hours_ago=None, summary="", raw=None, status=None, base=NOW):
    pub = base - timedelta(hours=hours_ago) if hours_ago is not None else None
    return {
        "guid": guid, "title": title, "link": link, "summary": summary,
        "published_raw": raw if raw is not None else (pub.isoformat() if pub else None),
        "published_at": iso_utc(pub) if pub else None,
        "date_status": status or ("ok" if pub else "missing"),
    }


class FakeFeeds:
    """可编程的 fetch：feeds[source_id] = 条目列表 或 Exception/错误字符串。"""

    def __init__(self):
        self.feeds = {}
        self.calls = []

    def __call__(self, src):
        self.calls.append(src["id"])
        f = self.feeds.get(src["id"], [])
        if isinstance(f, str):
            return {"source_id": src["id"], "ok": False, "error": f, "http_status": 503, "items": [],
                    "invalid_count": 0, "fetched_at": iso_utc(NOW)}
        if isinstance(f, Exception):
            raise f
        return {"source_id": src["id"], "ok": True, "error": None, "http_status": 200, "items": list(f),
                "invalid_count": 0, "fetched_at": iso_utc(NOW)}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ainews_test_"))
        self.fake = FakeFeeds()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_once(self, now=NOW, **kw):
        return pipeline.run(base=self.tmp, sources=SOURCES, fetch=self.fake, now=now, **kw)

    def src(self, rec, sid):
        return next(s for s in rec["sources"] if s["source_id"] == sid)

    def store(self):
        return Store(self.tmp / "data" / "news.db")


class TestClassify(unittest.TestCase):
    def test_rule_examples(self):
        for ex in classify.load_rules().examples:
            v = classify.assess(ex["title"])
            self.assertEqual(v["relevance"] != "no", ex["expect_relevant"], ex["title"])
            if ex["expect_relevant"]:
                self.assertEqual(v["category"], ex["expect_category"], ex["title"])

    def test_word_boundary(self):
        # 仅因字母 ai 出现不收录
        for t in ["Thailand said rain will continue", "Mainland retail sales rise", "Dubai airport expands",
                  "Claim your daily bonus"]:
            self.assertEqual(classify.assess(t)["relevance"], "no", t)
        # 中文紧邻 AI 仍可识别
        self.assertNotEqual(classify.assess("百度发布AI搜索新功能，向所有用户开放")["relevance"], "no")
        self.assertEqual(classify.assess("百度发布AI搜索新功能，向所有用户开放")["category"], "产品与工具")

    def test_four_categories(self):
        cases = {
            "DeepSeek open-sources new reasoning model weights and paper": "模型与研究",
            "Google rolls out Gemini AI features to Gmail users": "产品与工具",
            "英伟达AI芯片出口管制新规发布，影响数据中心市场": "行业与政策",
            "OpenAI 年度回顾": "待确认",
        }
        for t, c in cases.items():
            self.assertEqual(classify.assess(t)["category"], c, (t, classify.assess(t)))

    def test_off_topic_ai_writing_excluded(self):
        self.assertEqual(classify.assess("某明星演唱会新闻稿由AI生成的配图引热议")["relevance"], "no")
        self.assertEqual(classify.assess("World Cup recap: AI-generated match report goes viral")["relevance"], "no")

    def test_ambiguous_names(self):
        self.assertEqual(classify.assess("Claude Monet painting sells for $50 million")["relevance"], "no")
        self.assertEqual(classify.assess("Llama farm opens to visitors")["relevance"], "no")
        self.assertEqual(classify.assess("claude update for developers")["relevance"], "no")  # 小写不算产品名
        self.assertEqual(classify.assess("Anthropic ships Claude update")["relevance"], "yes")

    def test_context_only_excluded(self):
        for t in ["新款手机芯片参数曝光", "Robot vacuum review: training tips", "推理小说新作上市，开源出版模式受关注"]:
            self.assertEqual(classify.assess(t)["relevance"], "no", t)

    def test_weak_signal_pending(self):
        v = classify.assess("周末市场速览", "文中提到部分公司在使用 AI 工具和新的模型")
        self.assertEqual(v["category"], "待确认")


class TestDedupe(unittest.TestCase):
    def test_normalize(self):
        a = dedupe.normalize_url("http://WWW.Example.com/News/1/?utm_source=x&b=2&a=1#frag")
        b = dedupe.normalize_url("https://example.com/News/1?a=1&b=2")
        self.assertEqual(a, b)
        self.assertIsNone(dedupe.normalize_url("javascript:alert(1)"))
        self.assertIsNone(dedupe.normalize_url(None))

    def test_keys(self):
        k = dedupe.dedupe_keys("s", "id-1", "https://x.com/a")
        self.assertEqual(k, ["guid:s:id-1", "url:https://x.com/a"])
        self.assertEqual(dedupe.dedupe_keys("s", None, None), [])


class TestPipeline(Base):
    def test_initial_72h_and_boundaries(self):
        self.fake.feeds["src_a"] = [
            item("a1", "OpenAI releases new model", "https://a.example/1", 1),
            item("a2", "Anthropic Claude update for developers", "https://a.example/2", 72),      # 恰好边界：保留
            item("a3", "Nvidia AI chip sales surge", "https://a.example/3", 72 + 1 / 3600),      # 超出 1 秒：排除
            item("a4", "Mistral AI raises funding", "https://a.example/4", -0.01),               # 未来时间：排除
        ]
        rec = self.run_once()
        s = self.src(rec, "src_a")
        self.assertEqual(s["mode"], "initial")
        self.assertEqual(parse_iso(s["window_start"]), NOW - timedelta(hours=72))
        self.assertEqual(parse_iso(s["window_end"]), NOW)
        self.assertEqual(s["in_window_count"], 2)
        self.assertEqual(s["new_count"], 2)
        self.assertEqual(s["status"], "ok_new")
        self.assertEqual(self.src(rec, "src_b")["status"], "ok_no_new")
        st = self.store().get_state("src_a")
        self.assertEqual(parse_iso(st["last_window_end"]), NOW)

    def test_repeat_run_no_duplicates_and_same_day_keeps_report(self):
        self.fake.feeds["src_a"] = [item("a1", "OpenAI releases GPT-5.5 model", "https://a.example/1", 2)]
        r1 = self.run_once()
        self.assertEqual(r1["new_count"], 1)
        # 同一链接换了 guid 与追踪参数、另一来源同链接：均视为重复
        self.fake.feeds["src_a"] = [item("a1-changed", "OpenAI releases GPT-5.5 model", "http://a.example/1/?utm_source=rss", 2)]
        self.fake.feeds["src_b"] = [item("b-x", "OpenAI 发布 GPT-5.5 模型", "https://a.example/1", 2)]
        r2 = self.run_once(now=NOW + timedelta(hours=2))
        self.assertEqual(r2["new_count"], 0)
        self.assertEqual(self.src(r2, "src_a")["duplicate_count"], 1)
        self.assertEqual(self.src(r2, "src_a")["status"], "ok_no_new")
        self.assertEqual(self.src(r2, "src_a")["mode"], "incremental")
        # 同日第二次运行后，日报仍包含第一次的文章
        html = (self.tmp / "reports" / "2026-11-26.html").read_text(encoding="utf-8")
        self.assertIn("GPT-5.5", html)
        data = json.loads((self.tmp / "data" / "daily" / "2026-11-26.json").read_text(encoding="utf-8"))
        self.assertEqual(data["total_count"], 1)
        self.assertEqual(len(data["runs"]), 2)

    def test_incremental_overlap_window(self):
        self.run_once()
        later = NOW + timedelta(hours=24)
        rec = self.run_once(now=later)
        s = self.src(rec, "src_a")
        self.assertEqual(parse_iso(s["window_start"]), NOW - timedelta(hours=pipeline.OVERLAP_HOURS))
        self.assertEqual(parse_iso(s["window_end"]), later)

    def test_single_source_failure_and_recovery(self):
        self.run_once()  # 两个来源都成功，建立位置
        t1 = NOW + timedelta(hours=24)
        self.fake.feeds["src_a"] = "HTTP 503 Service Unavailable"
        self.fake.feeds["src_b"] = [item("b1", "智谱发布新一代大模型", "https://b.example/1", 1, base=t1)]
        r = self.run_once(now=t1)
        self.assertEqual(self.src(r, "src_a")["status"], "failed")
        self.assertIn("503", self.src(r, "src_a")["error"])
        self.assertEqual(self.src(r, "src_b")["new_count"], 1)
        st = self.store().get_state("src_a")
        self.assertEqual(parse_iso(st["last_window_end"]), NOW)  # 失败未推进
        self.assertEqual(st["consecutive_failures"], 1)
        # 异常也不影响其他来源
        t2 = NOW + timedelta(hours=30)
        self.fake.feeds["src_a"] = RuntimeError("boom")
        r = self.run_once(now=t2)
        self.assertEqual(self.src(r, "src_a")["status"], "failed")
        self.assertEqual(self.src(r, "src_b")["status"], "ok_no_new")
        # 恢复：窗口从上次成功位置（减重叠）开始，补取故障期间的文章
        t3 = NOW + timedelta(hours=48)
        self.fake.feeds["src_a"] = [item("a9", "Google DeepMind publishes research paper", "https://a.example/9", 40, base=t3)]
        r = self.run_once(now=t3)
        s = self.src(r, "src_a")
        self.assertEqual(parse_iso(s["window_start"]), NOW - timedelta(hours=pipeline.OVERLAP_HOURS))
        self.assertEqual(s["new_count"], 1)
        self.assertEqual(self.store().get_state("src_a")["consecutive_failures"], 0)
        html = (self.tmp / "reports" / "2026-11-28.html").read_text(encoding="utf-8")
        self.assertIn("DeepMind", html)

    def test_force_fail_flag(self):
        r = self.run_once(force_fail=["src_b"])
        self.assertEqual(self.src(r, "src_b")["status"], "failed")
        self.assertIn("模拟", self.src(r, "src_b")["error"])
        self.assertNotIn("src_b", self.fake.calls)

    def test_missing_dates_invalid_and_special_chars(self):
        self.fake.feeds["src_a"] = [
            item("m1", "OpenAI policy <script>alert('x')</script> & \"quotes\"", "https://a.example/m1",
                 summary="<b>LLM</b> & 中文 简介"),
            item("u1", "Anthropic AI model study", "https://a.example/u1", raw="yesterday-ish", status="unparseable"),
            item(None, "OpenAI no link no guid", None, 1),
        ]
        r = self.run_once()
        s = self.src(r, "src_a")
        self.assertEqual(s["new_count"], 2)
        self.assertEqual(s["invalid_count"], 1)
        html = (self.tmp / "reports" / "latest.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("日期缺失", html)
        self.assertIn("日期无法解析", html)
        arts = self.store().articles_for_date("2026-11-26")
        m = next(a for a in arts if a["guid"] == "m1")
        self.assertIsNone(m["published_at"])  # 不伪造日期
        # 缺日期条目再次出现不会重复新增
        r2 = self.run_once(now=NOW + timedelta(hours=1))
        self.assertEqual(r2["new_count"], 0)

    def test_reload_dedupe(self):
        self.fake.feeds["src_a"] = [item("a1", "OpenAI releases new model", "https://a.example/1", 1)]
        self.run_once()
        # 模拟进程重启：新的 Store / 新的 run 调用读取同一数据库
        st = self.store()
        self.assertIsNotNone(st.existing_key(dedupe.dedupe_keys("src_a", "a1", None)))
        st.close()
        r = self.run_once(now=NOW + timedelta(hours=3))
        self.assertEqual(r["new_count"], 0)

    def test_irrelevant_excluded(self):
        self.fake.feeds["src_b"] = [
            item("b1", "某球队用AI写作生成赛后新闻稿", "https://b.example/1", 1),
            item("b2", "Thailand tourism said to grow", "https://b.example/2", 1),
            item("b3", "阿里通义千问发布新模型", "https://b.example/3", 1),
        ]
        s = self.src(self.run_once(), "src_b")
        self.assertEqual(s["excluded_count"], 2)
        self.assertEqual(s["new_count"], 1)

    def test_manual_range_does_not_advance_state(self):
        self.run_once()
        since = NOW - timedelta(days=5)
        until = NOW - timedelta(days=4)
        self.fake.feeds["src_a"] = [item("old", "OpenAI old news model", "https://a.example/old", 24 * 4.5)]
        r = self.run_once(now=NOW + timedelta(hours=1), since=since, until=until)
        s = self.src(r, "src_a")
        self.assertEqual(s["mode"], "manual")
        self.assertEqual(s["new_count"], 1)
        self.assertEqual(parse_iso(self.store().get_state("src_a")["last_window_end"]), NOW)

    def test_history_and_latest(self):
        self.fake.feeds["src_a"] = [item("a1", "OpenAI releases new model", "https://a.example/1", 1)]
        self.run_once()
        self.fake.feeds["src_a"] = [item("a2", "Anthropic releases new model", "https://a.example/2", 1, base=NOW + timedelta(days=1))]
        self.run_once(now=NOW + timedelta(days=1))
        rep = self.tmp / "reports"
        self.assertTrue((rep / "2026-11-26.html").exists() and (rep / "2026-11-27.html").exists())
        self.assertTrue((rep / "2026-11-26.md").exists() and (rep / "2026-11-27.md").exists())
        self.assertIn("Anthropic", (rep / "latest.md").read_text(encoding="utf-8"))
        self.assertIn("OpenAI", (rep / "2026-11-26.md").read_text(encoding="utf-8"))
        idx = (rep / "index.html").read_text(encoding="utf-8")
        self.assertIn("2026-11-26", idx)
        self.assertIn("2026-11-27", idx)

    def test_all_failed_report(self):
        self.fake.feeds["src_a"] = "timeout"
        self.fake.feeds["src_b"] = "timeout"
        r = self.run_once()
        self.assertTrue(all(s["status"] == "failed" for s in r["sources"]))
        html = (self.tmp / "reports" / "latest.html").read_text(encoding="utf-8")
        self.assertIn("读取失败", html)
        self.assertIsNone(self.store().get_state("src_a")["last_window_end"])

    def test_invalid_range_and_empty_sources(self):
        with self.assertRaises(ValueError):
            self.run_once(since=NOW, until=NOW - timedelta(days=1))
        with self.assertRaises(ValueError):
            pipeline.run(base=self.tmp, sources=[], fetch=self.fake, now=NOW)

    def test_failure_notice_when_day_has_articles(self):
        self.fake.feeds["src_a"] = [item("a1", "OpenAI releases new model", "https://a.example/1", 1)]
        self.run_once()
        self.fake.feeds["src_a"] = "HTTP 500"
        self.run_once(now=NOW + timedelta(hours=1))
        html = (self.tmp / "reports" / "latest.html").read_text(encoding="utf-8")
        self.assertIn('notice fail', html)
        self.assertIn("OpenAI releases new model", html)  # 失败不清空当日内容
        md = (self.tmp / "reports" / "latest.md").read_text(encoding="utf-8")
        self.assertIn("读取失败", md)

    def test_regenerate(self):
        self.fake.feeds["src_a"] = [item("a1", "OpenAI releases new model", "https://a.example/1", 1)]
        self.run_once()
        out = pipeline.regenerate(base=self.tmp, report_date="2026-11-26", sources=SOURCES)
        self.assertTrue(Path(out["html"]).exists())
        with self.assertRaises(ValueError):
            pipeline.regenerate(base=self.tmp, report_date="2020-01-01", sources=SOURCES)

    def test_lock_prevents_concurrent_run(self):
        paths = pipeline.Paths(self.tmp)
        with pipeline.run_lock(paths.lock):
            with self.assertRaises(pipeline.LockBusy):
                self.run_once()


if __name__ == "__main__":
    unittest.main()
