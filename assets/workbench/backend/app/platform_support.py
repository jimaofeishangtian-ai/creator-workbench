"""Explicit platform choices; selecting Darwin here never emulates a Mac kernel."""
from __future__ import annotations

import platform
from dataclasses import dataclass


@dataclass(frozen=True)
class TunnelArtifact:
    filename: str
    download_sha256: str
    binary_sha256: str
    executable: str


VERSION = "2026.10.7"
WINDOWS = TunnelArtifact(
    "cloudflared-windows-amd64.exe",
    "2493dbc168923bb3e59e5e7716f71deced92992eea9acd6e9d74f84ac7751343",
    "2493dbc168923bb3e59e5e7716f71deced92992eea9acd6e9d74f84ac7751343", "cloudflared.exe")
MAC_INTEL = TunnelArtifact(
    "cloudflared-darwin-amd64.tgz",
    "02f3edfd87684dd5b705607688e4eb4b323f6e777b81e7b3a85af556aa356848",
    "5c55e77da7012cebca19170357e196176c48c9647cc2ead404702fa125ddbd86", "cloudflared")
MAC_ARM = TunnelArtifact(
    "cloudflared-darwin-arm64.tgz",
    "3586ccf397e98e5f437e60e7c307ca6ac463e110580503af8bb8e6f9ebf831bd",
    "2136431d5eb467bf447d2ee514b713e192719b143aec1d9e09bf7dc290d5bb12", "cloudflared")


def tunnel_artifact(system=None, machine=None):
    system, machine = system or platform.system(), (machine or platform.machine()).lower()
    if system == "Windows" and machine in {"amd64", "x86_64"}:
        return WINDOWS
    if system == "Darwin":
        return MAC_ARM if machine in {"arm64", "aarch64"} else MAC_INTEL if machine in {"x86_64", "amd64"} else None
    return None


def process_options(system=None, *, detached=False):
    if (system or platform.system()) == "Windows":
        return {"creationflags": 0x08000000}
    return {"start_new_session": True} if detached else {}


def phone_platform_error(system=None, version=None):
    if (system or platform.system()) == "Darwin":
        version = platform.mac_ver()[0] if version is None else version
        try:
            supported = int(version.split(".")[0]) >= 15
        except ValueError:
            supported = False
        if not supported:
            return "当前扫码组件需要macOS 15或更高版本；电脑工作台仍可单独使用。"
    return ""
