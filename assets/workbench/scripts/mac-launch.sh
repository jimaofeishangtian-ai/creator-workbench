#!/bin/bash
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
workbench_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$workbench_root"
action="${1:-start}"
case "$action" in install|start|reset-password) ;; *) echo 'Unknown action'; exit 2 ;; esac
if [[ "$action" != install && -x .venv/bin/python ]]; then
  workbench_python="$workbench_root/.venv/bin/python"
else
  workbench_python=''
  for candidate in python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3,10))'; then
      workbench_python="$(command -v "$candidate")"
      break
    fi
  done
fi
if [[ -z "$workbench_python" ]]; then
  echo '需要 Python 3.10+，建议安装官方 Python 3.12，再重新打开。'
  read -r -p '按回车关闭…' || true
  exit 1
fi
if ! "$workbench_python" "scripts/$action.py"; then
  echo '操作未完成；请把上面的提示交给电脑 AI 助手。'
  read -r -p '按回车关闭…' || true
  exit 1
fi
