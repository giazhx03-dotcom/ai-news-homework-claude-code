# 定时运行（macOS 用户级 launchd）

| 项 | 值 |
|---|---|
| 任务标识 | `com.ai-news-homework-claude-code.<项目路径 SHA1 前 8 位>`，本机为 `com.ai-news-homework-claude-code.2f4452a3` |
| 时间 | 每天 09:00，用 `StartCalendarInterval` 设定，按**系统本地时区**解释 |
| 解释器 | `<项目>/.venv/bin/python -m ainews run`（明确的绝对路径） |
| 工作目录 | 项目根目录（`WorkingDirectory`） |
| 配置模板 | `scheduling/ainews.plist.template` |
| 本机生成的配置 | `scheduling/com.ai-news-homework-claude-code.2f4452a3.plist`（含本机绝对路径，已 gitignore，由 `schedule.sh generate` 生成） |
| 安装位置 | `~/Library/LaunchAgents/<标识>.plist` |
| 日志 | `logs/run.log`（每次运行的来源状态、数量、错误）、`logs/runs.jsonl`（结构化记录）、`logs/launchd.out.log` / `logs/launchd.err.log`（进程输出） |
| 防并发 | 程序用 `fcntl.flock` 给 `data/.run.lock` 加非阻塞锁，第二个实例直接退出（退出码 75）；launchd 本身也不会并行启动同一个任务 |

## 命令

```bash
scripts/schedule.sh generate    # 只生成 plist 并用 plutil 校验
scripts/schedule.sh install     # 生成、复制到 LaunchAgents、bootstrap 并 enable
scripts/schedule.sh status      # 查看加载状态、运行次数、上次退出码、最近日志、权限提示
scripts/schedule.sh trigger     # launchctl kickstart，立即触发一次
scripts/schedule.sh disable     # 停用（保留 plist）
scripts/schedule.sh enable      # 重新启用
scripts/schedule.sh uninstall   # 卸载并删除 plist
```

改执行时间：`AINEWS_HOUR=8 AINEWS_MINUTE=30 scripts/schedule.sh install`

## 时区

`install` 和 `status` 会检查 `/etc/localtime`。本机是 `Asia/Singapore`，所以 launchd 的 09:00 就是新加坡 09:00。如果系统时区改了，launchd 会跟着按新时区触发，脚本会给出警告。程序内部的时间计算和展示都显式使用 `Asia/Singapore`，不受系统时区影响。

## 限制

- **关机**：到点时关机，这一次不会补跑。下次运行时，增量窗口会从上次成功的位置开始，只要 RSS 里还保留着这段时间的条目，就能补取。
- **休眠**：到点时处于睡眠，launchd 会在唤醒后补跑一次（多个错过的时间点合并为一次）。
- **未登录**：这是用户级 LaunchAgent，必须登录图形会话才会运行；注销或在登录界面时不运行。
- **macOS 隐私保护（TCC）**：项目在“桌面”目录下，后台进程默认不能读取。2026-11-26 实测，触发后 Python 报错：`PermissionError: Operation not permitted: .../.venv/pyvenv.cfg`。解决方法（二选一，需要你手动操作）：
  1. 打开“系统设置 → 隐私与安全性 → 完全磁盘访问权限”，点“+”，在文件选择框中按 Cmd+Shift+G，粘贴 venv 实际解析到的可执行文件 `/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3.9`（`status` 会打印这个路径），添加后确认开关已打开。然后清空 `logs/launchd.err.log`，执行 `scripts/schedule.sh trigger` 验证。
     - 2026-11-26 02:46 实测：只授权 `Resources/Python.app`，由 launchd 启动的该 App 内可执行文件**仍然**无法读取 `~/Desktop`（`Operation not permitted`）。所以授权对象必须是上面的 `bin/python3.9`，不能是 Python.app。
  2. 把项目移到非受保护目录（如 `~/Projects/`），然后重新 `scripts/setup.sh` 和 `scripts/schedule.sh install`（任务标识会随路径变化）。

## 验证状态的区分

见 `docs/PROGRESS.md`：配置已生成 / 任务已加载启用 / 手动触发 / 触发后执行成功 / 自然运行。
