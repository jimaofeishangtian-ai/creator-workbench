import os
import tempfile
from pathlib import Path

os.environ["AI_WORKBENCH_DATA"] = tempfile.mkdtemp(prefix="aw-import-test-")

import pytest
from fastapi.testclient import TestClient
from app.main import create_app


@pytest.fixture(autouse=True)
def isolated_mac_credentials(monkeypatch):
    # Broad product tests never prompt for or modify a user's real Keychain.
    # A separate CI-only native test checks the real backend on disposable Macs.
    import sys
    if sys.platform == "darwin":
        class MemoryKeychain:
            values = {}
            def set_password(self, service, name, value): self.values[service, name] = value
            def get_password(self, service, name): return self.values.get((service, name))
            def delete_password(self, service, name): self.values.pop((service, name), None)
        monkeypatch.setattr("app.security._macos_keyring", lambda: MemoryKeychain())


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path / "data", run_jobs=False)


@pytest.fixture
def store(app):
    # Explicit legacy AI fixture; neutral fresh installs are tested independently.
    app.state.store.set('creator_domain',None)
    app.state.store.set('profile','AI垂直账号，面向AI小白和AI自媒体创作者。真人口播加录屏，分享AI知识、教程、工具、Agent工作流与真实使用经验。结果先行，通俗自然，操作可验证。')
    app.state.store.set('keywords',['AI教程', 'AI工具', 'Agent工作流', 'Codex', 'Claude', '豆包', 'WorkBuddy', 'AI知识库', 'AI剪辑', 'GitHub AI项目'])
    app.state.store.set('categories',['工具与新功能', 'Agent工作流', '知识库与记忆', 'AI视频与剪辑', '数字人与配音', 'AI图像与视频生成', '画布平台', '商单创意与分镜', '编程与开源', '效率与Token', '资讯', '商业与营销', '学习与成长', '生活与兴趣', '理财与财经', '其他'])
    return app.state.store


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        code = (app.state.store.root / "initial-setup-code.txt").read_text()
        response = c.post("/api/auth/setup", json={"password": "test-password-12345", "setup_token": code})
        assert response.status_code == 200
        c.headers["X-CSRF-Token"] = response.json()["csrf"]
        yield c

