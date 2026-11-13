"""OS simulations: no system credentials, remote APIs, or executable downloads."""
import hashlib
import importlib.util
import io
import os
import tarfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import security
from app.db import Store
from app.phone_access import PhoneAccess
from app.platform_support import MAC_ARM, MAC_INTEL, WINDOWS, tunnel_artifact, process_options, phone_platform_error


@pytest.mark.parametrize("system,arch,expected", [
    ("Darwin", "arm64", MAC_ARM), ("Darwin", "aarch64", MAC_ARM),
    ("Darwin", "x86_64", MAC_INTEL), ("Windows", "AMD64", WINDOWS),
    ("Windows", "ARM64", None), ("Linux", "x86_64", None)])
def test_native_artifact_is_explicit(system, arch, expected):
    assert tunnel_artifact(system, arch) == expected


def test_macos_process_has_no_windows_flags_and_server_detaches():
    assert process_options("Darwin") == {}
    assert process_options("Darwin", detached=True) == {"start_new_session": True}
    assert process_options("Windows", detached=True)["creationflags"] == 0x08000000
    assert phone_platform_error("Darwin", "14.7")
    assert phone_platform_error("Darwin", "")
    assert not phone_platform_error("Darwin", "15.0")


class MemoryKeychain:
    def __init__(self): self.values = {}
    def set_password(self, service, name, value): self.values[service, name] = value
    def get_password(self, service, name): return self.values.get((service, name))
    def delete_password(self, service, name): self.values.pop((service, name), None)


def use_mac(monkeypatch, backend):
    monkeypatch.setattr(security, "platform", SimpleNamespace(system=lambda: "Darwin"))
    monkeypatch.setattr(security, "_macos_keyring", lambda: backend)


def test_keychain_roundtrip_is_scoped_and_database_has_no_key(tmp_path, monkeypatch):
    keychain = MemoryKeychain()
    use_mac(monkeypatch, keychain)
    one, two = Store(tmp_path / "one"), Store(tmp_path / "two")
    value = "synthetic-not-a-real-secret"
    security.save_secret(one, "deepseek", value)
    assert security.get_secret(one, "deepseek") == value
    assert security.get_secret(two, "deepseek") == ""
    security.save_secret(two, "deepseek", "second-synthetic-credential")
    security.save_secret(one, "deepseek", "")
    assert security.get_secret(one, "deepseek") == ""
    assert security.get_secret(two, "deepseek") == "second-synthetic-credential"
    assert value.encode() not in one.path.read_bytes()
    security.save_secret(two, "deepseek", "")
    assert keychain.values == {}


def test_keychain_failure_never_falls_back_to_plaintext(client, store, monkeypatch):
    class Denied(MemoryKeychain):
        def set_password(self, *args): raise RuntimeError("synthetic-private-provider-detail")
    use_mac(monkeypatch, Denied())
    result = client.put("/api/secrets/deepseek", json={"value": "synthetic-private-key"})
    assert result.status_code == 503
    assert result.json()["code"] == "credential_storage_unavailable"
    assert "synthetic-private" not in result.text
    assert not store.one("SELECT * FROM secrets")
    assert b"synthetic-private-key" not in store.path.read_bytes()


def archive_bytes(name="cloudflared", *, symlink=False):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as archive:
        member = tarfile.TarInfo(name)
        payload = b"synthetic-MachO-for-test"
        if symlink:
            member.type, member.linkname = tarfile.SYMTYPE, "/etc/passwd"
        else:
            member.size = len(payload)
        archive.addfile(member, None if symlink else io.BytesIO(payload))
    return data.getvalue(), payload


def stream_bytes(monkeypatch, data):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_bytes(self, *args): return iter([data])
    monkeypatch.setattr("app.phone_access.httpx.stream", lambda *a, **k: Response())


@pytest.mark.parametrize("artifact", [MAC_ARM, MAC_INTEL])
def test_mac_install_checks_archive_binary_and_reuses_cache(tmp_path, monkeypatch, artifact):
    phone = PhoneAccess(tmp_path)
    data, payload = archive_bytes()
    phone.artifact = replace(artifact, download_sha256=hashlib.sha256(data).hexdigest(), binary_sha256=hashlib.sha256(payload).hexdigest())
    phone.binary = phone.folder / "cloudflared"
    stream_bytes(monkeypatch, data)
    phone.install()
    assert phone.binary.read_bytes() == payload
    if os.name != "nt": assert phone.binary.stat().st_mode & 0o777 == 0o700
    monkeypatch.setattr("app.phone_access.httpx.stream", lambda *a, **k: pytest.fail("download repeated"))
    phone.install()
    assert not (phone.folder / "cloudflared.download").exists()


@pytest.mark.parametrize("name,symlink", [("../outside", False), ("/cloudflared", False), ("cloudflared", True)])
def test_unexpected_archive_never_extracts_paths_or_links(tmp_path, monkeypatch, name, symlink):
    phone = PhoneAccess(tmp_path)
    data, payload = archive_bytes(name, symlink=symlink)
    phone.artifact = replace(MAC_ARM, download_sha256=hashlib.sha256(data).hexdigest())
    phone.binary = phone.folder / "cloudflared"
    stream_bytes(monkeypatch, data)
    with pytest.raises(RuntimeError, match="内容异常"): phone.install()
    assert not phone.binary.exists()
    assert not (phone.folder / "cloudflared.download").exists()
    assert not (tmp_path / "outside").exists()


def test_valid_archive_with_wrong_binary_is_not_executed(tmp_path, monkeypatch):
    phone = PhoneAccess(tmp_path)
    data, _ = archive_bytes()
    phone.artifact = replace(MAC_ARM, download_sha256=hashlib.sha256(data).hexdigest())
    phone.binary = phone.folder / "cloudflared"
    stream_bytes(monkeypatch, data)
    with pytest.raises(RuntimeError, match="可执行文件校验"): phone.install()
    assert not phone.binary.exists()
    assert not (phone.folder / "cloudflared.extracted").exists()


def test_user_password_reset_revokes_sessions_and_preserves_materials(store):
    path = Path(__file__).resolve().parents[2] / "scripts/reset-password.py"
    spec = importlib.util.spec_from_file_location("reset_local_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    store.run("INSERT INTO auth VALUES (1,?)", (security.password_hash("old-test-password"),))
    security.create_session(store)
    module.reset_database(store.path, "new-test-password")
    assert security.verify_password("new-test-password", store.one("SELECT * FROM auth")["password_hash"])
    assert not store.rows("SELECT * FROM sessions")
    with pytest.raises(RuntimeError): module.reset_database(store.path, "short")
