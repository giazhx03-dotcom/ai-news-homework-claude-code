"""日报渲染：HTML / Markdown / 历史索引。（子Agent B）

只负责把 CONTRACT 第 3 节定义的 report 字典渲染成字符串，不读写文件或数据库。
所有外部字符串一律转义；链接仅允许 http/https。
"""
from __future__ import annotations

import html
import re
from urllib.parse import quote, urlsplit

STATUS_LABELS = {"failed": "读取失败", "ok_no_new": "成功·无更新", "ok_new": "成功·有更新"}

DEFAULT_CATEGORIES = ["模型与研究", "产品与工具", "行业与政策", "待确认"]

CATEGORY_NOTES = {
    "模型与研究": "新模型发布、模型能力/评测、论文与学术研究、训练与推理技术进展。",
    "产品与工具": "面向用户或开发者的 AI 产品、应用、API、开源工具与功能更新。",
    "行业与政策": "融资并购、公司动态、市场与人事、监管法规、政策与安全治理。",
    "待确认": "与 AI 相关但规则无法明确归入以上三类的条目，需人工判断。",
}

DISCLAIMER = "内容仅基于 RSS 标题与简介，未阅读原文全文；分类为规则自动判断，可能有误。"

CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src 'none'"

NO_SUMMARY = "（RSS 未提供简介）"


# ---------------------------------------------------------------- 通用工具

def _s(v) -> str:
    """任意值 -> 字符串（None -> ""）。"""
    if v is None:
        return ""
    return str(v)


def _e(v) -> str:
    """HTML 转义（含引号）。"""
    return html.escape(_s(v), quote=True)


def _int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def safe_url(url) -> str | None:
    """仅当链接为 http/https 且有主机名时返回去空白后的 URL，否则 None。"""
    u = _s(url).strip()
    if not u:
        return None
    # 拒绝控制字符（可被用于绕过协议判断）
    if any(ord(c) < 32 or ord(c) == 127 for c in u):
        return None
    try:
        parts = urlsplit(u)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return None
    return u


def _categories(report: dict) -> list:
    cats = report.get("categories") or DEFAULT_CATEGORIES
    return list(cats)


def _articles(report: dict, cat: str) -> list:
    return list((report.get("articles_by_category") or {}).get(cat) or [])


def _latest_run(report: dict) -> dict:
    return report.get("latest_run") or {}


def _runs(report: dict) -> list:
    return list(report.get("runs") or [])


def _fail_notice(report: dict) -> str | None:
    """当日已有条目、但本次运行有来源读取失败时的提示（total_count==0 时由 _empty_reason 负责）。"""
    if _int(report.get("total_count")) == 0:
        return None
    failed = [s for s in (_latest_run(report).get("sources") or []) if s.get("status") == "failed"]
    if not failed:
        return None
    names = "、".join(_s(s.get("source_name") or s.get("source_id")) for s in failed)
    return f"本次运行中 {names} 读取失败；已保留当日此前收集的条目，失败来源将在下次运行时从上次成功位置补取。"


def _empty_reason(report: dict) -> str | None:
    """total_count==0 时返回说明文字，否则 None。"""
    if _int(report.get("total_count")) > 0:
        return None
    srcs = _latest_run(report).get("sources") or []
    if srcs and all(s.get("status") == "failed" for s in srcs):
        return "所有来源读取失败，本日暂无条目。请查看下方来源状态中的错误原因，稍后重试。"
    if not srcs:
        return "本日暂无条目，且没有可用的来源运行记录。"
    failed = [s for s in srcs if s.get("status") == "failed"]
    if failed:
        return ("部分来源读取失败；其余来源读取正常，但时间范围内没有 AI 相关的新内容。"
                "本日暂无条目。")
    return "所有来源读取正常，但时间范围内没有 AI 相关的新内容，本日暂无条目。"


# ---------------------------------------------------------------- HTML

_CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1f2328;--muted:#656d76;--border:#d8dee4;
--accent:#0969da;--red-bg:#ffebe9;--red-fg:#cf222e;--green-bg:#dafbe1;--green-fg:#1a7f37;
--gray-bg:#eaeef2;--gray-fg:#57606a;--warn-bg:#fff8c5;--warn-fg:#7d4e00;--new-bg:#ddf4ff;--new-fg:#0969da}
@media (prefers-color-scheme: dark){:root{--bg:#0d1117;--card:#161b22;--fg:#e6edf3;--muted:#8d96a0;
--border:#30363d;--accent:#4493f8;--red-bg:#3d1a1d;--red-fg:#ff7b72;--green-bg:#12301c;--green-fg:#56d364;
--gray-bg:#262c36;--gray-fg:#9198a1;--warn-bg:#3a2e0b;--warn-fg:#e3b341;--new-bg:#0c2d4f;--new-fg:#79c0ff}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.65 -apple-system,BlinkMacSystemFont,
"PingFang SC","Hiragino Sans GB","Microsoft YaHei","Segoe UI",Roboto,sans-serif;-webkit-text-size-adjust:100%}
.wrap{max-width:1080px;margin:0 auto;padding:20px 16px 40px}
h1{font-size:1.6rem;margin:0 0 8px;line-height:1.3}
h2{font-size:1.2rem;margin:28px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--border)}
a{color:var(--accent)}
.meta{color:var(--muted);font-size:.9rem;margin:2px 0}
.stats{display:flex;flex-wrap:wrap;gap:10px;margin:14px 0}
.stat{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:8px 14px;min-width:110px}
.stat b{display:block;font-size:1.35rem}
.stat span{color:var(--muted);font-size:.82rem}
.notice{border-radius:10px;padding:12px 16px;margin:14px 0;font-weight:600}
.notice.fail{background:var(--red-bg);color:var(--red-fg)}
.notice.empty{background:var(--warn-bg);color:var(--warn-fg)}
.badge{display:inline-block;border-radius:999px;padding:1px 10px;font-size:.8rem;font-weight:600;white-space:nowrap}
.b-failed{background:var(--red-bg);color:var(--red-fg)}
.b-ok_new{background:var(--green-bg);color:var(--green-fg)}
.b-ok_no_new{background:var(--gray-bg);color:var(--gray-fg)}
.b-new{background:var(--new-bg);color:var(--new-fg)}
.tablebox{overflow-x:auto;-webkit-overflow-scrolling:touch;border:1px solid var(--border);border-radius:10px;background:var(--card)}
table{border-collapse:collapse;width:100%;min-width:720px;font-size:.88rem}
th,td{padding:8px 10px;border-bottom:1px solid var(--border);text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:600;white-space:nowrap}
tr:last-child td{border-bottom:none}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.err{color:var(--red-fg);word-break:break-word}
details{margin:12px 0;background:var(--card);border:1px solid var(--border);border-radius:10px;padding:8px 14px}
summary{cursor:pointer;font-weight:600}
details .tablebox{margin:8px 0;border:none}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px 16px;min-width:0}
.card h3{font-size:1rem;margin:0 0 6px;line-height:1.45;word-break:break-word}
.card .sub{color:var(--muted);font-size:.82rem;margin:2px 0}
.card .sum{margin:8px 0;word-break:break-word}
.card .reason{color:var(--muted);font-size:.8rem}
.datewarn{color:var(--warn-fg);background:var(--warn-bg);border-radius:4px;padding:0 4px}
.invalid{color:var(--muted);font-size:.78rem}
.nosum{color:var(--muted);font-style:italic}
.empty-cat{color:var(--muted);background:var(--card);border:1px dashed var(--border);border-radius:10px;padding:14px;text-align:center}
.count{color:var(--muted);font-weight:400;font-size:.9rem}
footer{margin-top:36px;padding-top:14px;border-top:1px solid var(--border);color:var(--muted);font-size:.85rem}
footer ul{padding-left:20px;margin:6px 0}
.idx li{margin:6px 0}
@media (max-width:640px){.wrap{padding:14px 12px 32px}h1{font-size:1.3rem}.cards{grid-template-columns:1fr}
.stat{flex:1 1 40%;min-width:0}}
"""


def _head(title: str) -> str:
    return (
        "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\">\n"
        f"<meta http-equiv=\"Content-Security-Policy\" content=\"{CSP}\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<meta name=\"referrer\" content=\"no-referrer\">\n"
        "<meta name=\"color-scheme\" content=\"light dark\">\n"
        f"<title>{_e(title)}</title>\n<style>{_CSS}</style>\n</head>\n<body>\n<div class=\"wrap\">\n"
    )


_TAIL = "</div>\n</body>\n</html>\n"


def _html_link(text, url) -> str:
    """生成安全链接；非 http/https 仅显示文字并注明。"""
    label = _e(text) if _s(text).strip() else "（无标题）"
    u = md_url(url)  # 同样对空白/括号等百分号编码
    if u:
        return (f"<a href=\"{_e(u)}\" target=\"_blank\" rel=\"noopener noreferrer\">{label}</a>")
    if _s(url).strip():
        return f"{label} <span class=\"invalid\">（链接无效）</span>"
    return f"{label} <span class=\"invalid\">（无原文链接）</span>"


def _badge(status) -> str:
    st = _s(status)
    label = STATUS_LABELS.get(st, st or "未知")
    cls = st if st in STATUS_LABELS else "ok_no_new"
    return f"<span class=\"badge b-{cls}\">{_e(label)}</span>"


def _source_table(sources: list) -> str:
    rows = []
    for s in sources:
        err = s.get("error")
        err_html = f"<span class=\"err\">{_e(err)}</span>" if err else "—"
        rows.append(
            "<tr>"
            f"<td>{_e(s.get('source_name') or s.get('source_id'))}</td>"
            f"<td>{_badge(s.get('status'))}</td>"
            f"<td class=\"num\">{_int(s.get('fetched_count'))}</td>"
            f"<td class=\"num\">{_int(s.get('in_window_count'))}</td>"
            f"<td class=\"num\">{_int(s.get('relevant_count'))}</td>"
            f"<td class=\"num\">{_int(s.get('new_count'))}</td>"
            f"<td>{_e(s.get('window_display')) or '—'}</td>"
            f"<td>{err_html}</td>"
            "</tr>"
        )
    if not rows:
        rows.append("<tr><td colspan=\"8\">无来源运行记录</td></tr>")
    return (
        "<div class=\"tablebox\"><table>\n<thead><tr><th>来源</th><th>状态</th><th>抓取数</th>"
        "<th>窗口内</th><th>AI 相关</th><th>新增</th><th>收集窗口</th><th>错误原因</th></tr></thead>\n"
        "<tbody>\n" + "\n".join(rows) + "\n</tbody></table></div>\n"
    )


_MODE_LABELS = {"initial": "首次", "incremental": "增量", "manual": "手动", "mixed": "混合"}


def _article_card(a: dict) -> str:
    new = " <span class=\"badge b-new\">本次新增</span>" if a.get("is_new") else ""
    pub = _e(a.get("published_display") or "日期缺失")
    if a.get("date_status") in ("missing", "unparseable"):
        pub = f"<span class=\"datewarn\">{pub}</span>"
    summary = _s(a.get("summary")).strip()
    sum_html = _e(summary) if summary else f"<span class=\"nosum\">{NO_SUMMARY}</span>"
    reason = _s(a.get("category_reason")).strip()
    return (
        "<article class=\"card\">"
        f"<h3>{_html_link(a.get('title'), a.get('link'))}{new}</h3>"
        f"<div class=\"sub\">来源：{_e(a.get('source_name') or a.get('source_id'))}</div>"
        f"<div class=\"sub\">发布时间：{pub}</div>"
        f"<div class=\"sub\">收集时间：{_e(a.get('collected_display')) or '—'}</div>"
        f"<p class=\"sum\">{sum_html}</p>"
        f"<div class=\"reason\">分类依据：{_e(reason) if reason else '—'}</div>"
        "</article>"
    )


def _footer_html() -> str:
    notes = "".join(
        f"<li><b>{_e(k)}</b>：{_e(v)}</li>" for k, v in CATEGORY_NOTES.items())
    return (
        "<footer>\n"
        f"<p><b>声明：</b>{_e(DISCLAIMER)}</p>\n"
        f"<p><b>分类说明：</b></p><ul>{notes}</ul>\n"
        "<p>时间均以 Asia/Singapore（SGT）显示。<a href=\"index.html\">历史日报</a></p>\n"
        "</footer>\n"
    )


def render_html(report: dict) -> str:
    date = _s(report.get("report_date"))
    title = f"AI 行业新闻日报 · {date}"
    run = _latest_run(report)
    runs = _runs(report)
    cats = _categories(report)
    total = _int(report.get("total_count"))
    out = [_head(title)]

    out.append(f"<header>\n<h1>{_e(title)}</h1>\n")
    out.append(f"<p class=\"meta\">更新时间：{_e(report.get('generated_at'))}"
               f" · 时区：{_e(report.get('timezone') or 'Asia/Singapore')}</p>\n")
    out.append(f"<p class=\"meta\">本次收集范围：{_e(run.get('window_display')) or '—'}"
               "（各来源窗口见来源状态表）</p>\n")
    out.append(
        "<div class=\"stats\">"
        f"<div class=\"stat\"><b>{_int(run.get('new_count'))}</b><span>本次新增</span></div>"
        f"<div class=\"stat\"><b>{total}</b><span>日报总数</span></div>"
        f"<div class=\"stat\"><b>{len(runs)}</b><span>本日运行次数</span></div>"
        f"<div class=\"stat\"><b>{len(run.get('sources') or [])}</b><span>来源数</span></div>"
        "</div>\n")
    reason = _empty_reason(report)
    if reason:
        srcs = run.get("sources") or []
        cls = "fail" if srcs and all(s.get("status") == "failed" for s in srcs) else "empty"
        out.append(f"<div class=\"notice {cls}\">{_e(reason)}</div>\n")
    fail = _fail_notice(report)
    if fail:
        out.append(f"<div class=\"notice fail\">{_e(fail)}</div>\n")
    out.append("</header>\n")

    # 来源状态
    out.append("<section>\n<h2>来源状态（本次运行）</h2>\n")
    out.append(_source_table(run.get("sources") or []))
    if len(runs) > 1:
        out.append(f"<details><summary>本日历次运行记录（{len(runs)} 次）</summary>\n")
        for i, r in enumerate(runs, 1):
            mode = _MODE_LABELS.get(_s(r.get("mode")), _s(r.get("mode")))
            out.append(
                f"<p class=\"meta\"><b>第 {i} 次</b> · {_e(r.get('started_display'))} — "
                f"{_e(r.get('finished_display'))} · 模式：{_e(mode)} · "
                f"新增 {_int(r.get('new_count'))} · 窗口：{_e(r.get('window_display'))}</p>\n")
            out.append(_source_table(r.get("sources") or []))
        out.append("</details>\n")
    out.append("</section>\n")

    # 分类
    for cat in cats:
        arts = _articles(report, cat)
        out.append(f"<section>\n<h2>{_e(cat)} <span class=\"count\">（{len(arts)} 条）</span></h2>\n")
        if arts:
            out.append("<div class=\"cards\">\n")
            out.append("\n".join(_article_card(a) for a in arts))
            out.append("\n</div>\n")
        else:
            out.append("<div class=\"empty-cat\">本类暂无条目</div>\n")
        out.append("</section>\n")

    out.append(_footer_html())
    out.append(_TAIL)
    return "".join(out)


# ---------------------------------------------------------------- Markdown

_MD_SPECIAL = re.compile(r"([\\`*_\[\]()<>#|!~{}])")


def md_escape(v) -> str:
    """转义 Markdown 行内特殊字符，并把换行压成空格。"""
    s = re.sub(r"\s*[\r\n]+\s*", " ", _s(v))
    s = _MD_SPECIAL.sub(r"\\\1", s)
    s = re.sub(r"&(?=#?[A-Za-z0-9]+;)", "&amp;", s)  # 仅转义会被当作 HTML 实体的 &
    return s


def md_cell(v) -> str:
    """表格单元格：同 md_escape（已含 | 与换行处理）。"""
    return md_escape(v)


def md_url(url) -> str | None:
    """对 http/https 链接中的空白、括号、尖括号等做百分号编码；否则 None。"""
    u = safe_url(url)
    if not u:
        return None
    return quote(u, safe=":/?#@!$&'*+,;=%-._~")  # 不保留 ( ) [ ] < > 空格 |


def _md_link(text, url) -> str:
    label = md_escape(text) if _s(text).strip() else "（无标题）"
    u = md_url(url)
    if u:
        return f"[{label}]({u})"
    if _s(url).strip():
        return f"{label}（链接无效）"
    return f"{label}（无原文链接）"


def _md_source_table(sources: list) -> list:
    lines = ["| 来源 | 状态 | 抓取数 | 窗口内 | AI 相关 | 新增 | 收集窗口 | 错误原因 |",
             "|---|---|---:|---:|---:|---:|---|---|"]
    for s in sources:
        st = _s(s.get("status"))
        lines.append("| " + " | ".join([
            md_cell(s.get("source_name") or s.get("source_id")),
            md_cell(STATUS_LABELS.get(st, st or "未知")),
            str(_int(s.get("fetched_count"))),
            str(_int(s.get("in_window_count"))),
            str(_int(s.get("relevant_count"))),
            str(_int(s.get("new_count"))),
            md_cell(s.get("window_display")) or "—",
            md_cell(s.get("error")) if s.get("error") else "—",
        ]) + " |")
    if not sources:
        lines.append("| 无来源运行记录 |  |  |  |  |  |  |  |")
    return lines


def render_markdown(report: dict) -> str:
    date = _s(report.get("report_date"))
    run = _latest_run(report)
    runs = _runs(report)
    total = _int(report.get("total_count"))
    L = [f"# AI 行业新闻日报 · {md_escape(date)}", "", "## 概要", ""]
    L.append(f"- 更新时间：{md_escape(report.get('generated_at'))}")
    L.append(f"- 时区：{md_escape(report.get('timezone') or 'Asia/Singapore')}")
    L.append(f"- 本次收集范围：{md_escape(run.get('window_display')) or '—'}（各来源窗口见来源状态表）")
    L.append(f"- 本次新增：{_int(run.get('new_count'))} 条")
    L.append(f"- 日报总数：{total} 条")
    L.append(f"- 本日运行次数：{len(runs)}")
    reason = _empty_reason(report)
    if reason:
        L += ["", f"> **{md_escape(reason)}**"]
    fail = _fail_notice(report)
    if fail:
        L += ["", f"> **{md_escape(fail)}**"]
    L += ["", "## 来源状态（本次运行）", ""]
    L += _md_source_table(run.get("sources") or [])
    if len(runs) > 1:
        L += ["", "### 本日历次运行记录", ""]
        for i, r in enumerate(runs, 1):
            mode = _MODE_LABELS.get(_s(r.get("mode")), _s(r.get("mode")))
            L.append(f"- 第 {i} 次：{md_escape(r.get('started_display'))} — "
                     f"{md_escape(r.get('finished_display'))}，模式：{md_escape(mode)}，"
                     f"新增 {_int(r.get('new_count'))}，窗口：{md_escape(r.get('window_display'))}")
    for cat in _categories(report):
        arts = _articles(report, cat)
        L += ["", f"## {md_escape(cat)}（{len(arts)} 条）", ""]
        if not arts:
            L.append("本类暂无条目")
            continue
        for a in arts:
            new = " **【本次新增】**" if a.get("is_new") else ""
            L.append(f"### {_md_link(a.get('title'), a.get('link'))}{new}")
            L.append("")
            pub = md_escape(a.get("published_display") or "日期缺失")
            if a.get("date_status") in ("missing", "unparseable"):
                pub = f"⚠ {pub}"
            L.append(f"- 来源：{md_escape(a.get('source_name') or a.get('source_id'))}")
            L.append(f"- 发布时间：{pub}")
            L.append(f"- 收集时间：{md_escape(a.get('collected_display')) or '—'}")
            summary = _s(a.get("summary")).strip()
            L.append(f"- 简介：{md_escape(summary) if summary else NO_SUMMARY}")
            reason_c = _s(a.get("category_reason")).strip()
            L.append(f"- 分类依据：{md_escape(reason_c) if reason_c else '—'}")
            L.append("")
    L += ["", "---", "", f"**声明：**{DISCLAIMER}", "", "**分类说明：**", ""]
    for k, v in CATEGORY_NOTES.items():
        L.append(f"- **{k}**：{v}")
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------- 索引页

def _safe_rel(path) -> str | None:
    """索引中的相对文件名：仅允许简单文件名（字母数字 . _ -）。"""
    p = _s(path).strip()
    if p and re.fullmatch(r"[A-Za-z0-9._-]+", p) and not p.startswith("."):
        return p
    return None


def render_index(entries: list) -> str:
    title = "AI 行业新闻日报 · 历史"
    items = sorted(entries or [], key=lambda e: _s(e.get("date")), reverse=True)
    out = [_head(title), f"<h1>{_e(title)}</h1>\n"]
    out.append("<p><a href=\"latest.html\">查看最新日报 →</a></p>\n")
    if not items:
        out.append("<div class=\"empty-cat\">暂无历史日报</div>\n")
    else:
        out.append("<ul class=\"idx\">\n")
        for e in items:
            links = []
            for key, label in (("html", "HTML"), ("md", "Markdown")):
                rel = _safe_rel(e.get(key))
                if rel:
                    links.append(f"<a href=\"{_e(rel)}\">{label}</a>")
            out.append(f"<li><b>{_e(e.get('date'))}</b> · {_int(e.get('total_count'))} 条 · "
                       + (" · ".join(links) if links else "（无文件）") + "</li>\n")
        out.append("</ul>\n")
    out.append(f"<footer><p>{_e(DISCLAIMER)}</p></footer>\n")
    out.append(_TAIL)
    return "".join(out)
