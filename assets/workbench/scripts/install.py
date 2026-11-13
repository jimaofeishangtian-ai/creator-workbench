from __future__ import annotations
import os
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser()
parser.add_argument('--with-asr', action='store_true', help='可选：安装本地语音转写组件；仅推荐与收藏不需要')
args = parser.parse_args()
if sys.version_info < (3, 10):
    raise SystemExit("需要Python 3.10或更高版本。")
python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
if not python.exists():
    subprocess.run([sys.executable, "-m", "venv", str(root / ".venv")], check=True)
requirements = ["-r", str(root / "backend/requirements.txt")]
if args.with_asr:
    requirements += ["-r", str(root / "backend/requirements-asr.txt")]
subprocess.run([str(python), "-m", "pip", "install", *requirements], check=True)
npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
if not npm:
    raise SystemExit("需要Node.js和npm。请从nodejs.org安装当前LTS版本。")
subprocess.run([npm, "ci", "--no-audit", "--no-fund"], cwd=root / "frontend", check=True)
subprocess.run([npm, "run", "build"], cwd=root / "frontend", check=True)
if not shutil.which("ffmpeg"):
    print("提示：尚未找到ffmpeg，字幕粘贴和文案生成可用，本机音频转写需先安装ffmpeg。")
print("安装完成。运行「启动工作台.cmd」。")
