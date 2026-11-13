import json
from datetime import datetime, timedelta

import pytest

from app.jobs import JobEngine, TZ
from app.providers import TaskError, TikHub


def prepare_search(store, monkeypatch):
    engine = JobEngine(store)
    monkeypatch.setattr(engine, 'classify_pending', lambda: None)
    monkeypatch.setattr(engine.stop, 'wait', lambda seconds: False)
    return engine


def test_search_covers_a_week_in_one_request_without_adding_retries(store, monkeypatch):
    calls=[]
    def request(self,path,params=None,body=None,**options):
        calls.append((path,body,options))
        return {'data':{}}
    monkeypatch.setattr(TikHub,'request',request)
    assert TikHub(store).search('AI教程')=={'data':{}}
    assert len(calls)==1
    path,body,options=calls[0]
    assert path.endswith('/fetch_video_search_v2')
    assert body['keyword']=='AI教程' and body['publish_time']=='7'
    assert body['cursor']==0 and body['sort_type']=='0'
    assert options['retry_transient'] is False


def test_search_schedule_once_per_day_catches_up_and_survives_restart(store, monkeypatch):
    prices = store.settings()['pricing']; prices['confirmed'] = True
    store.set('pricing', prices); store.set('data_pricing_confirmed', True)
    automation = store.settings()['automation']; automation['enabled'] = True
    store.set('automation', automation)
    monkeypatch.setattr('app.jobs.get_secret', lambda *args: 'test-only')
    morning = datetime(2026, 11, 3, 8, 29, tzinfo=TZ)
    engine = JobEngine(store)
    engine.schedule_tick(morning)
    assert not store.rows("SELECT id FROM jobs WHERE kind='search'")
    engine.schedule_tick(morning + timedelta(minutes=1))
    assert len(store.rows("SELECT id FROM jobs WHERE kind='search'")) == 1
    store.run("UPDATE jobs SET status='succeeded' WHERE kind='search'")
    # Completing the morning run, evening ticks and a restart must not run it again.
    engine = JobEngine(store)
    engine.schedule_tick(morning.replace(hour=18, minute=30))
    engine.schedule_tick(morning.replace(hour=23, minute=59))
    assert len(store.rows("SELECT id FROM jobs WHERE kind='search'")) == 1
    # First online tick late the following day catches up exactly one daily run.
    late = morning + timedelta(days=1, hours=11)
    engine.schedule_tick(late); engine.schedule_tick(late)
    assert len(store.rows("SELECT id FROM jobs WHERE kind='search'")) == 2
    assert not store.rows("SELECT slot FROM schedule_runs WHERE slot LIKE 'search:%:18'")


def test_upgrade_reuses_existing_morning_schedule_slot(store, monkeypatch):
    prices = store.settings()['pricing']; prices['confirmed'] = True
    store.set('pricing', prices); store.set('data_pricing_confirmed', True)
    automation = store.settings()['automation']; automation['enabled'] = True
    store.set('automation', automation)
    monkeypatch.setattr('app.jobs.get_secret', lambda *args: 'test-only')
    store.run("INSERT INTO schedule_runs VALUES ('search:2026-11-03:8','2026-11-03T00:30:00+00:00')")
    JobEngine(store).schedule_tick(datetime(2026, 11, 3, 19, 0, tzinfo=TZ))
    assert not store.rows("SELECT id FROM jobs WHERE kind='search'")


def test_only_short_lived_server_error_is_retried_and_can_recover(store, monkeypatch):
    engine = prepare_search(store, monkeypatch)
    calls = []
    def search(self, keyword):
        calls.append(keyword)
        if len(calls) == 1:
            raise TaskError('provider_error', '服务暂不可用', http_status=503)
        return {'data': {}}
    monkeypatch.setattr(TikHub, 'search', search)
    result = engine.execute('search', {'keywords': ['Codex']}, lambda *args: None)
    assert calls == ['Codex', 'Codex']
    assert result['completed_keywords'] == 1 and not result['failed_keywords']
    assert result['request_attempts'] == 2 and result['retry_attempts'] == 1


def test_server_retry_budget_is_shared_across_keywords(store, monkeypatch):
    engine = prepare_search(store, monkeypatch)
    calls = []
    def search(self, keyword):
        calls.append(keyword)
        raise TaskError('provider_error', '服务暂不可用', http_status=502)
    monkeypatch.setattr(TikHub, 'search', search)
    result = engine.execute('search', {'keywords': ['A', 'B', 'C']}, lambda *args: None)
    assert calls == ['A', 'A', 'B', 'B', 'C']
    assert result['request_attempts'] == 5 and result['retry_attempts'] == 2
    assert result['completed_keywords'] == 0 and len(result['failed_keywords']) == 3


@pytest.mark.parametrize('code,status', [('provider_error', 400), ('provider_error', 422), ('network_error', None)])
def test_rejection_and_unknown_billing_are_not_repeated(store, monkeypatch, code, status):
    engine = prepare_search(store, monkeypatch)
    calls = []
    def search(self, keyword):
        calls.append(keyword)
        raise TaskError(code, '本次请求失败', http_status=status)
    monkeypatch.setattr(TikHub, 'search', search)
    result = engine.execute('search', {'keywords': ['Codex', 'Claude']}, lambda *args: None)
    assert calls == ['Codex', 'Claude']
    assert result['request_attempts'] == 2 and result['retry_attempts'] == 0
    assert len(result['failed_keywords']) == 2


def test_duplicate_keywords_do_not_repeat_paid_searches(store, monkeypatch):
    engine = prepare_search(store, monkeypatch)
    calls = []
    monkeypatch.setattr(TikHub, 'search', lambda self, keyword: calls.append(keyword) or {'data': {}})
    result = engine.execute('search', {'keywords': [' Codex ', 'codex', 'AI工具', ' AI工具 ', ' ']}, lambda *args: None)
    assert calls == ['Codex', 'AI工具']
    assert result['keywords'] == 2 and result['request_attempts'] == 2


def test_cancel_during_retry_wait_prevents_next_paid_request(store, monkeypatch):
    engine = prepare_search(store, monkeypatch)
    identity = engine.enqueue('search', {'keywords': ['Codex']})
    calls = []
    def search(self, keyword):
        calls.append(keyword)
        raise TaskError('provider_error', '服务暂不可用', http_status=503)
    monkeypatch.setattr(TikHub, 'search', search)
    monkeypatch.setattr(engine.stop, 'wait', lambda seconds: engine.cancel(identity))
    with pytest.raises(TaskError) as error:
        engine.execute('search', {'keywords': ['Codex']}, lambda *args: None, job_id=identity)
    assert error.value.code == 'cancelled' and calls == ['Codex']


def test_manual_partial_retry_only_includes_failed_keywords(client, store, app):
    identity = app.state.engine.enqueue('search', {'keywords': ['Codex', 'Claude']})
    store.run("UPDATE jobs SET status='attention',result=? WHERE id=?", (json.dumps({'failed_keywords': [{'keyword': 'Claude'}]}), identity))
    response = client.post(f'/api/jobs/{identity}/retry', json={})
    assert response.status_code == 200
    queued = store.one('SELECT payload FROM jobs WHERE id=?', (response.json()['job_id'],))
    assert json.loads(queued['payload']) == {'keywords': ['Claude']}
