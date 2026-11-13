import json
from datetime import datetime,timedelta,timezone

import pytest

from app import services
from app.db import now
from app.hotspots import parse_hot_words,refresh_hotspots,select_terms
from app.jobs import JobEngine,TZ
from app.providers import DeepSeek,TaskError,TikHub


def raw_video(identity='991000001',title='AI教程热点',likes=120000):
    return {'aweme_id':identity,'desc':title,'create_time':int(datetime.now(timezone.utc).timestamp()),'author':{'nickname':'合成作者','sec_uid':'SYNTHETIC_'+identity},'statistics':{'digg_count':likes,'comment_count':10,'collect_count':20}}


def seed(store,identity,**changes):
    value={'id':identity,'title':'AI教程 '+identity,'author_id':'SYNTHETIC_'+identity,'published_at':now(),'followers_at':now(),'followers':900000,'likes':120000}
    value.update(changes)
    services.ingest(store,value,changes.pop('source','search'))


def ids(response):
    assert response.status_code==200,response.text
    return {v['id'] for v in response.json()['items']}


def test_hot_words_parse_wrappers_deduplicate_and_reject_unknown_formats():
    assert parse_hot_words({'data':{'data':{'word_list':[{'word':'AI新工具'},{'word':'AI新工具'},{'word':'新模型\n指令'},{'word_name':'Gemini发布'}]}}})==['AI新工具','Gemini发布']
    assert parse_hot_words({'data':{'word_list':[]}})==[]
    with pytest.raises(TaskError):parse_hot_words({'data':{'unrecognized':123}})


def test_model_can_select_only_real_trend_words_and_cache_is_reused(store,monkeypatch):
    prices=store.settings()['pricing'];prices['confirmed']=True;store.set('pricing',prices)
    monkeypatch.setattr('app.hotspots.get_secret',lambda *a:'test-only')
    calls=[]
    def choose(*args,**kwargs):
        calls.append(1)
        return {'selected':[{'id':1,'reason':'工具发布'},{'id':999,'reason':'不存在'},{'id':True,'reason':'无效'},{'id':2,'reason':'模型更新'}]}
    monkeypatch.setattr(DeepSeek,'json',choose)
    words=['新AI工具发布','新模型上线','无关事件']
    original=store.settings()['keywords'][:]
    first,issues=select_terms(store,words);second,_=select_terms(store,words)
    assert first==second and len(calls)==1 and not issues
    assert {x['keyword'] for x in first if x['origin'].startswith('热榜')}==set(words[:2])
    assert len(first)<=5 and store.settings()['keywords']==original


def test_indirect_social_news_is_rejected_even_when_model_selects_it(store,monkeypatch):
    prices=store.settings()['pricing'];prices['confirmed']=True;store.set('pricing',prices)
    monkeypatch.setattr('app.hotspots.get_secret',lambda *a:'test-only')
    monkeypatch.setattr(DeepSeek,'json',lambda *a,**kw:{'selected':[{'id':1,'reason':'AI工具更新'},{'id':2,'reason':'可以牵强关联AI安全'}]})
    terms,_=select_terms(store,['Gemini新功能','网信开展科普健康类网络谣言整治'])
    assert [t['keyword'] for t in terms if t['origin'].startswith('热榜')]==['Gemini新功能']


def test_refresh_caps_queries_uses_both_sorts_and_never_fetches_follower_profiles(store,monkeypatch):
    monkeypatch.setattr('app.hotspots.get_secret',lambda *a:'')
    monkeypatch.setattr(TikHub,'hot_search',lambda self:{'data':{'word_list':[{'word':'Gemini新模型'},{'word':'即梦新功能'},{'word':'Codex新工具'},{'word':'Claude更新'}]}})
    calls=[]
    def search(self,keyword,**options):
        calls.append((keyword,options))
        return {'data':{'aweme_list':[raw_video()]}}
    monkeypatch.setattr(TikHub,'search',search)
    monkeypatch.setattr(TikHub,'profile',lambda *a:pytest.fail('hotspots must not require followers'))
    original=store.settings()['keywords'][:]
    result=refresh_hotspots(store,lambda *a:None)
    assert result['request_attempts']==11 and len(calls)==10
    assert {o['sort_type'] for _,o in calls}=={'1','2'}
    assert all(o['publish_time']=='7' for _,o in calls)
    assert len({k for k,_ in calls})==5
    assert result['observed_videos']==1 and result['added']==1
    assert store.one('SELECT COUNT(*) n FROM videos')['n']==1
    assert store.one("SELECT COUNT(*) n FROM sources WHERE kind='hotspot'")['n']==10
    assert store.settings()['keywords']==original and store.settings()['last_sync']['hotspots']


