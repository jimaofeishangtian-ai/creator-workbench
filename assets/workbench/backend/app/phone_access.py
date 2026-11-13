"""Explicit, process-scoped Quick Tunnel access. Credentials stay in the app login."""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tarfile
import threading
import time
from pathlib import Path

import httpx

from .providers import TaskError
from .platform_support import VERSION, tunnel_artifact, process_options, phone_platform_error

URL_PATTERN = re.compile(r"https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com(?=[\s/|\"']|$)")


class PhoneAccess:
    def __init__(self, root: Path, port=8765):
        self.folder = root / "tools" / "cloudflared"
        self.artifact = tunnel_artifact()
        self.binary = self.folder / (self.artifact.executable if self.artifact else "cloudflared")
        self.port = port
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.worker = None
        self.process = None
        self.state = "stopped"
        self.message = "开启后用手机相机扫码，再输入工作台密码。"
        self.url = ""
        self.last_visit = None

    def status(self):
        with self.lock:
            if self.process and self.process.poll() is not None and self.state in {"starting", "ready"}:
                self.state, self.url = "error", ""
                self.message = "手机连接已断开，请在电脑重新开启并扫码。"
            return {"state": self.state, "message": self.message, "url": self.url,
                    "last_visit": self.last_visit, "installed": self.binary.is_file()}

    def accepts(self, hostname):
        status = self.status()
        return bool(status["url"] and hostname == status["url"].removeprefix("https://"))

    def start(self):
        with self.lock:
            if self.worker and self.worker.is_alive():
                return self.status()
            if not self.artifact:
                raise TaskError("unsupported_platform", "扫码连接支持Windows x64及macOS 15+的Intel/Apple芯片电脑。")
            if error := phone_platform_error():
                raise TaskError("unsupported_platform", error)
            self.cancel = threading.Event()
            self.state, self.message, self.url = "starting", "正在准备手机连接，首次会下载连接组件…", ""
            self.last_visit = None
            self.worker = threading.Thread(target=self.run, daemon=True, name="workbench-phone")
            self.worker.start()
            return self.status()

    def stop(self):
        with self.lock:
            self.cancel.set()
            self.url, self.state, self.message = "", "stopped", "手机访问已关闭。"
            process, worker = self.process, self.worker
        if process and process.poll() is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        if worker and worker is not threading.current_thread():
            worker.join(timeout=2)
        return self.status()

    def install(self):
        if not self.artifact:
            raise RuntimeError("没有适合当前系统的扫码组件")
        artifact = self.artifact
        self.folder.mkdir(parents=True, exist_ok=True)
        if self.binary.is_file() and hashlib.sha256(self.binary.read_bytes()).hexdigest() == artifact.binary_sha256:
            if artifact.executable != "cloudflared.exe":
                self.binary.chmod(0o700)
            return
        partial = self.folder / "cloudflared.download"
        extracted = self.folder / "cloudflared.extracted"
        digest, size = hashlib.sha256(), 0
        try:
            download = f"https://github.com/cloudflare/cloudflared/releases/download/{VERSION}/{artifact.filename}"
            with httpx.stream("GET", download, follow_redirects=True, timeout=30) as response:
                response.raise_for_status()
                with partial.open("wb") as output:
                    for chunk in response.iter_bytes(256 * 1024):
                        if self.cancel.is_set():
                            return
                        size += len(chunk)
                        if size > 100_000_000:
                            raise RuntimeError("连接组件文件大小异常")
                        output.write(chunk)
                        digest.update(chunk)
            if self.cancel.is_set():
                return
            if digest.hexdigest() != artifact.download_sha256:
                raise RuntimeError("连接组件校验未通过")
            if artifact.filename.endswith(".tgz"):
                # Never extract paths or links from the archive to disk.
                with tarfile.open(partial, "r:gz") as archive:
                    members = archive.getmembers()
                    if len(members) != 1 or members[0].name != "cloudflared" or not members[0].isfile() or not 0 < members[0].size <= 100_000_000:
                        raise RuntimeError("连接组件压缩包内容异常")
                    source = archive.extractfile(members[0])
                    digest, size = hashlib.sha256(), 0
                    with source, extracted.open("wb") as output:
                        while chunk := source.read(256 * 1024):
                            if self.cancel.is_set():
                                return
                            size += len(chunk)
                            if size > 100_000_000:
                                raise RuntimeError("连接组件文件大小异常")
                            digest.update(chunk)
                            output.write(chunk)
                    if digest.hexdigest() != artifact.binary_sha256:
                        raise RuntimeError("连接组件可执行文件校验未通过")
                extracted.chmod(0o700)
                if not self.cancel.is_set():
                    extracted.replace(self.binary)
            else:
                partial.replace(self.binary)
        finally:
            partial.unlink(missing_ok=True)
            extracted.unlink(missing_ok=True)

    def run(self):
        process = None
        try:
            self.install()
            if self.cancel.is_set():
                return
            with self.lock:
                if self.cancel.is_set():
                    return
                self.message = "正在建立手机访问地址…"
                process = subprocess.Popen(
                    [str(self.binary), "tunnel", "--no-autoupdate", "--protocol", "http2",
                     "--url", f"http://127.0.0.1:{self.port}", "--loglevel", "info"],
                    cwd=self.folder, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", **process_options(),
                )
                self.process = process
            deadline = time.monotonic() + 100
            timer = threading.Timer(100, lambda: self.expire_start(process))
            timer.daemon = True
            timer.start()
            try:
                for line in process.stdout:
                    if self.cancel.is_set():
                        break
                    with self.lock:
                        match = URL_PATTERN.search(line)
                        if match:
                            self.url = match.group()
                            self.message = "已生成地址，正在等待线路连通…"
                        if self.url and "Registered tunnel connection" in line:
                            self.state = "ready"
                            self.message = "手机用相机扫码，在浏览器输入工作台密码。"
                        elif "failed to serve tunnel connection" in line.lower():
                            self.state = "starting"
                            self.message = "手机线路中断，正在尝试重新连接…"
                        if time.monotonic() > deadline and self.state != "ready":
                            break
            finally:
                timer.cancel()
            if not self.cancel.is_set():
                with self.lock:
                    self.state, self.url = "error", ""
                    self.message = "手机线路未能保持连接，请检查电脑网络后重试。"
        except Exception as error:
            with self.lock:
                if not self.cancel.is_set():
                    self.state, self.url = "error", ""
                    self.message = (str(error) if isinstance(error, RuntimeError) else "连接组件下载或启动失败，请检查电脑网络后重试。")
        finally:
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()

    def expire_start(self, process):
        with self.lock:
            if self.state == "starting" and process.poll() is None:
                process.terminate()
