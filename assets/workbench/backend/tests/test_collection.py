import types
import sys
import pytest
from app.collector import FavoritesCollector
from app.providers import TaskError


def test_more_than_100_new_favorites_resume_before_advancing_checkpoint(store, monkeypatch):
    old_id = '100001'
    new_ids = [str(300000 + i) for i in range(250)]
    all_ids = new_ids + [old_id]
    store.set('favorites_checkpoint', [old_id])
    store.set('backfill_cursor', 5000)
    class Page:
        def goto(self, *args, **kwargs): pass
        def wait_for_timeout(self, *args): pass
        def evaluate(self, script, args):
            start, count = args['cursor'], args['count']
            items = all_ids[start:start+count]
            return {'status_code': 0, 'aweme_list': [{'aweme_id': x, 'desc': 'AI收藏'+x, 'author': {}, 'video': {}} for x in items], 'cursor': start+len(items), 'has_more': start+len(items)<len(all_ids)}
    class Context:
        pages = [Page()]
        def cookies(self, *args): return [{'name': 'sessionid', 'value': 'test'}]
        def close(self): pass
    class Chromium:
        def launch_persistent_context(self, **kwargs): return Context()
    class Playwright:
        chromium = Chromium()
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setitem(sys.modules, 'playwright.sync_api', types.SimpleNamespace(sync_playwright=lambda: Playwright()))
    collect = lambda: FavoritesCollector(store, lambda *args: None).run('sync')
    assert collect()['added'] == 100
    assert store.settings()['favorites_checkpoint'] == [old_id]
    assert store.settings()['favorites_pending']['cursor'] == 100
    assert collect()['added'] == 100
    assert collect()['added'] == 51
    assert store.one('SELECT COUNT(*) n FROM videos')['n'] == 251
    assert store.settings()['favorites_checkpoint'] == new_ids[:40]
    assert not store.settings()['favorites_pending']
    assert store.settings()['backfill_cursor'] == 5000


def mock_browser(monkeypatch, *, cookies, response):
    class Page:
        def goto(self, *args, **kwargs): pass
        def wait_for_timeout(self, *args): pass
        def evaluate(self, *args): return response
    class Context:
        pages = [Page()]
        def cookies(self, *args): return cookies
        def close(self): pass
    class Chromium:
        def launch_persistent_context(self, **kwargs): return Context()
    class Playwright:
        chromium = Chromium()
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setitem(sys.modules, 'playwright.sync_api', types.SimpleNamespace(sync_playwright=lambda: Playwright()))


def test_missing_initial_login_is_not_called_expired(store, monkeypatch):
    mock_browser(monkeypatch, cookies=[], response={})
    with pytest.raises(TaskError, match='尚未完成登录'):
        FavoritesCollector(store, lambda *args: None).run('sync')
    assert '失效' not in store.settings()['collection_state']['message']


def test_login_cookie_with_unreadable_collection_is_an_api_failure(store, monkeypatch):
    mock_browser(monkeypatch, cookies=[{'name':'sessionid','value':'test'}], response={'http_status':403})
    moments = iter([0,1,901])
    monkeypatch.setattr('app.collector.time', types.SimpleNamespace(monotonic=lambda:next(moments)))
    with pytest.raises(TaskError) as err:
        FavoritesCollector(store, lambda *args: None).run('login')
    assert err.value.code == 'collection_failed'
    assert 'HTTP 403' in err.value.message
    assert store.settings()['collection_state']['status'] == 'collection_failed'
