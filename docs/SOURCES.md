# RSS 来源说明（子Agent A）

验证方式：本机真实网络，使用 Python `urllib`（UA：`ai-news-homework/1.0 (+RSS reader)`，同时对部分候选用 curl 默认 UA 和浏览器 UA 复测），并用 `ainews/fetcher.py` 实际解析统计条目数。
验证时间：2026-11-25 18:27–18:30 UTC（= 2026-11-26 02:27–02:30 SGT）。"72 小时内"指验证时刻往前 72 小时。

## 选定来源

### 1. 量子位（qbitai）

| 项 | 内容 |
|---|---|
| 网站 | https://www.qbitai.com/ |
| RSS 地址 | https://www.qbitai.com/feed |
| 语言 | 中文（zh） |
| 验证结果 | HTTP 200，RSS 2.0，10 条，无效 0 条，最新条目 `Wed, 25 Nov 2026 16:34:09 +0000`（2026-11-25T16:34:09Z），72 小时内 10/10 条 |
| 内容侧重点 | 中文 AI 垂直媒体：大模型、AI 产品与应用、国内外 AI 公司与行业动态 |
| 选择原因 | AI 专门媒体，条目基本都与 AI 相关；公开无需登录；日更多篇；提供中文、国内视角 |

### 2. TechCrunch AI（techcrunch_ai）

| 项 | 内容 |
|---|---|
| 网站 | https://techcrunch.com/category/artificial-intelligence/ |
| RSS 地址 | https://techcrunch.com/category/artificial-intelligence/feed/ |
| 语言 | 英文（en） |
| 验证结果 | HTTP 200，RSS 2.0（`application/rss+xml`），20 条，无效 0 条，最新条目 `Wed, 25 Nov 2026 18:11:46 +0000`，72 小时内 20/20 条 |
| 内容侧重点 | 科技行业媒体的 AI 分类：AI 创业公司、融资、产品发布、政策监管、大厂动态 |
| 选择原因 | 更新最频繁的英文行业报道之一；公开无需登录；与量子位形成一中一英、国内/全球互补 |

备注：主控初测时 curl 返回 403；本次复测 curl 默认 UA、本项目 UA 均返回 200。该站可能存在间歇性反爬，若后续失败，fetcher 会如实返回 `ok=False` 与错误原因，备选替换为 The Verge AI 或 MIT Technology Review AI（见下表）。

## 候选与淘汰记录（均为真实请求结果）

| 候选 | RSS 地址 | 结果 | 结论 |
|---|---|---|---|
| 36氪 | https://36kr.com/feed | HTTP 200，但 `Content-Type: text/html`，返回 HTML 页面（`<!DOCTYPE html>`），0 条 | 淘汰：不是有效 RSS |
| 机器之心 | https://www.jiqizhixin.com/rss | HTTP 302 → https://www.jiqizhixin.com/data-service（HTML 页面），0 条 | 淘汰：RSS 已不可用 |
| VentureBeat AI | https://venturebeat.com/category/ai/feed/ （及 /feed/） | HTTP 429 Too Many Requests（本项目 UA 与浏览器 UA 均如此） | 淘汰：被限流，不稳定 |
| OpenAI News | https://openai.com/news/rss.xml | HTTP 200，RSS 2.0，1430 条（全量历史），最新 2026-11-24T13:00:00Z，72 小时内 8 条 | 未选：可用，但官方单一公司、更新频率较低且每次下载全量历史，作备选 |
| The Verge AI | https://www.theverge.com/rss/ai-artificial-intelligence/index.xml | HTTP 200，Atom，10 条，最新 2026-11-25T17:44:51Z，72 小时内 10/10 | 未选：可用，但与 TechCrunch 同为英文行业媒体，作英文备选（Atom 格式，可替换 TechCrunch） |
| MIT Technology Review AI | https://www.technologyreview.com/topic/artificial-intelligence/feed | HTTP 200，RSS 2.0，10 条，最新 2026-11-25T10:00:00Z | 未选：可用但更新较慢（10 条跨度 2026-11-12 至 2026-11-25），作备选 |
| IT之家 | https://www.ithome.com/rss/ | HTTP 200，RSS 2.0，60 条，最新 2026-11-25T18:08:53Z | 未选：综合科技资讯，AI 条目占比低 |
| Google AI Blog | https://blog.google/technology/ai/rss/ | HTTP 200（重定向到 https://blog.google/innovation-and-ai/technology/ai/rss/），RSS 2.0，20 条，最新 2026-11-24T16:00:00Z | 未选：单一公司官方博客，更新较低频 |
| Hugging Face Blog | https://huggingface.co/blog/feed.xml | HTTP 200，RSS 2.0，917 条（全量），最新 2026-11-25T10:22:09Z | 未选：偏技术博客且全量体积大 |

## 日期解析规则

- 取值顺序：RSS 取 `<pubDate>`，没有则 `<dc:date>`；Atom 取 `<published>`，没有则 `<updated>`，再没有则 `<dc:date>`。原始字符串原样存入 `published_raw`（去首尾空白）。
- 解析顺序：先按 RFC 822/1123（`email.utils.parsedate_to_datetime`，如 `Wed, 25 Nov 2026 16:34:09 +0000`、`... GMT`），失败再按 ISO 8601（支持 `Z`、`±HH:MM`、`±HHMM`、毫秒/微秒、`T` 或空格分隔、仅日期 `YYYY-MM-DD`）。
- 成功：转换为 UTC，输出 `published_at` 为 ISO 字符串（秒级，如 `2026-11-25T16:34:09+00:00`），`date_status="ok"`。
- **无时区**的日期（如 `2026-11-25T10:00:00`）一律按 **UTC** 解释。
- 缺失或空白：`published_at=None`，`date_status="missing"`。
- 无法解析（如 `昨天下午`、`not a date`、非法月份日期）：`published_at=None`，`date_status="unparseable"`，保留 `published_raw` 供展示。
- 时间窗口：`date_status=="ok"` 且 `start <= published_at <= end`（两端闭区间）才算窗口内；缺失/无法解析的条目**保留**并计入 `no_date_kept`，不伪造时间。

## 无效条目规则

- 既无 `link` 也无 `guid`（Atom 为 `<id>`）→ 无效。
- 既无 `title` 也无 `link` → 无效。
- 无效条目丢弃并计入 `invalid_count`。只有 guid 而无 link、但有标题的条目视为有效（`link=None`）。

## 文本清洗与安全

- 标题与简介：去除 HTML 标签（`<script>`/`<style>` 连同内容一并删除）、解码 HTML 实体、压缩空白；简介优先 `description`/`summary`，为空时用 `content:encoded`/`content`，最长 500 字符，超出截断并加 `…`。清洗后的文本仍属外部内容，渲染时必须再 `html.escape`。
- 网络：超时默认 20 秒；自动跟随重定向；支持 gzip/deflate；`Content-Length` 或实际读取/解压后超过 10MB 即拒绝；仅允许 http/https 地址。
- XML：使用 `xml.etree.ElementTree`（不解析外部实体），并拒绝含 `<!DOCTYPE`/`<!ENTITY` 的文档（防实体扩展，同时能识别出伪装成 feed 的 HTML 页面）。
- 任何错误（DNS 失败、拒绝连接、超时、HTTP 非 2xx、XML 错误、非 RSS/Atom 根元素）`fetch_feed` 均返回 `ok=False`、`items=[]` 与中文错误原因，不抛异常。
