import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app import services
from app.db import now
from app.edge_bridge import EdgeBridge
from app.jobs import JobEngine, TZ
from app.providers import DeepSeek, TaskError


def material(store):
    services.ingest(store, {'id':'123456789','title':'AI知识库实操','author_id':'author','author':'作者','published_at':now(),'likes':1000,'followers':5000})
    return '123456789'


def prices(store):
    price = store.settings()['pricing']; price['confirmed'] = True
    store.set('pricing',price); store.set('data_pricing_confirmed',True)


@pytest.mark.parametrize('minutes',[240,480,720,1440])
def test_both_sync_intervals_offer_four_hours_upward(client,store,minutes):
    automation = store.settings()['automation']
    automation.update(favorites_minutes=minutes,creator_minutes=minutes)
    assert client.patch('/api/settings',json={'automation':automation}).status_code == 200
    for key in ['favorites_minutes','creator_minutes']:
        invalid = {**automation,key:60}
        assert client.patch('/api/settings',json={'automation':invalid}).status_code == 400
    assert client.patch('/api/settings',json={'automation':{**automation,'backfill_enabled':True}}).status_code == 400


def test_creator_daily_interval_does_not_reset_at_midnight(store,monkeypatch):
    prices(store); monkeypatch.setattr('app.jobs.get_secret',lambda *args:'test')
    settings=store.settings()['automation']; settings.update(enabled=True,creator_minutes=1440)
    store.set('automation',settings)
    current=datetime(2026,11,3,12,0,tzinfo=TZ)
    previous=current-timedelta(hours=23,minutes=59)
    store.set('last_sync',{'creators':previous.isoformat()})
    store.run("INSERT INTO creators (id,name,url) VALUES ('author','作者','https://www.douyin.com/user/author')")
    engine=JobEngine(store)
    engine.schedule_tick(current)
    assert store.one("SELECT COUNT(*) n FROM jobs WHERE kind='creators_sync'")['n']==0
    monkeypatch.setattr('app.jobs.now',lambda:(current+timedelta(minutes=2)).isoformat())
    engine.schedule_tick(current+timedelta(minutes=2))
    assert store.one("SELECT COUNT(*) n FROM jobs WHERE kind='creators_sync'")['n']==1


def test_cancel_returns_while_worker_holds_restore_lock(client,store,app):
    engine=app.state.engine
    identity=engine.enqueue('creators_sync')
    store.run("UPDATE jobs SET status='running' WHERE id=?",(identity,))
    locked,release=threading.Event(),threading.Event()
    def hold():
        with engine.restore_lock:
            locked.set(); release.wait(3)
    thread=threading.Thread(target=hold); thread.start(); assert locked.wait(1)
    try:
        began=time.monotonic()
        response=client.post(f'/api/jobs/{identity}/cancel',json={})
        assert time.monotonic()-began<1
        assert response.json()['cancelled'] is True
        assert client.get('/api/jobs/'+identity).json()['status']=='cancelled'
    finally:
        release.set(); thread.join(2)
    assert client.post(f'/api/jobs/{identity}/cancel',json={}).json()['cancelled'] is False
    assert client.post('/api/jobs/missing/cancel',json={}).status_code==404


def test_cancellation_stops_next_creator_page_and_model_classification(store,monkeypatch):
    material(store)
    for author in ['author','other']:
        store.run('INSERT INTO creators VALUES (?,?,?,?,?,?)',(author,author,'https://www.douyin.com/user/'+author,5000,1,now()))
    engine=JobEngine(store); identity=engine.enqueue('creators_sync')
    store.run("UPDATE jobs SET status='running' WHERE id=?",(identity,))
    calls=[]
    def posts(_,author,cursor=0):
        calls.append((author,cursor)); engine.cancel(identity)
        return {'data':{'aweme_list':[{'aweme_id':'123456789','desc':'AI教程','create_time':int(time.time())}],'has_more':True,'max_cursor':100}}
    monkeypatch.setattr('app.jobs.TikHub.posts',posts)
    monkeypatch.setattr(engine,'classify_pending',lambda:pytest.fail('cancelled sync must not enqueue AI work'))
    with pytest.raises(TaskError) as error:
        engine.execute('creators_sync',{},lambda *args:None,job_id=identity)
    assert error.value.code=='cancelled'
    assert len(calls)==1


