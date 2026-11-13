"""Disposable GitHub Mac runner only. Never uses real Douyin/provider accounts."""
import hashlib
import json
import os
import platform
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if sys.platform != "darwin" or os.environ.get("GITHUB_ACTIONS") != "true":
    raise SystemExit("Native smoke is restricted to disposable GitHub macOS runners.")
ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "assets/workbench"
sys.path.insert(0, str(APP / "backend"))
import httpx
from app.db import Store
from app.security import save_secret, get_secret
from app.phone_access import PhoneAccess
from app.platform_support import phone_platform_error

report = {"macos": platform.mac_ver()[0], "arch": platform.machine(), "python": platform.python_version()}
assert not phone_platform_error()
with tempfile.TemporaryDirectory(prefix="creator-workbench-mac-smoke-") as temp:
    store = Store(Path(temp))
    try:
        save_secret(store, "deepseek", "synthetic-ci-key-not-a-provider-credential")
        assert get_secret(store, "deepseek") == "synthetic-ci-key-not-a-provider-credential"
        assert b"synthetic-ci-key-not-a-provider-credential" not in store.path.read_bytes()
        report["native_keychain"] = "roundtrip passed; no plaintext in SQLite"
    finally:
        save_secret(store, "deepseek", "")
    assert not get_secret(store, "deepseek")
    phone = PhoneAccess(Path(temp))
    phone.install()
    report["cloudflared"] = subprocess.check_output([str(phone.binary), "--version"], text=True, timeout=20).strip()
    assert os.access(phone.binary, os.X_OK)

import ctranslate2
from faster_whisper import WhisperModel
report["asr"] = {"import": "passed", "cpu_compute_types": sorted(ctranslate2.get_supported_compute_types("cpu")), "model_downloaded": False}

# Exercise the real detached Mac launcher on an otherwise empty runner.
runtime = APP / "runtime"
assert not (runtime / "server.pid").exists()
pid = None
try:
    subprocess.run([sys.executable, str(APP / "scripts/start.py"), "--no-browser"], check=True, timeout=70)
    pid = int((runtime / "server.pid").read_text())
    expected = hashlib.sha256(str(runtime.resolve()).encode()).hexdigest()[:16]
    with httpx.Client(base_url="http://127.0.0.1:8765", trust_env=False, timeout=10) as client:
        assert client.get("/api/health").json()["instance_id"] == expected
        assert client.get("/").status_code == 200
        assert client.get("/api/overview").status_code == 401
        token = (runtime / "initial-setup-code.txt").read_text().strip()
        response = client.post("/api/auth/setup", json={"password": "SyntheticMacCI2026!", "setup_token": token})
        assert response.status_code == 200
        assert client.get("/api/overview").status_code == 200
    report["native_launch_login_frontend"] = "passed; no paid calls or browser platform login"
finally:
    if pid is not None:
        os.kill(pid, signal.SIGTERM)
        time.sleep(1)
print(json.dumps(report, ensure_ascii=False, indent=2))
(ROOT / "macos-native-result.local.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
