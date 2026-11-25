"""主流程：加锁 → 逐来源抓取/筛选/分类/去重入库 → 生成日报与数据文件 → 记录日志。（主控维护）"""
from __future__ import annotations

import fcntl
import json
import os
import secrets
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import classify, dedupe, timeutil
from .storage import Store
from .timeutil import fmt_sgt, iso_utc, now_utc, parse_iso, sgt_date

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INITIAL_HOURS = 72      # 首次运行回溯小时数
OVERLAP_HOURS = 6       # 增量运行与上次成功窗口的重叠小时数（重叠部分靠去重消化）

STATUS_FAILED, STATUS_NO_NEW, STATUS_NEW = "failed", "ok_no_new", "ok_new"


class LockBusy(RuntimeError):
    pass


@contextmanager
def run_lock(lock_path: Path):
    """文件锁（fcntl.flock，非阻塞）：防止定时任务与手动运行并发写库。进程退出时自动释放。"""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a+")
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LockBusy(f"已有运行实例持有锁：{lock_path}")
        fh.seek(0)
        fh.truncate()
        fh.write(f"pid={os.getpid()} at={iso_utc(now_utc())}\n")
        fh.flush()
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


class Paths:
    def __init__(self, base: Path):
        self.base = Path(base)
        self.db = self.base / "data" / "news.db"
        self.daily_json = self.base / "data" / "daily"
        self.reports = self.base / "reports"
        self.logs = self.base / "logs"
        self.lock = self.base / "data" / ".run.lock"


def load_sources(path: Optional[Path] = None) -> List[Dict]:
    p = path or (PROJECT_ROOT / "config" / "sources.json")
    data = json.loads(Path(p).read_text(encoding="utf-8"))
    sources = data.get("sources") if isinstance(data, dict) else None
    if not sources:
        raise ValueError(f"来源配置为空或格式错误（需要 {{\"sources\": [...]}}）：{p}")
    return sources


def _atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _log(paths: Paths, line: str):
    paths.logs.mkdir(parents=True, exist_ok=True)
    stamp = fmt_sgt(now_utc())
    with open(paths.logs / "run.log", "a", encoding="utf-8") as f:
        f.write(f"[{stamp}] {line}\n")


def _window_for(store: Store, source_id: str, now: datetime, since: Optional[datetime],
                until: Optional[datetime]):
    """返回 (mode, start, end, advance_state)。"""
    if since or until:
        end = until or now
        start = since or (end - timedelta(hours=INITIAL_HOURS))
        return "manual", start, end, False
    st = store.get_state(source_id)
    last_end = parse_iso(st["last_window_end"]) if st and st.get("last_window_end") else None
    if last_end is None:
        return "initial", now - timedelta(hours=INITIAL_HOURS), now, True
    return "incremental", last_end - timedelta(hours=OVERLAP_HOURS), now, True


def _process_source(store: Store, src: Dict, fetch: Callable, now: datetime, run_id: str,
                    report_date: str, since, until, force_fail: bool) -> Dict:
    from .fetcher import filter_by_window  # 延迟导入，便于测试替换

    mode, start, end, advance = _window_for(store, src["id"], now, since, until)
    info = {
        "source_id": src["id"], "source_name": src["name"], "mode": mode,
        "window_start": iso_utc(start), "window_end": iso_utc(end),
        "status": STATUS_FAILED, "error": None, "http_status": None,
        "fetched_count": 0, "invalid_count": 0, "in_window_count": 0, "no_date_count": 0,
        "relevant_count": 0, "pending_count": 0, "excluded_count": 0,
        "duplicate_count": 0, "new_count": 0,
    }
    attempt_at = iso_utc(now)
    try:
        if force_fail:
            res = {"ok": False, "error": "模拟故障（--force-fail，仅用于验证失败处理）", "items": [],
                   "http_status": None, "invalid_count": 0}
        else:
            res = fetch(src)
        info["http_status"] = res.get("http_status")
        if not res.get("ok"):
            info["error"] = res.get("error") or "未知错误"
            store.record_failure(src["id"], attempt_at, info["error"])
            return info
        items = res.get("items", [])
        info["fetched_count"] = len(items)
        info["invalid_count"] = res.get("invalid_count", 0)
        kept, stats = filter_by_window(items, start, end)
        info["in_window_count"] = len(kept)
        info["no_date_count"] = stats.get("no_date_kept", 0)
        collected_at = iso_utc(now)
        for it in kept:
            verdict = classify.assess(it.get("title", ""), it.get("summary", ""))
            if verdict["relevance"] == "no":
                info["excluded_count"] += 1
                continue
            if verdict["relevance"] == "maybe":
                info["pending_count"] += 1
            info["relevant_count"] += 1
            keys = dedupe.dedupe_keys(src["id"], it.get("guid"), it.get("link"))
            if not keys:
                info["invalid_count"] += 1
                continue
            art = {
                "id": dedupe.article_id(keys), "source_id": src["id"], "source_name": src["name"],
                "guid": it.get("guid"), "title": it.get("title") or "（无标题）", "link": it.get("link"),
                "canonical_link": dedupe.normalize_url(it.get("link")), "summary": it.get("summary") or "",
                "published_raw": it.get("published_raw"), "published_at": it.get("published_at"),
                "date_status": it.get("date_status", "missing"), "relevance": verdict["relevance"],
                "category": verdict["category"], "category_reason": verdict["reason"],
                "collected_at": collected_at, "report_date": report_date, "run_id": run_id,
            }
            if store.insert_article(art, keys):
                info["new_count"] += 1
            else:
                info["duplicate_count"] += 1
        info["status"] = STATUS_NEW if info["new_count"] else STATUS_NO_NEW
        store.record_success(src["id"], attempt_at, iso_utc(end) if advance else None)
    except Exception as e:  # 单来源任何异常都不影响其他来源，且不推进成功记录
        info["status"] = STATUS_FAILED
        info["error"] = f"{type(e).__name__}: {e}"
        info["traceback"] = traceback.format_exc(limit=5)
        store.record_failure(src["id"], attempt_at, info["error"])
    return info