def test_cancelled_http_error_is_not_retried_or_released_as_free(store,monkeypatch):
    material(store); prices(store)
    store.run('INSERT INTO creators VALUES (?,?,?,?,?,?)',('author','作者','https://www.douyin.com/user/author',5000,1,now()))
    engine=JobEngine(store); identity=engine.enqueue('creators_sync')
    store.run("UPDATE jobs SET status='running' WHERE id=?",(identity,))
    calls=[]
    def request(*args,**kwargs):
        calls.append(1); engine.cancel(identity)
        return httpx.Response(400,request=httpx.Request('GET','https://api.tikhub.io/test'))
    monkeypatch.setattr('app.providers.get_secret',lambda *args:'test-key')
    monkeypatch.setattr(httpx.Client,'request',request)
    with pytest.raises(TaskError) as error:
        engine.execute('creators_sync',{},lambda *args:None,job_id=identity)
    assert error.value.code=='cancelled' and len(calls)==1
    assert store.one("SELECT COUNT(*) n FROM expenses WHERE status!='released'")['n']==1


def test_sync_buttons_reuse_active_task_then_enforce_manual_cooldown(client,store,app):
    engine=app.state.engine
    first=client.post('/api/jobs',json={'kind':'favorites_sync','payload':{'foreground':True}}).json()['job_id']
    assert client.post('/api/jobs',json={'kind':'favorites_backfill','payload':{'foreground':True}}).json()['job_id']==first
    bridge=EdgeBridge(store); pairing=bridge.pairing(); bridge.pair(pairing['code'],'a'*32)
    store.run("UPDATE jobs SET status='running' WHERE id=?",(first,)); bridge.request('sync',first); assert bridge.work()['request']
    assert '本轮' in engine.cancel(first)['message']
    response=client.post('/api/jobs',json={'kind':'favorites_sync','payload':{}})
    assert response.status_code==400 and response.json()['code']=='collection_cooldown'
    assert bridge.status()['access_cooldown_seconds']>500


def test_repeated_topic_clicks_share_one_unfinished_project(store):
    identity=material(store)
    with ThreadPoolExecutor(max_workers=4) as pool:
        projects=list(pool.map(lambda _:services.create_project(store,identity),range(4)))
    assert len(set(projects))==1
    assert store.one('SELECT COUNT(*) n FROM projects')['n']==1


def test_verified_checklist_is_saved_and_restored_with_versions(client,store):
    project=services.create_project(store,material(store))
    services.update_project(store,project,1,{'verification':['核对工具版本','录屏证明效果']})
    response=client.patch('/api/projects/'+project,json={'revision':2,'changes':{'verified_items':['核对工具版本']}})
    assert response.status_code==200
    assert client.get('/api/projects/'+project).json()['verified_items']==['核对工具版本']
    assert client.patch('/api/projects/'+project,json={'revision':3,'changes':{'verified_items':['编造事项']}}).status_code==400
    assert client.post('/api/projects/'+project+'/restore',json={'revision':3,'version':2}).status_code==200
    assert client.get('/api/projects/'+project).json()['verified_items']==[]


@pytest.mark.skip(reason="已下线的知识库/制作行为；由 test_discovery 的停用与数据保留检查替代")
def test_hook_generation_prepares_missing_transcript_without_selecting_a_hook(store,monkeypatch):
    identity=material(store); project=services.create_project(store,identity); called=[]
    def transcribe(_,video,progress):
        called.append('transcribe'); services.save_transcript(store,video,'这是实际提供的测试原文。')
    def generate(*args,**kwargs):
        called.append('hooks')
        return {'hooks':[{'text':str(i),'mechanism':'结果先行','visual':'录屏展示','promise':'演示真实结果'} for i in range(3)]}
    monkeypatch.setattr('app.jobs.transcribe',transcribe); monkeypatch.setattr(DeepSeek,'json',generate)
    JobEngine(store).execute('hooks',{'project_id':project,'revision':1},lambda *args:None)
    assert called==['transcribe','hooks']
    assert services.project(store,project)['selected_hook']==''
    assert len(services.project(store,project)['hooks'])==3


def test_low_follower_ratio_is_likes_divided_by_sampled_followers(store):
    identity=material(store)
    store.set('low_ratio',0.2)
    assert [v['id'] for v in services.ranked_videos(store,mode='low')]==[identity] #1000/5000=20%, explicit historical/custom threshold.
    store.run('UPDATE videos SET followers=20000') #1000/20000=5%
    assert services.ranked_videos(store,mode='low')==[]


