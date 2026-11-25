# 进度记录

时间均为 Asia/Singapore。截止时间：2026-11-28 12:00。

## 2026-11-26 环境检查（02:25）

- 项目路径：`~/Desktop/<项目目录>`（初始为空目录）。
- 系统：macOS 26.6；Python：`/usr/bin/python3` 3.9.6（CommandLineTools）；Git 2.50.1。
- 网络正常；系统时区 `/etc/localtime → Asia/Singapore`。
- GitHub：没有 gh CLI，没有 SSH 密钥，钥匙串中没有 github.com 凭据，也没有 GH_TOKEN 环境变量。

## 分工（主控 + 子Agent）

| 角色 | 负责 | 结果 |
|---|---|---|
| 主控 | 制定 `docs/CONTRACT.md`（数据字段、接口、文件归属）；实现 timeutil / storage / dedupe / classify / pipeline / CLI；定时；Git；文档 | 完成 |
| 子Agent A | 验证 RSS 来源，实现 `fetcher.py`，编写 `tests/test_fetcher.py` 和测试数据，撰写 `docs/SOURCES.md` | 验证 11 个候选，选定量子位、TechCrunch AI；32 项测试 |
| 子Agent B | 按约定格式实现 `report.py`（HTML/MD/索引），编写 `tests/test_report.py` | 21 项测试 |
| 独立审查子Agent | 只读审查，对照验收要求逐项检查 | 8 项中 5 项满足、3 项部分满足；提出 8 个问题，主控已修复其中第 2–8 项 |

## 审查问题处理

| # | 问题 | 处理 |
|---|---|---|
| 1 | launchd 触发后 Python 无法读取桌面目录（TCC） | **未解决，需要用户授权**，方法见 docs/SCHEDULING.md |
| 2 | 提交了含本机路径的 plist | 已从 Git 移除并加入 gitignore，只保留模板 |
| 3 | `--until` 早于 `--since` 时静默通过 | 现在报错，退出码 2；时间格式错误也给出友好提示 |
| 4 | Claude/Gemini 等歧义词误收（如 Monet、horoscope） | 改为区分大小写，且必须同时有其他 AI 信号 |
| 5 | “AI 天气模型”、“AI 电影”被体育娱乐规则误杀 | 去掉“天气”；标题有独立 AI 时归入待确认 |
| 6 | 只有语境词也进入待确认 | 现在至少需要一个强信号 |
| 7 | 当日已有条目、本次运行失败时没有醒目提示 | 新增失败提示（HTML 与 MD） |
| 8 | 空来源、regenerate 生成空日报、日报生成异常时缺日志、Markdown `&` | 均已修复并补充测试 |

## 真实运行记录（真实网络）

规则修正后，清空了之前的数据（旧数据已移到会话临时目录，没有删除），然后按顺序执行：

| 运行 | 时间 | 模式/参数 | 结果 |
|---|---|---|---|
| 20261126T023954-908e | 02:39 | 首次（各来源 2026-11-23 02:39 — 2026-11-26 02:39） | 量子位 抓取 10、相关 8、新增 8；TechCrunch AI 抓取 20、相关 20、新增 20；**共 28 条** |
| 20261126T023957-d469 | 02:39 | 增量（窗口从 20:39 起，即重叠 6h） | 新增 0，两个来源各有 5 条重复 → 重复运行不会重复新增 |
| 20261126T023958-7643 | 02:40 | 增量 | 新增 0 |
| 20261126T024004-fe5f | 02:40 | 增量 + `--force-fail techcrunch_ai`（**模拟故障**，网络真实） | 量子位成功；TechCrunch 失败，成功位置保持 02:39 不变，连续失败 1 次 |
| 20261126T024006-c895 | 02:40 | 增量（恢复） | TechCrunch 窗口从 20:39 起（上次成功位置 − 6h），状态恢复为 ok，连续失败清零 |
| 20261126T024009-a171 | 02:40 | 手动 `--since 2026-11-24T00:00 --until 2026-11-25T00:00` | 窗口内 3 条，全部重复；手动区间不推进增量位置 |
| 20261126T024026-e9b1 | 02:40 | 增量 | 新增 0；当日日报保留 28 条（同日 7 次运行，内容没有丢失） |

当日日报分类：模型与研究 1、产品与工具 9、行业与政策 11、待确认 7。

“故障后补取遗漏文章”需要真实时间流逝，所以用模拟数据测试：`test_single_source_failure_and_recovery`。

## 定时任务验证

| 阶段 | 状态 |
|---|---|
| 配置生成 | ✅ `plutil -lint` OK |
| 任务安装启用 | ✅ `launchctl print` 显示已加载、enabled，program 和 working directory 正确 |
| 手动触发（kickstart） | ✅ launchd 确实启动了进程（runs=2） |
| 触发后执行成功 | ✅ 2026-11-26 02:58 用户为 `bin/python3.9` 授予完全磁盘访问权限后，kickstart 触发 run `20261126T025835-4c14`，退出码 0，两个来源均成功（无更新），日报已更新（此前因 TCC 失败，见下） |
| 自然运行（09:00） | ⏳ 尚未到时间，待首次 09:00 自然运行后在 logs/run.log 核对 |

### 2026-11-26 02:45–02:48 授权后复验（用户表示已授权）

| 步骤 | 结果 |
|---|---|
| 清空 err 日志，用 venv 解释器触发 | ❌ 仍报 `PermissionError ... .venv/pyvenv.cfg` |
| 定位原因 | launchd 实际执行的是 `.../Versions/3.9/bin/python3.9`，不是 `Resources/Python.app`（两者签名标识相同，都是 com.apple.python3）；之前文档让授权的对象写错了 |
| 临时改为直接用 `Python.app/Contents/MacOS/Python` 触发 | ❌ `No module named ainews`（读不到工作目录） |
| 用一次性诊断任务（launchctl submit，已移除）让该解释器列目录 | ❌ `~/Desktop` 和项目目录都是 `Operation not permitted`，说明对 Python.app 的授权对 launchd 启动的进程没有生效 |
| 恢复为 venv 解释器并重新 install，再触发 | ❌ 同样的权限错误；`status` 现在会提示正确的授权路径 `bin/python3.9` |

当时结论：定时任务“已安装、已启用、能触发，但执行被 TCC 拦截”。02:58 授权 `bin/python3.9` 后已解决（见上表）。需要把 `bin/python3.9` 加入完全磁盘访问权限，或者把项目移出桌面。

## 测试

`.venv/bin/python -m unittest discover -s tests -t . -v`：共 77 项，全部 OK，跳过 2 项（真实网络测试需设 `AINEWS_LIVE=1`，样例输出需设 `AINEWS_WRITE_SAMPLE=1`）。
真实网络测试 `AINEWS_LIVE=1 ... LiveFetchTest`：两个来源都 ok（量子位 10 条，TechCrunch 20 条）。

## Git 版本记录

见 `git log`。各提交：
- `d2b84be` v1.0：初始实现与首次真实运行样例
- （本次提交）审查问题修复、规则修正、真实运行重做、进度记录
