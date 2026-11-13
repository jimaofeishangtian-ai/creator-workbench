import time
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import services
from app.edge_bridge import EdgeBridge, ResultBody
from app.jobs import JobEngine
from app.providers import TaskError

EXTENSION = 'a' * 32
ORIGIN = 'chrome-extension://' + EXTENSION


def pair(store):
    bridge = EdgeBridge(store)
    code = bridge.pairing()['code']
    return bridge, bridge.pair(code, EXTENSION, ORIGIN)['token']


def request(store, bridge, mode='sync'):
    engine = JobEngine(store)
    jid = engine.enqueue('favorites_sync' if mode == 'sync' else 'favorites_backfill')
    store.run("UPDATE jobs SET status='running' WHERE id=?", (jid,))
    assert bridge.request(mode, jid) == {'_await_browser': True}
    return bridge.work()['request']


def result(work, **changes):
    values = dict(job_id=work['job_id'], nonce=work['nonce'], outcome='ok', items=[{'id':'123456789','title':'Codex知识库实测','author':'真实作者'}], head_ids=['123456789'])
    return ResultBody(**{**values, **changes})


@pytest.mark.parametrize('kind', ['favorites_login','favorites_status','favorites_sync','favorites_backfill'])
def test_unpaired_edge_mode_never_launches_an_isolated_browser(store, monkeypatch, kind):
    def forbidden(*args, **kwargs):
        raise AssertionError('must not open another login window')
    monkeypatch.setattr('app.collector.FavoritesCollector.run', forbidden)
    with pytest.raises(TaskError) as error:
        JobEngine(store).execute(kind, {}, lambda *args:None, job_id='a'*32)
    assert error.value.code == 'configuration_required'


def test_pairing_is_single_use_limited_and_not_exposed(client, store):
    code = client.post('/api/connections/edge/pairing', json={}).json()['code']
    bridge = EdgeBridge(store)
    with pytest.raises(TaskError):
        bridge.pair('incorrect-code-long-enough', EXTENSION, ORIGIN)
    assert store.one('SELECT attempts FROM browser_pairing')['attempts'] == 1
    token = bridge.pair(code, EXTENSION, ORIGIN)['token']
    assert token.encode() not in store.path.read_bytes()
    assert token not in client.get('/api/settings').text
    assert code not in client.get('/api/settings').text
    with pytest.raises(TaskError):
        bridge.pair(code, EXTENSION, ORIGIN)
    bridge.authorize('Bearer '+token, ORIGIN)


def test_bridge_credential_is_not_an_app_login_and_origin_is_bound(app, store):
    bridge, token = pair(store)
    with TestClient(app) as raw:
        headers={'Authorization':'Bearer '+token,'Origin':ORIGIN}
        assert raw.get('/api/edge/bridge/work',headers=headers).status_code == 200
        assert raw.get('/api/settings',headers=headers).status_code == 401
        assert raw.get('/api/edge/bridge/work',headers={**headers,'Origin':'https://evil.example'}).status_code == 403
        assert raw.get('/api/edge/bridge/work',headers={**headers,'Origin':'chrome-extension://'+'b'*32}).status_code == 403
        assert raw.get('/api/edge/bridge/work').json()['code'] == 'bridge_unauthorized'
        store.set('remote_access', {'url':'https://private.example.ts.net','allowed_device':'phone'})
        assert raw.get('/api/edge/bridge/work',headers={**headers,'Host':'private.example.ts.net'}).status_code == 403


def test_cancelled_task_and_stale_claim_never_import(client, store):
    bridge, _ = pair(store)
    work = request(store, bridge)
    assert client.post('/api/jobs/'+work['job_id']+'/cancel',json={}).status_code == 200
    with pytest.raises(TaskError, match='未写入'):
        bridge.complete(result(work))
    assert store.one('SELECT COUNT(*) n FROM videos')['n'] == 0
    work = request(store, bridge)
    store.run('UPDATE browser_requests SET lease_until=? WHERE job_id=?',(time.time()-1,work['job_id']))
    renewed=bridge.work()['request']
    assert renewed['nonce'] != work['nonce']
    with pytest.raises(TaskError):
        bridge.complete(result(work))
    assert bridge.complete(result(renewed))['added'] == 1
    with pytest.raises(TaskError):
        bridge.complete(result(renewed))