def test_partial_retry_only_requeues_failed_creators(client,store,app):
    engine=app.state.engine; identity=engine.enqueue('creators_sync')
    store.run("UPDATE jobs SET status='attention',result=? WHERE id=?",(json.dumps({'completed_creators':30,'failed_creators':[{'id':'one-failure'}]}),identity))
    response=client.post(f'/api/jobs/{identity}/retry',json={})
    assert response.status_code==200
    queued=store.one('SELECT payload FROM jobs WHERE id=?',(response.json()['job_id'],))
    assert json.loads(queued['payload'])=={'creator_ids':['one-failure']}


@pytest.mark.parametrize('purpose',['analysis','hooks','script'])
def test_creative_tasks_use_latest_alias_with_deep_thinking_budget(store,monkeypatch,purpose):
    prices(store); monkeypatch.setattr('app.providers.get_secret',lambda *args:'test-only')
    original=httpx.Client
    def handler(request):
        body=json.loads(request.content)
        assert body['model']=='deepseek-flash'
        assert body['thinking']['type']=='enabled' and body['reasoning_effort']=='max'
        assert body['max_tokens']>=16000 and 'temperature' not in body
        parts=[{'choices':[{'delta':{'reasoning_content':'not returned to UI'}}]}, {'choices':[{'delta':{'content':'{"ok":true}'},'finish_reason':'stop'}]}, {'choices':[],'usage':{'prompt_tokens':20,'completion_tokens':50}}]
        return httpx.Response(200,text='\n\n'.join('data: '+json.dumps(p) for p in parts)+'\n\ndata: [DONE]\n')
    monkeypatch.setattr('app.providers.httpx.Client',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    assert DeepSeek(store).json(purpose,{},'输出json',100)=={'ok':True}
    expense=store.one('SELECT * FROM expenses')
    assert expense['actual']<expense['reserved']


def test_analysis_cache_is_reused_but_explicit_regenerate_runs_again(store,monkeypatch):
    identity=material(store); services.save_transcript(store,identity,'实际逐字稿，不是视频简介')
    calls=[]
    def generate(*args,**kwargs):
        calls.append(1)
        return {'summary':'总结','audience':'AI小白','angle':'实操','hook':'开头','structure':[{'quote':'原句','role':'步骤','reason':'真实演示'}],'opportunities':[],'requirements':[],'verification':[]}
    monkeypatch.setattr(DeepSeek,'json',generate)
    services.analyze(store,identity); services.analyze(store,identity)
    assert len(calls)==1
    services.analyze(store,identity,force=True)
    assert len(calls)==2


def test_metadata_refresh_does_not_change_favorite_observation(store):
    identity=material(store)
    services.ingest(store,{'id':identity,'title':'原始收藏'},'favorites','my')
    before=store.rows('SELECT * FROM sources ORDER BY kind')
    services.ingest(store,{'id':identity,'title':'补全标题','published_at':now()},touch_source=False)
    assert store.rows('SELECT * FROM sources ORDER BY kind')==before


@pytest.mark.skip(reason="已下线的知识库/制作行为；由 test_discovery 的停用与数据保留检查替代")
def test_deep_model_stream_cancellation_preserves_uncertain_cost_and_original_draft(store,monkeypatch):
    prices(store); monkeypatch.setattr('app.providers.get_secret',lambda *args:'test-only')
    identity=material(store); project=services.create_project(store,identity)
    services.save_transcript(store,identity,'测试原文')
    engine=JobEngine(store); job=engine.enqueue('hooks',{'project_id':project,'revision':1})
    store.run("UPDATE jobs SET status='running' WHERE id=?",(job,))
    original=httpx.Client
    def handler(request):
        engine.cancel(job)
        return httpx.Response(200,text='data: {"choices":[{"delta":{"reasoning_content":"pending"}}]}\n\n')
    monkeypatch.setattr('app.providers.httpx.Client',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    with pytest.raises(TaskError) as error:
        engine.execute('hooks',{'project_id':project,'revision':1},lambda *args:None,job_id=job)
    assert error.value.code=='cancelled'
    assert services.project(store,project)['revision']==1
    assert store.one('SELECT status FROM expenses')['status']=='unknown'
    store.run('UPDATE videos SET followers=NULL')
    assert services.ranked_videos(store,mode='low')==[]
