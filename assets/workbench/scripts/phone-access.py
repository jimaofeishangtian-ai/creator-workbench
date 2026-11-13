"""Control phone access from the authorized local Agent without printing credentials."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.db import Store, data_directory
from app.security import create_session
import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "status", "stop"])
    args = parser.parse_args()
    root = data_directory()
    if not (root / "workbench.sqlite3").is_file():
        raise SystemExit("请先启动本工作台并设置密码")
    store = Store(root)
    if not store.one("SELECT id FROM auth"):
        raise SystemExit("请先在电脑工作台设置密码")
    token, csrf = create_session(store)
    try:
        with httpx.Client(base_url="http://127.0.0.1:8765", trust_env=False, timeout=15) as client:
            client.cookies.set("aw_session", token)
            response = client.request("GET" if args.action == "status" else "POST",
                                      "/api/phone/" + args.action, headers={"X-CSRF-Token": csrf})
            result = response.json()
            print(json.dumps(result, ensure_ascii=False))
            return 0 if response.is_success else 1
    finally:
        store.run("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))


if __name__ == "__main__":
    raise SystemExit(main())
