#!/bin/bash
# 定时任务管理（macOS 用户级 launchd）
# 用法：scripts/schedule.sh {generate|install|status|trigger|disable|enable|uninstall}
set -euo pipefail
PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$PROJECT/.venv/bin/python"
HASH="$(printf '%s' "$PROJECT" | shasum -a 1 | cut -c1-8)"
LABEL="com.ai-news-homework-claude-code.${HASH}"   # 含项目唯一标识（项目路径哈希）
HOUR="${AINEWS_HOUR:-9}"; MINUTE="${AINEWS_MINUTE:-0}"
GEN="$PROJECT/scheduling/${LABEL}.plist"
DEST="$HOME/Library/LaunchAgents/${LABEL}.plist"
DOMAIN="gui/$(id -u)"
EXPECT_TZ="Asia/Singapore"

check_tz() {
  local tz; tz="$(readlink /etc/localtime | sed 's#.*/zoneinfo/##')"
  echo "系统时区：$tz（launchd 按系统时区触发）"
  if [ "$tz" != "$EXPECT_TZ" ]; then
    echo "警告：系统时区不是 $EXPECT_TZ，09:00 将按 $tz 触发；可设 AINEWS_HOUR 调整后重新 install" >&2
  fi
}

generate() {
  [ -x "$PYTHON" ] || { echo "缺少 $PYTHON，请先运行 scripts/setup.sh" >&2; exit 1; }
  mkdir -p "$PROJECT/logs"
  sed -e "s#__LABEL__#${LABEL}#g" -e "s#__PYTHON__#${PYTHON}#g" -e "s#__PROJECT__#${PROJECT}#g" \
      -e "s#__HOUR__#${HOUR}#g" -e "s#__MINUTE__#${MINUTE}#g" \
      "$PROJECT/scheduling/ainews.plist.template" > "$GEN"
  plutil -lint "$GEN"
  echo "已生成配置：$GEN"
}

case "${1:-}" in
  generate) check_tz; generate ;;
  install)
    check_tz; generate
    mkdir -p "$HOME/Library/LaunchAgents"
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    cp "$GEN" "$DEST"
    launchctl bootstrap "$DOMAIN" "$DEST"
    launchctl enable "$DOMAIN/$LABEL"
    echo "已安装并启用：$LABEL（每天 $(printf '%02d:%02d' "$HOUR" "$MINUTE")）" ;;
  status)
    check_tz
    echo "标签：$LABEL"; echo "plist：$DEST"
    [ -f "$DEST" ] && echo "plist 已安装" || echo "plist 未安装"
    launchctl print "$DOMAIN/$LABEL" 2>/dev/null | grep -E "state =|runs =|last exit code|program =|working directory" \
      || echo "launchd 中未加载"
    launchctl print-disabled "$DOMAIN" 2>/dev/null | grep "$LABEL" || true
    echo "--- 最近运行日志 ---"; tail -n 5 "$PROJECT/logs/run.log" 2>/dev/null || echo "暂无日志"
    echo "--- launchd 错误输出（末 5 行）---"; tail -n 5 "$PROJECT/logs/launchd.err.log" 2>/dev/null || echo "无"
    if grep -q "Operation not permitted" "$PROJECT/logs/launchd.err.log" 2>/dev/null; then
      echo "提示：macOS 隐私保护阻止后台进程访问项目目录（桌面）。请在 系统设置→隐私与安全性→完全磁盘访问权限 中添加："
      echo "  $(dirname "$(dirname "$(readlink -f "$PYTHON")")")/Resources/Python.app（真实解释器：$(readlink -f "$PYTHON")）"
      echo "  然后执行：scripts/schedule.sh trigger 验证（清空 logs/launchd.err.log 后再看结果）"
    fi ;;
  trigger)
    launchctl kickstart -p "$DOMAIN/$LABEL" && echo "已通过 launchd 立即触发一次（结果见 logs/）" ;;
  disable)
    launchctl disable "$DOMAIN/$LABEL"; launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    echo "已停用（plist 保留，可 enable 恢复）" ;;
  enable)
    launchctl enable "$DOMAIN/$LABEL"
    launchctl bootstrap "$DOMAIN" "$DEST" 2>/dev/null || true
    echo "已重新启用" ;;
  uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    launchctl enable "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$DEST"; echo "已卸载：$LABEL" ;;
  *) echo "用法：$0 {generate|install|status|trigger|disable|enable|uninstall}"; exit 2 ;;
esac