def test_hot_list_failure_is_visible_and_base_queries_still_run_without_retry(store,monkeypatch):
    monkeypatch.setattr('app.hotspots.get_secret',lambda *a:'')
    def broken(*a):raise TaskError('provider_error','热榜暂不可用',http_status=400)
    monkeypatch.setattr(TikHub,'hot_search',broken)
    calls=[]
    monkeypatch.setattr(TikHub,'search',lambda self,k,**kw:calls.append((k,kw)) or {'data':{'aweme_list':[]}})
    result=refresh_hotspots(store,lambda *a:None)
    assert len(calls)==4 and result['request_attempts']==5
    assert result['issues'][0]['stage']=='热榜' and result['completed_queries']==4


def test_all_failed_queries_keep_last_success_and_remain_failures(store,monkeypatch):
    store.set('last_sync',{'hotspots':'2026-01-01T00:00:00+00:00'})
    monkeypatch.setattr('app.hotspots.get_secret',lambda *a:'')
    monkeypatch.setattr(TikHub,'hot_search',lambda self:{'data':{'word_list':[]}})
    calls=[]
    def broken(self,keyword,**kw):
        calls.append(keyword);raise TaskError('provider_error','查询失败',http_status=400)
    monkeypatch.setattr(TikHub,'search',broken)
    result=refresh_hotspots(store,lambda *a:None)
    assert len(calls)==4 and result['completed_queries']==0
    assert all(q['status']=='failed' for q in result['queries'])
    assert store.settings()['last_sync']['hotspots']=='2026-01-01T00:00:00+00:00'


def test_budget_interrupt_preserves_partial_results_without_claiming_completed_round(store,monkeypatch):
    monkeypatch.setattr('app.hotspots.get_secret',lambda *a:'')
    monkeypatch.setattr(TikHub,'hot_search',lambda self:{'data':{'word_list':[]}})
    calls=[]
    def search(self,keyword,**kw):
        calls.append(keyword)
        if len(calls)>1:raise TaskError('budget_exceeded','数据预算已用完')
        return {'data':{'aweme_list':[raw_video()]}}
    monkeypatch.setattr(TikHub,'search',search)
    with pytest.raises(TaskError):refresh_hotspots(store,lambda *a:None)
    state=store.settings()['hotspot_state']
    assert state['completed_queries']==1 and state['observed_videos']==1
    assert state['queries'][-1]['code']=='budget_exceeded'
    assert 'hotspots' not in store.settings()['last_sync']


@pytest.mark.parametrize('code',['budget_exceeded','cancelled','credential_error'])
def test_fatal_condition_stops_before_more_data_requests(store,monkeypatch,code):
    calls=[]
    def broken(self):
        calls.append('hot');raise TaskError(code,'停止')
    monkeypatch.setattr(TikHub,'hot_search',broken)
    monkeypatch.setattr(TikHub,'search',lambda *a,**kw:pytest.fail('must stop'))
    with pytest.raises(TaskError) as exc:refresh_hotspots(store,lambda *a:None)
    assert exc.value.code==code and calls==['hot']