def run(base: Path = PROJECT_ROOT, sources: Optional[List[Dict]] = None, fetch: Optional[Callable] = None,
        now: Optional[datetime] = None, since: Optional[datetime] = None, until: Optional[datetime] = None,
        only: Optional[List[str]] = None, force_fail: Optional[List[str]] = None) -> Dict:
    paths = Paths(base)
    if fetch is None:
        from .fetcher import fetch_feed as fetch
    sources = sources if sources is not None else load_sources()
    if only:
        sources = [s for s in sources if s["id"] in only]
    if not sources:
        raise ValueError("没有可处理的来源（检查 config/sources.json 或 --sources 参数）")
    if since and until and timeutil.to_utc(since) >= timeutil.to_utc(until):
        raise ValueError(f"起始时间必须早于结束时间：since={iso_utc(since)} until={iso_utc(until)}")
    force_fail = set(force_fail or [])
    with run_lock(paths.lock):
        now = timeutil.to_utc(now) if now else now_utc()
        report_date = sgt_date(now)
        run_id = timeutil.to_sgt(now).strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(2)
        store = Store(paths.db)
        try:
            results = [
                _process_source(store, s, fetch, now, run_id, report_date, since, until, s["id"] in force_fail)
                for s in sources
            ]
            modes = {r["mode"] for r in results}
            run_rec = {
                "run_id": run_id, "started_at": iso_utc(now), "finished_at": iso_utc(now_utc()),
                "report_date": report_date, "mode": modes.pop() if len(modes) == 1 else "mixed",
                "new_count": sum(r["new_count"] for r in results), "sources": results,
                "window_start": min((r["window_start"] for r in results), default=None),
                "window_end": max((r["window_end"] for r in results), default=None),
            }
            store.save_run(run_rec)
            try:
                run_rec["outputs"] = write_outputs(store, paths, report_date, sources)
            except Exception as e:  # 日报生成失败也要留下运行日志
                run_rec["outputs"] = {}
                run_rec["output_error"] = f"{type(e).__name__}: {e}"
                _log(paths, f"ERROR 生成日报失败 {run_rec['output_error']}\n{traceback.format_exc(limit=5)}")
        finally:
            store.close()
    _log(paths, _summary_line(run_rec))
    for r in run_rec["sources"]:
        if r["status"] == STATUS_FAILED:
            _log(paths, f"ERROR source={r['source_id']} {r['error']}")
            if r.get("traceback"):
                _log(paths, r["traceback"].rstrip())
    with open(paths.logs / "runs.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({k: v for k, v in run_rec.items()}, ensure_ascii=False) + "\n")
    return run_rec


def _summary_line(run_rec: Dict) -> str:
    parts = [f"run={run_rec['run_id']} mode={run_rec['mode']} new={run_rec['new_count']}"]
    for r in run_rec["sources"]:
        parts.append(
            f"{r['source_id']}:{r['status']}(fetched={r['fetched_count']},window={r['in_window_count']},"
            f"relevant={r['relevant_count']},new={r['new_count']},dup={r['duplicate_count']})"
        )
    return " ".join(parts)


# ---------------- 日报生成 ----------------

def _display_run(run_rec: Dict) -> Dict:
    srcs = []
    for r in run_rec["sources"]:
        srcs.append({
            "source_id": r["source_id"], "source_name": r["source_name"], "status": r["status"],
            "window_display": f"{fmt_sgt(r['window_start'])} — {fmt_sgt(r['window_end'])}",
            "fetched_count": r["fetched_count"], "in_window_count": r["in_window_count"],
            "relevant_count": r["relevant_count"], "new_count": r["new_count"], "error": r["error"],
        })
    win = "—"
    if run_rec.get("window_start"):
        win = f"{fmt_sgt(run_rec['window_start'])} — {fmt_sgt(run_rec['window_end'])}"
    return {
        "run_id": run_rec["run_id"], "started_display": fmt_sgt(run_rec["started_at"]),
        "finished_display": fmt_sgt(run_rec.get("finished_at")), "window_display": win,
        "mode": run_rec["mode"], "new_count": run_rec["new_count"], "sources": srcs,
    }


def _published_display(a: Dict) -> str:
    if a["date_status"] == "ok" and a.get("published_at"):
        return fmt_sgt(a["published_at"])
    if a["date_status"] == "unparseable":
        return f"日期无法解析（原文：{a.get('published_raw') or ''}）"
    return "日期缺失"


def build_report(store: Store, report_date: str, sources: List[Dict]) -> Dict:
    runs = store.runs_for_date(report_date)
    latest_id = runs[-1]["run_id"] if runs else None
    by_cat = {c: [] for c in classify.CATEGORIES}
    for a in store.articles_for_date(report_date):
        by_cat.setdefault(a["category"], []).append({
            "id": a["id"], "title": a["title"], "link": a["link"], "source_id": a["source_id"],
            "source_name": a["source_name"], "published_display": _published_display(a),
            "date_status": a["date_status"], "collected_display": fmt_sgt(a["collected_at"]),
            "summary": a["summary"] or "", "category": a["category"],
            "category_reason": a["category_reason"] or "", "is_new": a["run_id"] == latest_id,
            # 以下为结构化数据文件额外保留的原始字段
            "published_at": a["published_at"], "published_raw": a["published_raw"],
            "collected_at": a["collected_at"], "guid": a["guid"], "relevance": a["relevance"],
        })
    disp_runs = [_display_run(r) for r in runs]
    return {
        "report_date": report_date,
        "generated_at": fmt_sgt(now_utc()),
        "timezone": timeutil.TZ_NAME,
        "categories": list(classify.CATEGORIES),
        "articles_by_category": by_cat,
        "total_count": sum(len(v) for v in by_cat.values()),
        "latest_run": disp_runs[-1] if disp_runs else None,
        "runs": disp_runs,
        "sources": [{k: s.get(k) for k in ("id", "name", "website", "rss_url", "language")} for s in sources],
    }


def write_outputs(store: Store, paths: Paths, report_date: str, sources: List[Dict]) -> Dict:
    from . import report as R

    rep = build_report(store, report_date, sources)
    html_text, md_text = R.render_html(rep), R.render_markdown(rep)
    html_p = paths.reports / f"{report_date}.html"
    md_p = paths.reports / f"{report_date}.md"
    json_p = paths.daily_json / f"{report_date}.json"
    data = dict(rep)
    data["raw_runs"] = store.runs_for_date(report_date)
    json_text = json.dumps(data, ensure_ascii=False, indent=2)
    _atomic_write(html_p, html_text)
    _atomic_write(md_p, md_text)
    _atomic_write(json_p, json_text)
    latest = max(store.report_dates() or [report_date])
    if report_date == latest:  # 只有最新日期才覆盖 latest.*
        _atomic_write(paths.reports / "latest.html", html_text)
        _atomic_write(paths.reports / "latest.md", md_text)
        _atomic_write(paths.base / "data" / "latest.json", json_text)
    entries = [{"date": d, "total_count": store.count_for_date(d), "html": f"{d}.html", "md": f"{d}.md"}
               for d in store.report_dates()]
    _atomic_write(paths.reports / "index.html", R.render_index(entries))
    return {"html": str(html_p), "markdown": str(md_p), "json": str(json_p),
            "latest_html": str(paths.reports / "latest.html"), "index": str(paths.reports / "index.html")}


def regenerate(base: Path = PROJECT_ROOT, report_date: Optional[str] = None,
               sources: Optional[List[Dict]] = None) -> Dict:
    paths = Paths(base)
    report_date = report_date or sgt_date()
    with run_lock(paths.lock):
        store = Store(paths.db)
        try:
            if report_date not in store.report_dates():
                raise ValueError(f"{report_date} 没有运行记录或文章，不生成空日报")
            return write_outputs(store, paths, report_date, sources or load_sources())
        finally:
            store.close()
