"""命令行入口：python -m ainews {run,report,status}（主控维护）"""
from __future__ import annotations

import argparse
import json
import sys

from . import pipeline
from .storage import Store
from .timeutil import fmt_sgt, parse_cli_time


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="ainews", description="AI 行业新闻收集助手")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="收集新闻并生成日报（默认：首次72小时，之后按来源增量）")
    r.add_argument("--since", help="手动起始时间，ISO 格式；无时区按 Asia/Singapore（手动区间不推进增量位置）")
    r.add_argument("--until", help="手动结束时间，同上；默认当前时间")
    r.add_argument("--sources", help="只处理指定来源 id，逗号分隔")
    r.add_argument("--force-fail", help="模拟指定来源读取失败（仅验证用），逗号分隔")
    g = sub.add_parser("report", help="根据已存数据重新生成日报")
    g.add_argument("--date", help="新加坡日期 YYYY-MM-DD，默认今天")
    sub.add_parser("status", help="查看来源状态与最近运行")
    a = p.parse_args(argv)

    if a.cmd == "run":
        try:
            since = parse_cli_time(a.since) if a.since else None
            until = parse_cli_time(a.until) if a.until else None
        except ValueError as e:
            print(f"时间格式错误（示例：2026-11-25 或 2026-11-25T09:00 或 2026-11-25T01:00Z）：{e}", file=sys.stderr)
            return 2
        try:
            rec = pipeline.run(
                since=since, until=until,
                only=a.sources.split(",") if a.sources else None,
                force_fail=a.force_fail.split(",") if a.force_fail else None,
            )
        except pipeline.LockBusy as e:
            print(f"跳过：{e}", file=sys.stderr)
            return 75
        except ValueError as e:
            print(f"参数错误：{e}", file=sys.stderr)
            return 2
        print(f"运行 {rec['run_id']}（{rec['mode']}）完成，本次新增 {rec['new_count']} 条")
        for s in rec["sources"]:
            print(f"  - {s['source_name']}: {s['status']} 窗口 {fmt_sgt(s['window_start'])} — {fmt_sgt(s['window_end'])}"
                  f" 抓取{s['fetched_count']} 窗口内{s['in_window_count']} 相关{s['relevant_count']}"
                  f" 新增{s['new_count']} 重复{s['duplicate_count']}" + (f" 错误：{s['error']}" if s["error"] else ""))
        for k, v in rec["outputs"].items():
            print(f"  {k}: {v}")
        return 0 if any(s["status"] != "failed" for s in rec["sources"]) else 1
    if a.cmd == "report":
        try:
            print(json.dumps(pipeline.regenerate(report_date=a.date), ensure_ascii=False, indent=2))
        except ValueError as e:
            print(f"无法生成：{e}", file=sys.stderr)
            return 2
        return 0
    if a.cmd == "status":
        st = Store(pipeline.Paths(pipeline.PROJECT_ROOT).db)
        for s in st.all_states():
            print(f"{s['source_id']}: 状态={s['last_status']} 上次成功={fmt_sgt(s['last_success_at'], '无')} "
                  f"成功窗口终点={fmt_sgt(s['last_window_end'], '无')} 连续失败={s['consecutive_failures']} 错误={s['last_error'] or '-'}")
        for r in st.last_runs(5):
            print(f"run {r['run_id']} {r['mode']} 新增{r['new_count']} " +
                  " ".join(f"{x['source_id']}={x['status']}" for x in r["sources"]))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