def test_hot_feed_accepts_large_and_unknown_followers_but_not_stale_irrelevant_or_low_likes(client,store):
    seed(store,'991000001')
    seed(store,'991000002',followers=None,likes=10000)
    seed(store,'991000003',title='做饭技巧',likes=999999)
    seed(store,'991000004',likes=9999)
    seed(store,'991000005',published_at=(datetime.now(timezone.utc)-timedelta(days=8)).isoformat())
    seed(store,'991000006')
    store.run('UPDATE videos SET metrics_at=? WHERE id=?',((datetime.now(timezone.utc)-timedelta(days=3)).isoformat(),'991000006'))
    assert ids(client.get('/api/discovery',params={'mode':'hot'}))=={'991000001','991000002'}
    assert ids(client.get('/api/discovery',params={'mode':'hot','hot_min_likes':100000}))=={'991000001'}
    assert ids(client.get('/api/discovery'))==set()


def test_hot_source_is_traceable_and_human_rejection_wins(client,store):
    services.ingest(store,{'id':'991000001','title':'新工具演示','likes':110000,'published_at':now()},'hotspot','1:Gemini新功能')
    services.ingest(store,{'id':'991000002','title':'健康监管新闻','likes':999999,'published_at':now()},'hotspot','1:网信开展科普健康类网络谣言整治')
    row=client.get('/api/discovery',params={'mode':'hot'}).json()['items'][0]
    assert row['followers'] is None and 'Gemini新功能' in ' '.join(row['reference_reasons'])
    client.post('/api/discovery/991000001/feedback',json={'action':'dismiss'})
    assert ids(client.get('/api/discovery',params={'mode':'hot'}))==set()
    client.post('/api/discovery/991000001/feedback',json={'action':'restore'})
    assert ids(client.get('/api/discovery',params={'mode':'hot'}))=={'991000001'}
    store.run('UPDATE videos SET manual_classification=1,is_ai=0 WHERE id=?',('991000001',))
    assert ids(client.get('/api/discovery',params={'mode':'hot'}))==set()


def test_hotspot_manual_updates_deduplicate_reuse_success_and_throttle_failure(client,store):
    first=client.post('/api/jobs',json={'kind':'hotspot_sync','payload':{}}).json()['job_id']
    assert client.post('/api/jobs',json={'kind':'hotspot_sync','payload':{}}).json()['job_id']==first
    store.run("UPDATE jobs SET status='succeeded',result=? WHERE id=?",(json.dumps({'completed_queries':4}),first))
    cached=client.post('/api/jobs',json={'kind':'hotspot_sync','payload':{}}).json()
    assert cached['job_id']==first and cached['cached']
    store.run("UPDATE jobs SET status='failed',result=NULL WHERE id=?",(first,))
    response=client.post('/api/jobs/'+first+'/retry',json={})
    assert response.status_code==400 and response.json()['code']=='rate_limited'
    assert store.one("SELECT COUNT(*) n FROM jobs WHERE kind='hotspot_sync'")['n']==1


def test_hotspot_daily_schedule_requires_opt_in_and_does_not_repeat_after_restart(store,monkeypatch):
    monkeypatch.setattr('app.jobs.get_secret',lambda *a:'test-only')
    prices=store.settings()['pricing'];prices['confirmed']=True;store.set('pricing',prices);store.set('data_pricing_confirmed',True)
    auto=store.settings()['automation'];auto['enabled']=True;store.set('automation',auto)
    current=datetime.now(TZ).replace(hour=9,minute=0,second=0,microsecond=0)
    engine=JobEngine(store);engine.schedule_tick(current)
    assert not store.rows("SELECT id FROM jobs WHERE kind='hotspot_sync'")
    store.set('hotspots',{'enabled':True,'min_likes':10000})
    engine.schedule_tick(current)
    assert len(store.rows("SELECT id FROM jobs WHERE kind='hotspot_sync'"))==1
    store.run("UPDATE jobs SET status='succeeded' WHERE kind='hotspot_sync'")
    JobEngine(store).schedule_tick(current.replace(hour=23))
    assert len(store.rows("SELECT id FROM jobs WHERE kind='hotspot_sync'"))==1


@pytest.mark.parametrize('value',[{'enabled':'yes','min_likes':10000},{'enabled':True,'min_likes':0},{'enabled':True,'min_likes':True},{'enabled':True,'min_likes':10000,'pages':100}])
def test_hotspot_settings_reject_invalid_values_and_unbounded_requests(client,value):
    assert client.patch('/api/settings',json={'hotspots':value}).status_code==400