def test_dom_import_preserves_unknown_fields_and_manual_classification(store):
    bridge, _=pair(store)
    services.ingest(store, {'id':'123456789','title':'人工分类的作品'})
    store.run("UPDATE videos SET manual_classification=1,category='资讯' WHERE id='123456789'")
    work=request(store,bridge)
    saved=bridge.complete(result(work,items=[{'id':'123456789','title':'新标题','cover':'https://evil.example/track.png','media_kind':'image'}]))
    assert saved['added'] == 0
    row=store.one("SELECT * FROM videos WHERE id='123456789'")
    assert row['category']=='资讯' and row['media_kind']=='image'
    assert row['cover'] is None and row['likes'] is None and row['collected_at'] is None and row['published_at'] is None
    assert row['url']=='https://www.douyin.com/note/123456789'
    assert store.one("SELECT source_key FROM sources WHERE kind='favorites'")['source_key']=='existing-edge'


def test_partial_increment_keeps_old_checkpoint_and_can_resume(store):
    bridge,_=pair(store)
    store.set('favorites_checkpoint',['200000'])
    first=request(store,bridge)
    bridge.complete(result(first,truncated=True))
    assert store.settings()['favorites_checkpoint']==['200000']
    second=request(store,bridge)
    assert '123456789' in second['known_ids'] and second['stop_ids']==['200000']
    bridge.complete(result(second,reached_checkpoint=True))
    assert store.settings()['favorites_checkpoint']==['123456789']


def test_unconfirmed_empty_is_not_success_and_expiry_is_visible(store):
    bridge,_=pair(store)
    work=request(store,bridge)
    with pytest.raises(TaskError,match='不能记为成功'):
        bridge.complete(result(work,items=[],head_ids=[]))
    store.run('UPDATE browser_requests SET created_at=? WHERE job_id=?',(time.time()-901,work['job_id']))
    bridge.expire()
    row=store.one('SELECT status,error_code FROM jobs WHERE id=?',(work['job_id'],))
    assert row=={'status':'attention','error_code':'browser_offline'}


def test_probe_reads_twenty_without_advancing_incremental_checkpoint(store):
    bridge,_=pair(store)
    store.set('favorites_checkpoint',['200000'])
    engine=JobEngine(store)
    jid=engine.enqueue('favorites_probe')
    store.run("UPDATE jobs SET status='running' WHERE id=?",(jid,))
    assert engine.execute('favorites_probe',{},lambda *args:None,job_id=jid)=={'_await_browser':True}
    work=bridge.work()['request']
    assert work['limit']==20 and work['stop_ids']==[]
    saved=bridge.complete(result(work))
    assert saved['probe'] and store.settings()['favorites_checkpoint']==['200000']


def test_known_history_continuation_keeps_the_job_running_and_renews_claim(store):
    bridge,_=pair(store)
    services.ingest(store,{'id':'123456789','title':'已入库收藏'},'favorites')
    work=request(store,bridge,mode='backfill')
    scan=result(work,items=[],head_ids=['123456789'],truncated=True,continuation=True,stop_reason='scan_limit',reader_state={'rounds':24,'scroll_top':5000,'scroll_height':6000,'viewport_height':900})
    assert bridge.complete(scan)['continuation']
    assert store.one('SELECT status FROM jobs WHERE id=?',(work['job_id'],))['status']=='running'
    renewed=bridge.work()['request']
    assert renewed['job_id']==work['job_id'] and renewed['nonce']!=work['nonce']
    with pytest.raises(TaskError):bridge.complete(scan)
    saved=bridge.complete(result(renewed,items=[{'id':'222222222','title':'更早的新收藏'}]))
    assert saved['added']==1
    assert store.one('SELECT status FROM jobs WHERE id=?',(work['job_id'],))['status']=='succeeded'


