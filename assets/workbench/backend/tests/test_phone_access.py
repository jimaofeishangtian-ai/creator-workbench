import hashlib
import threading
from dataclasses import replace

import pytest

from app.phone_access import PhoneAccess, URL_PATTERN


def activate(app):
    phone = app.state.phone
    phone.url = "https://quiet-river-example.trycloudflare.com"
    phone.state = "ready"
    return phone, phone.url


def test_public_phone_requires_login_restricts_management_and_expires_with_tunnel(client, app):
    phone, url = activate(app)
    assert client.get(url + "/api/overview").status_code == 401
    assert client.get("https://another.trycloudflare.com/api/health").status_code == 403
    login = client.post(url + "/api/auth/login", json={"password": "test-password-12345"})
    assert login.status_code == 200
    assert "Secure" in login.headers["set-cookie"] and "HttpOnly" in login.headers["set-cookie"]
    client.headers["X-CSRF-Token"] = login.json()["csrf"]
    assert client.get(url + "/api/auth/status").json()["public_access"]
    assert client.get(url + "/api/overview").status_code == 200
    assert phone.last_visit
    settings = client.get(url + "/api/settings").json()
    assert "data_directory" not in settings and "edge_extension_directory" not in settings
    for method, path in [("POST", "/api/phone/start"), ("POST", "/api/phone/stop"),
                         ("GET", "/api/backups"), ("GET", "/api/system"),
                         ("PUT", "/api/secrets/deepseek"), ("PATCH", "/api/settings"),
                         ("POST", "/api/auth/setup"), ("POST", "/api/edge/bridge/pair")]:
        assert client.request(method, url + path, json={}).status_code == 403
    phone.stop()
    assert client.get(url + "/api/overview").status_code == 403
    assert client.get("/api/overview").status_code == 200


def test_eight_character_password_can_set_up_and_log_in(app):
    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        code = (app.state.store.root / "initial-setup-code.txt").read_text()
        assert client.post('/api/auth/setup', json={'password': 'eight888', 'setup_token': code}).status_code == 200
        csrf = client.get('/api/auth/status').json()['csrf']
        client.post('/api/auth/logout', headers={'X-CSRF-Token': csrf})
        assert client.post('/api/auth/login', json={'password': 'eight888'}).status_code == 200
        assert client.post('/api/auth/login', json={'password': 'short77'}).status_code == 422


def test_public_login_limit_cannot_be_bypassed_with_forwarded_header(client, app):
    _, url = activate(app)
    for i in range(10):
        r = client.post(url + "/api/auth/login", json={"password": "wrong-password-123"},
                        headers={"X-Forwarded-For": f"1.2.3.{i}"})
        assert r.json()["code"] == "invalid_password"
    assert client.post(url + "/api/auth/login", json={"password": "test-password-12345"}).json()["code"] == "rate_limited"


def test_connection_extraction_is_limited_to_quick_tunnel_hostname():
    assert URL_PATTERN.search("| https://quiet-river-example.trycloudflare.com |").group() == "https://quiet-river-example.trycloudflare.com"
    assert not URL_PATTERN.search("https://quiet-river-example.trycloudflare.com.attacker.example")


def test_download_rejects_bad_binary_and_does_not_keep_partial(tmp_path, monkeypatch):
    phone = PhoneAccess(tmp_path)
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_bytes(self, *args): return iter([b"incorrect executable"])
    monkeypatch.setattr("app.phone_access.httpx.stream", lambda *a, **k: Response())
    with pytest.raises(RuntimeError, match="校验"):
        phone.install()
    assert not phone.binary.exists()
    assert not (phone.folder / "cloudflared.download").exists()


def test_verified_download_is_reused(tmp_path, monkeypatch):
    phone = PhoneAccess(tmp_path)
    phone.folder.mkdir(parents=True)
    phone.binary.write_bytes(b"verified")
    phone.artifact = replace(phone.artifact, binary_sha256=hashlib.sha256(b"verified").hexdigest())
    monkeypatch.setattr("app.phone_access.httpx.stream", lambda *a, **k: pytest.fail("cached binary was downloaded again"))
    phone.install()
    assert phone.status()["installed"]


def test_repeated_start_stop_and_restart_only_accept_the_current_address(tmp_path, monkeypatch):
    phone = PhoneAccess(tmp_path)
    monkeypatch.setattr(phone, "install", lambda: None)
    processes = []
    created = threading.Event()

    class Process:
        def __init__(self, *args, **kwargs):
            self.ready = threading.Event()
            self.stopped = threading.Event()
            self.returncode = None
            processes.append(self)
            self.url = f"https://tunnel-{len(processes)}.trycloudflare.com"
            self.stdout = self.output()
            created.set()
        def output(self):
            yield self.url + "\n"
            yield "Registered tunnel connection\n"
            self.ready.set()
            self.stopped.wait(3)
        def poll(self): return self.returncode
        def terminate(self):
            self.returncode = 0
            self.stopped.set()
        def kill(self): self.terminate()
        def wait(self, **kwargs): return self.returncode

    monkeypatch.setattr("app.phone_access.subprocess.Popen", Process)
    phone.start()
    # Waiting on the event also observes the consumer processing the ready line.
    assert created.wait(1)
    assert processes[0].ready.wait(1)
    phone.start()
    assert len(processes) == 1
    assert phone.accepts("tunnel-1.trycloudflare.com")
    assert phone.stop()["state"] == "stopped"
    assert not phone.accepts("tunnel-1.trycloudflare.com")
    created.clear()
    phone.start()
    assert created.wait(1)
    assert processes[1].ready.wait(1)
    assert phone.accepts("tunnel-2.trycloudflare.com")
    assert not phone.accepts("tunnel-1.trycloudflare.com")
    phone.stop()
