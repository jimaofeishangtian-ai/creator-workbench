import pytest

from app import services
from app.jobs import JobEngine
from app.providers import TaskError


def commercial(store):
    services.ingest(store, {"id": "123456789", "title": "AIGC参考"})
    identity = services.create_project(store, "123456789", "commercial")
    services.update_project(store, identity, 1, {"brief": {"product": "测试平台"}})
    return identity


@pytest.mark.skip(reason="已下线的知识库/制作行为；由 test_discovery 的停用与数据保留检查替代")
def test_one_hook_task_prepares_references_then_uses_the_resulting_revision(store, monkeypatch):
    identity = commercial(store)
    calls = []
    def prepare(store, identity, revision, progress, job_id=None):
        calls.append(("references", revision))
        changed = services.update_project(store, identity, revision, {"reference_analysis": {"summary": "真实参考分析"}})
        return {"project_id": identity, "revision": changed["revision"]}
    def generate(store, identity, kind, revision, **kwargs):
        calls.append((kind, revision))
        assert services.project(store, identity)["reference_analysis"]["summary"] == "真实参考分析"
        return {"revision": revision}
    monkeypatch.setattr("app.commercial.analyze_references", prepare)
    monkeypatch.setattr("app.jobs.generate_project", generate)
    result = JobEngine(store).execute("hooks", {"project_id": identity, "revision": 2}, lambda *args: None)
    assert calls == [("references", 2), ("hooks", 3)]
    assert result["revision"] == 3


@pytest.mark.skip(reason="已下线的知识库/制作行为；由 test_discovery 的停用与数据保留检查替代")
def test_existing_reference_analysis_is_reused_without_another_request(store, monkeypatch):
    identity = commercial(store)
    services.update_project(store, identity, 2, {"reference_analysis": {"summary": "已保存"}})
    monkeypatch.setattr("app.commercial.analyze_references", lambda *args: pytest.fail("cached references must not be reanalyzed"))
    monkeypatch.setattr("app.jobs.generate_project", lambda *args, **kwargs: {"ok": True})
    assert JobEngine(store).execute("hooks", {"project_id": identity, "revision": 3}, lambda *args: None)["ok"]


@pytest.mark.skip(reason="已下线的知识库/制作行为；由 test_discovery 的停用与数据保留检查替代")
def test_cancel_between_reference_analysis_and_hooks_stops_the_next_model_request(store, monkeypatch):
    identity = commercial(store)
    engine = JobEngine(store)
    job_id = engine.enqueue("hooks", {"project_id": identity, "revision": 2})
    def prepare(store, identity, revision, progress, job_id=None):
        engine.cancel(job_id)
        return {"revision": 3}
    monkeypatch.setattr("app.commercial.analyze_references", prepare)
    monkeypatch.setattr("app.jobs.generate_project", lambda *args, **kwargs: pytest.fail("cancelled job must not start hooks"))
    with pytest.raises(TaskError) as error:
        engine.execute("hooks", {"project_id": identity, "revision": 2}, lambda *args: None, job_id=job_id)
    assert error.value.code == "cancelled"


@pytest.mark.skip(reason="已下线的知识库/制作行为；由 test_discovery 的停用与数据保留检查替代")
def test_stale_revision_is_rejected_before_paid_preparation(store, monkeypatch):
    identity = commercial(store)
    monkeypatch.setattr("app.commercial.analyze_references", lambda *args: pytest.fail("stale task must not analyze"))
    with pytest.raises(TaskError) as error:
        JobEngine(store).execute("hooks", {"project_id": identity, "revision": 1}, lambda *args: None)
    assert error.value.code == "revision_conflict"


@pytest.mark.skip(reason="已下线的知识库/制作行为；由 test_discovery 的停用与数据保留检查替代")
def test_reference_conflict_returns_paid_result_instead_of_starting_hooks(store, monkeypatch):
    identity = commercial(store)
    monkeypatch.setattr("app.commercial.analyze_references", lambda *args: {"conflict": True, "generated": {"reference_analysis": {"summary": "可找回的结果"}}})
    monkeypatch.setattr("app.jobs.generate_project", lambda *args, **kwargs: pytest.fail("conflict must stop hooks"))
    result = JobEngine(store).execute("hooks", {"project_id": identity, "revision": 2}, lambda *args: None)
    assert result["conflict"] and result["generated"]["reference_analysis"]["summary"] == "可找回的结果"