def test_history_continuation_cannot_report_unknown_empty_results_as_progress(store):
    bridge,_=pair(store)
    work=request(store,bridge,mode='backfill')
    with pytest.raises(TaskError,match='缺少已入库'):
        bridge.complete(result(work,items=[],head_ids=['999999999'],truncated=True,continuation=True,stop_reason='scan_limit',reader_state={'rounds':24,'scroll_top':5000,'scroll_height':6000,'viewport_height':900}))


def test_foreground_is_explicit_and_stalled_page_pauses_scheduled_collection(store):
    from datetime import datetime
    from app.jobs import TZ
    bridge,_=pair(store)
    work=request(store,bridge)
    assert work['foreground'] is False
    bridge.complete(result(work,truncated=True,reader_revision='dom-bottom-v2',stop_reason='page_stalled'))
    assert store.one('SELECT status,error_code FROM jobs WHERE id=?',(work['job_id'],)) == {'status':'attention','error_code':'pagination_stalled'}
    assert store.settings()['favorites_checkpoint']==[]
    config=store.settings()['automation']
    store.set('automation',{**config,'enabled':True})
    engine=JobEngine(store)
    engine.schedule_tick(datetime(2026,11,1,12,0,tzinfo=TZ))
    assert store.one("SELECT COUNT(*) n FROM jobs WHERE kind='favorites_sync'")['n']==1
    jid=engine.enqueue('favorites_sync',{'foreground':True})
    store.run("UPDATE jobs SET status='running' WHERE id=?",(jid,))
    bridge.request('sync',jid)
    assert bridge.work()['request']['foreground'] is True


def test_history_import_keeps_recent_favorites_first_without_faking_collection_time(store):
    bridge,_=pair(store)
    def material(identity):
        return {'id':identity,'title':'AI工具 '+identity}
    bridge.complete(result(request(store,bridge),items=[material(x) for x in ['300000','200000','100000']],head_ids=['300000','200000','100000']))
    history=request(store,bridge,mode='backfill')
    bridge.complete(result(history,items=[material('090000')],head_ids=['300000'],truncated=True,stop_reason='batch_limit'))
    row=store.one('SELECT status,message FROM jobs WHERE id=?',(history['job_id'],))
    assert row['status']=='succeeded' and '本批完成' in row['message']
    bridge.complete(result(request(store,bridge),items=[material('400000')],head_ids=['400000','300000','200000'],reached_checkpoint=True))
    ranked=services.ranked_videos(store,source='favorites',mode='recent')
    assert [r['id'] for r in ranked]==['400000','300000','200000','100000','090000']
    assert all(r['collected_at'] is None for r in ranked)


def test_revoke_and_restore_invalidate_bridge_credentials(client,store):
    bridge,token=pair(store)
    snapshot=services.backup(store)['name']
    bridge.revoke()
    with pytest.raises(TaskError): bridge.authorize('Bearer '+token,ORIGIN)
    services.restore(store,snapshot)
    with pytest.raises(TaskError): bridge.authorize('Bearer '+token,ORIGIN)
    assert not bridge.status()['paired']


def test_extension_download_contains_only_component_files_and_narrow_permissions(client):
    response=client.get('/api/connections/edge/extension.zip')
    assert response.status_code==200
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        names=z.namelist()
        assert len(names)==9 and all(n.startswith('edge-extension/') for n in names)
        assert not any(n.endswith(('.sqlite3','.env')) for n in names)
        manifest=json.loads(z.read('edge-extension/manifest.json'))
        assert set(manifest['permissions'])=={'scripting','storage','alarms'}
        assert set(manifest['host_permissions'])=={'https://www.douyin.com/*','http://127.0.0.1/*'}
