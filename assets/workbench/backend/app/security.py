from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import os
import secrets
import time
from ctypes import wintypes

from .db import Store


def password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(key).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, expected = stored.split("$")
        key = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=16384, r=8, p=1, dklen=32)
        return hmac.compare_digest(key, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(value: bytes, decrypt=False) -> bytes:
    buffer = ctypes.create_string_buffer(value)
    inp = Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    out = Blob()
    crypt = ctypes.windll.crypt32
    if decrypt:
        ok = crypt.CryptUnprotectData(ctypes.byref(inp), None, None, None, None, 1, ctypes.byref(out))
    else:
        ok = crypt.CryptProtectData(ctypes.byref(inp), "AIWorkbench", None, None, None, 1, ctypes.byref(out))
    if not ok:
        raise OSError("Windows 凭据加密失败")
    try:
        return ctypes.string_at(out.data, out.size)
    finally:
        ctypes.windll.kernel32.LocalFree(out.data)


def save_secret(store: Store, name: str, value: str):
    if not value:
        store.run("DELETE FROM secrets WHERE name=?", (name,))
        return
    if os.name != "nt":
        raise ValueError("此版本仅在Windows上保存API密钥；其他平台请使用环境变量")
    store.run("INSERT INTO secrets VALUES (?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value", (name, _dpapi(value.encode())))


def get_secret(store: Store, name: str) -> str:
    row = store.one("SELECT value FROM secrets WHERE name=?", (name,))
    if row and os.name == "nt":
        try:
            return _dpapi(row["value"], True).decode()
        except (OSError, UnicodeError):
            return ""
    return os.environ.get({"deepseek": "DEEPSEEK_API_KEY", "tikhub": "TIKHUB_API_KEY"}[name], "")


def create_session(store: Store):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    store.run("DELETE FROM sessions WHERE expires<?", (time.time(),))
    store.run("INSERT INTO sessions VALUES (?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), time.time() + 14 * 86400, csrf))
    return token, csrf


def read_session(store: Store, token: str):
    return store.one("SELECT * FROM sessions WHERE token_hash=? AND expires>?", (hashlib.sha256(token.encode()).hexdigest(), time.time())) if token else None
