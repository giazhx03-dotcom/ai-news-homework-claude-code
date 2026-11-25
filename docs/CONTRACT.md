# 模块接口与数据约定（主控制定，各方必须遵守）

运行环境：macOS，`.venv/bin/python`（Python 3.9.6，**仅标准库**）。Python 3.9：注解里用 `X | None` 时文件头必须写 `from __future__ import annotations`。
测试框架：标准库 `unittest`，运行 `.venv/bin/python -m unittest discover -s tests -v`。
时间：内部一律 UTC aware datetime / ISO 字符串（`ainews/timeutil.py` 提供 `iso_utc / parse_iso / fmt_sgt / sgt_date / SGT`），展示统一 Asia/Singapore。

## 文件归属

| 归属 | 文件 |
|---|---|
| 子Agent A | `ainews/fetcher.py`、`config/sources.json`、`tests/test_fetcher.py`、`tests/fixtures/*`（A 的 RSS/Atom 样例）、`docs/SOURCES.md` |
| 子Agent B | `ainews/report.py`、`tests/test_report.py` |
| 主控 | `ainews/timeutil.py`、`storage.py`、`dedupe.py`、`classify.py`、`pipeline.py`、`__main__.py`、`config/rules.json`、`scheduling/*`、`scripts/*`、README、其余 docs、Git |

不得修改不属于自己的文件；如需改接口，在回复中提出。

## 1. config/sources.json（A 产出）

```json
{"sources": [
  {"id": "src_slug", "name": "名称", "website": "https://...", "rss_url": "https://...",
   "language": "zh|en", "focus": "内容侧重点", "reason": "选择原因",
   "verified_at": "ISO UTC", "verify_result": "HTTP 200, RSS 2.0, N 条, 最新条目日期 ..."}
]}
```

## 2. fetcher.py（A 实现）

```python
def fetch_feed(source: dict, timeout: float = 20.0) -> dict
# 返回 FetchResult:
# {"source_id": str, "ok": bool, "error": str|None, "http_status": int|None,
#  "fetched_at": ISO UTC str, "items": [RawItem, ...], "invalid_count": int}
# 网络错误/HTTP非2xx/XML解析失败 -> ok=False, items=[], error 写明原因；绝不抛异常。

def parse_feed(data: bytes) -> tuple[list[dict], int]
# 纯函数，支持 RSS 2.0 与 Atom（及常见命名空间 content:encoded / dc:date）。返回 (items, invalid_count)。
# RawItem:
# {"guid": str|None,          # RSS <guid> / Atom <id>
#  "title": str,              # 去首尾空白，HTML 实体已解码；无标题用 ""
#  "link": str|None,          # 原文链接（Atom 取 rel=alternate 或第一个 href）
#  "summary": str,            # 去 HTML 标签、解码实体、压缩空白后的纯文本，最长 500 字符（超出加 "…"）；无则 ""
#  "published_raw": str|None, # 原始日期字符串（pubDate/published/updated/dc:date 依次取）
#  "published_at": str|None,  # 解析成功 -> ISO UTC 字符串；否则 None
#  "date_status": "ok"|"missing"|"unparseable"}
# 无效条目（既无 link 也无 guid，或既无 title 也无 link）丢弃并计入 invalid_count。
# 无时区日期按 UTC 解释并在 docs/SOURCES.md 记录该规则。

def filter_by_window(items: list[dict], start: datetime, end: datetime) -> tuple[list[dict], dict]
# start/end 为 UTC aware。规则：date_status=="ok" 且 start <= published_at <= end 保留；
# 日期缺失/无法解析的条目保留（如实标记，不伪造时间，由主控去重防止重复新增）；
# 返回 (kept, stats)，stats = {"total": n, "in_window": n, "out_of_window": n, "no_date_kept": n}
```

## 3. 主控提供给 B 的 Report 字典（B 只负责渲染字符串，不写文件）

```python
report = {
  "report_date": "2026-11-26",               # 新加坡日期
  "generated_at": "2026-11-26 09:00 SGT",     # 已格式化
  "timezone": "Asia/Singapore",
  "categories": ["模型与研究", "产品与工具", "行业与政策", "待确认"],
  "articles_by_category": {"模型与研究": [Article, ...], ...},  # 四类键都存在，可为空列表
  "total_count": 12,                          # 当日日报总条数
  "latest_run": Run,                          # 本次运行
  "runs": [Run, ...],                         # 当日所有运行（时间升序）
  "sources": [{"id","name","website","rss_url","language"}],
}
Article = {
  "id": str, "title": str, "link": str|None, "source_id": str, "source_name": str,
  "published_display": "2026-11-26 08:30 SGT" 或 "日期缺失" / "日期无法解析（原文：xxx）",
  "date_status": "ok"|"missing"|"unparseable",
  "collected_display": "2026-11-26 09:00 SGT",
  "summary": str,            # 纯文本，可能为空
  "category": str, "category_reason": str,   # 例如 "命中：大模型, GPT"
  "is_new": bool,            # 是否本次运行新增
}
Run = {
  "run_id": str, "started_display": str, "finished_display": str,
  "window_display": "2026-11-23 09:00 SGT — 2026-11-26 09:00 SGT",
  "mode": "initial"|"incremental"|"manual"|"mixed",
  "new_count": int,
  "sources": [{"source_id","source_name","status": "failed"|"ok_no_new"|"ok_new",
               "window_display": str, "fetched_count": int, "in_window_count": int,
               "relevant_count": int, "new_count": int, "error": str|None}],
}
```

## 4. report.py（B 实现）

```python
STATUS_LABELS = {"failed": "读取失败", "ok_no_new": "成功·无更新", "ok_new": "成功·有更新"}
def render_html(report: dict) -> str     # 单文件 HTML，内联 CSS，无 JS，响应式（手机/电脑）
def render_markdown(report: dict) -> str
def render_index(entries: list[dict]) -> str  # 历史日报索引 HTML；entry={"date","total_count","html":"2026-11-26.html","md":"2026-11-26.md"}，日期倒序
```
要求：所有外部内容（标题、简介、链接、来源名、错误信息）必须 `html.escape`；链接只允许 http/https，否则不生成 `<a>`；`<a>` 加 `target="_blank" rel="noopener noreferrer"`；加 CSP meta `default-src 'none'; style-src 'unsafe-inline'; img-src 'none'`。Markdown 需转义 `[]()` 等影响链接的字符。
页面须写明：收集范围、更新时间、来源状态表（区分三种状态，失败显示错误原因）、本次新增数、日报总数、分类说明、空分类显示“本类暂无条目”、全部为空/全部失败时有明确说明、声明“内容仅基于 RSS 标题与简介，未阅读原文全文”。界面文字中文，标题/简介保留原语言。
