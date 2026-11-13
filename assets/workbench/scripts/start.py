from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA = ROOT / "runtime"
RUNTIME = DEFAULT_DATA


def health():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8765/api/health", timeout=2) as r:
            j = json.loads(r.read())
            if j.get("app") == "AIWorkbench":
                expected=hashlib.sha256(str(RUNTIME.resolve()).encode()).hexdigest()[:16]
                if j.get("instance_id") != expected:
                    raise SystemExit("8765端口已有另一套工作台。请使用那套工作台，或由用户正常关闭后再启动此分享包；不会覆盖旧数据。")
                return j
            return None
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    if not health():
        python = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not python.exists() or not (ROOT / "frontend/dist/index.html").exists():
            print("请先运行「安装工作台.cmd」。")
            return 1
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "backend")
        env["PYTHONIOENCODING"] = "utf-8"
        with (RUNTIME / "server.log").open("a", encoding="utf-8") as log:
            process = subprocess.Popen([str(python), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8765", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1", "--no-access-log"], cwd=ROOT, env=env, stdout=log, stderr=log, creationflags=0x08000000 if os.name == "nt" else 0)
            (RUNTIME / "server.pid").write_text(str(process.pid), encoding="ascii")
        for _ in range(45):
            if health():
                break
            time.sleep(1)
        else:
            print("启动未成功，请查看数据目录的server.log，或检查8765端口是否占用。")
            return 1
    url = "http://127.0.0.1:8765/"
    code = RUNTIME / "initial-setup-code.txt"
    if code.exists():
        url += "#setup=" + code.read_text(encoding="utf-8").strip()
    if not args.no_browser:
        webbrowser.open(url)
    print("AI创作台正在运行：http://127.0.0.1:8765/")
    print("首次打开请设置密码；模型和数据服务在「我的」中配置。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
