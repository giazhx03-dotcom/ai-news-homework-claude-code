# AI 行业新闻收集助手（ai-news-homework）

每天从两个公开 RSS 来源收集 AI 行业新闻。程序会按 72 小时首次窗口和后续增量窗口抓取，然后去重、按规则筛选 AI 相关内容、分成四类，最后生成可点击原文的 **HTML / Markdown 日报**和**结构化 JSON**。定时由 macOS 用户级 launchd 在每天 09:00（Asia/Singapore）运行。

- 只用 Python 3.9+ 标准库，无第三方依赖，不需要付费服务或模型 API。
- 日报内容仅基于 RSS 标题与简介，**不阅读原文全文**。

## 快速开始

```bash
scripts/setup.sh                    # 创建 .venv（仅标准库）
.venv/bin/python -m ainews run      # 收集并生成日报（首次：执行前 72 小时）
open reports/latest.html            # 查看最新日报
```

其他命令：

```bash
.venv/bin/python -m ainews run --since "2026-11-24 00:00" --until "2026-11-25 00:00"  # 手动区间（无时区按新加坡时间）
.venv/bin/python -m ainews run --sources qbitai           # 只跑某个来源
.venv/bin/python -m ainews run --force-fail techcrunch_ai # 模拟某来源失败（验证用）
.venv/bin/python -m ainews report [--date 2026-11-26]     # 用已存数据重新生成日报
.venv/bin/python -m ainews status                          # 来源状态与最近运行
.venv/bin/python -m unittest discover -s tests -t . -v     # 运行全部测试（模拟数据）
AINEWS_LIVE=1 .venv/bin/python -m unittest tests.test_fetcher.LiveFetchTest -v  # 真实网络测试
```

退出码：0 表示至少一个来源成功，1 表示全部来源失败，75 表示已有实例在运行（被锁跳过）。

## RSS 来源

| 来源 | RSS | 语言 | 侧重 |
|---|---|---|---|
| 量子位 | https://www.qbitai.com/feed | 中文 | 中文 AI 垂直媒体：大模型、产品、国内外 AI 公司 |
| TechCrunch AI | https://techcrunch.com/category/artificial-intelligence/feed/ | 英文 | 全球 AI 行业报道：创业融资、产品、政策 |

验证记录、选择原因、淘汰候选（36氪、机器之心、VentureBeat 等）见 [docs/SOURCES.md](docs/SOURCES.md)。

## 运行逻辑

- **时间窗口**
  - 首次运行（来源还没有成功记录）：抓取 `[执行时间-72h, 执行时间]`。
  - 之后每个来源：从 `[上次成功窗口终点-6h, 执行时间]` 增量抓取，重叠部分靠去重处理。
  - `--since/--until` 手动指定区间时，**不推进**增量位置。
- **失败隔离**
  - 单个来源读取失败或出异常时，只记录错误，**不推进**该来源的成功记录，其他来源照常处理。
  - 恢复后从上次成功的位置开始补取。
- **去重**：每条文章有两类去重键，任一已存在即视为重复。
  - `guid:<来源>:<条目ID>`
  - `url:<规范化链接>`：统一 https、去掉 www、片段和 utm 等追踪参数，查询参数排序，去掉尾斜杠。
  - 两类键都存在 SQLite（`data/news.db`）里，重启后仍然有效。
- **日期**
  - 内部统一用 UTC 存储，展示统一用 Asia/Singapore。
  - 缺失的日期标“日期缺失”，无法解析的标“日期无法解析（原文：…）”，不伪造。
  - 这两类条目不受时间窗口限制，靠去重避免重复新增。
- **筛选与分类**：先判断 AI 相关性，再分成“模型与研究 / 产品与工具 / 行业与政策 / 待确认”四类。详见 [docs/RULES.md](docs/RULES.md)。
- **日报**
  - 按新加坡日期归档：`reports/YYYY-MM-DD.html|md`、`data/daily/YYYY-MM-DD.json`。
  - 最新一份：`reports/latest.*`、`data/latest.json`。
  - 历史索引：`reports/index.html`。
  - 同一天多次运行时，从数据库重新生成当日的全部内容，**只增不减**。
  - 页面会标出本次新增的条目，并区分三种来源状态：读取失败 / 成功·无更新 / 成功·有更新。
- **安全**
  - HTML 中所有外部内容都做了转义，链接只放行 http/https。
  - 加了 CSP `default-src 'none'`，页面不含任何脚本。
- **防并发**：用 `fcntl.flock` 给 `data/.run.lock` 加锁，SQLite 开启 WAL，文件先写临时文件再原子替换。

## 目录

```
ainews/            fetcher.py(读取/解析/时间窗口) report.py(HTML/MD渲染) pipeline.py(主流程)
                   storage.py(SQLite) dedupe.py classify.py timeutil.py __main__.py(CLI)
config/            sources.json  rules.json
tests/             test_fetcher.py test_report.py test_pipeline.py fixtures/
scheduling/        launchd 模板与本机生成的 plist
scripts/           setup.sh  schedule.sh
reports/ data/     日报与结构化数据（提交真实样例；news.db 不提交）
logs/              运行日志（不提交）
docs/              CONTRACT.md SOURCES.md RULES.md SCHEDULING.md PROGRESS.md
```

## 定时任务

```bash
scripts/schedule.sh install
scripts/schedule.sh status
scripts/schedule.sh trigger
scripts/schedule.sh disable
scripts/schedule.sh uninstall
```

关机、休眠、未登录时的行为，以及 **macOS 桌面目录权限（TCC）** 的处理方法，见 [docs/SCHEDULING.md](docs/SCHEDULING.md)。
