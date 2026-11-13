import json
from datetime import datetime,timedelta,timezone

import pytest

from app import services
from app.db import now,encode
from app.jobs import JobEngine
from app.providers import TikHub,TaskError
from app.hotspots import refresh_hotspots,select_terms
from app.onboarding import apply_profile,validate_profile,validate_tags

PROFILE={'domain':'居家健身','audience':'上班族新手','goal':'找可复现教程','formats':['动作讲解'],'include_keywords':['居家训练','练腿'],'search_keywords':['居家练腿'],'exclude_keywords':['代餐'],'prefer_low_followers':True}


def test_profile_shared_by_video_refresh_and_creator_with_original_time(store,monkeypatch):
    calls=[];stamp=now()
    services.record_author_followers(store,'SYNTHETIC_AUTHOR','作者',5000,stamp)
    services.ingest(store,{'id':'984000001','title':'AI工具教程'},'favorites')
    monkeypatch.setattr(TikHub,'detail',lambda self,i:{'id':i,'title':'AI工具教程','author_id':'SYNTHETIC_AUTHOR'})
    monkeypatch.setattr(TikHub,'profile',lambda *a:calls.append(1) or {'name':'作者','followers':9000})
    engine=JobEngine(store)
    engine.execute('video_refresh',{'video_id':'984000001'},lambda *a:None)
    assert calls==[]
    row=store.one('SELECT followers,followers_at FROM videos WHERE id=?',('984000001',))
    assert row=={'followers':5000,'followers_at':stamp}


def test_profile_failure_does_not_repeat_across_callers(store,monkeypatch):
    calls=[]
    def fail(*a):
        calls.append(1)
        raise TaskError('provider_error','拒绝',http_status=400)
    monkeypatch.setattr(TikHub,'profile',fail)
    for _ in range(3):
        with pytest.raises(TaskError):services.author_profile(store,TikHub(store),'SYNTHETIC_AUTHOR')
    assert len(calls)==1


@pytest.mark.parametrize('status',[400,503])
def test_ordinary_data_has_no_automatic_paid_retry(store,monkeypatch,status):
    calls=[]
    def fail(*a,**kw):
        calls.append(1)
        raise TaskError('provider_error','拒绝',http_status=status)
    monkeypatch.setattr(TikHub,'_request_once',fail)
    with pytest.raises(TaskError):TikHub(store).posts('SYNTHETIC_AUTHOR')
    assert len(calls)==1


def test_successful_quick_clicks_reuse_same_video_and_creator_jobs(store):
    e=JobEngine(store)
    for kind,p in [('video_refresh',{'video_id':'984000001'}),('creators_sync',{})]:
        i=e.enqueue(kind,p)
        store.run("UPDATE jobs SET status='succeeded',result=? WHERE id=?",(encode({'creator_ids_seen':[]}),i))
        assert e.enqueue(kind,p)==i
    assert len(store.rows('SELECT id FROM jobs'))==2
    assert e.enqueue('video_refresh',{'video_id':'984000002'})!=i


def test_import_and_refresh_use_same_active_video_job_key(store):
    e=JobEngine(store);p={'video_id':'984000001'}
    first=e.enqueue('video_refresh',p,'video:984000001')
    assert e.enqueue('video_refresh',p,'video_refresh:984000001')==first


def test_all_failed_search_does_not_advance_success_timestamp(store,monkeypatch):
    prior='2026-01-01T00:00:00+00:00';store.set('last_sync',{'search':prior})
    def fail(*a,**kw):raise TaskError('provider_error','拒绝',http_status=400)
    monkeypatch.setattr(TikHub,'search',fail)
    result=JobEngine(store).execute('search',{'keywords':['AI教程']},lambda *a:None)
    assert result['completed_keywords']==0 and store.settings()['last_sync']['search']==prior


def test_search_does_not_fetch_profiles_of_unrelated_old_local_candidates(store,monkeypatch):
    services.ingest(store,{'id':'984000001','title':'AI教程','author_id':'OLD_LOCAL','likes':9999,'published_at':now()},'favorites')
    monkeypatch.setattr(TikHub,'search',lambda *a,**kw:{'data':{}})
    monkeypatch.setattr(TikHub,'profile',lambda *a:pytest.fail('no new candidate needs profile'))
    result=JobEngine(store).execute('search',{'keywords':['AI教程']},lambda *a:None)
    assert result['completed_keywords']==1


def test_hotspot_retry_reuses_successes_and_only_calls_failed_query(store,monkeypatch):
    previous={'attempted_at':now(),'terms':[{'keyword':'AI教程','origin':'发现关键词'}],'queries':[{'keyword':'AI教程','sort':'最多点赞','status':'succeeded'},{'keyword':'AI教程','sort':'最新发布','status':'failed'}],'issues':[],'observed_ids':['984000001'],'observed_videos':1}
    calls=[]
    monkeypatch.setattr(TikHub,'hot_search',lambda *a:pytest.fail('reuse hot list'))
    monkeypatch.setattr(TikHub,'search',lambda self,k,**kw:calls.append((k,kw['sort_type'])) or {'data':{}})
    result=refresh_hotspots(store,lambda *a:None,previous=previous)
    assert calls==[('AI教程','2')]
    assert result['request_attempts']==1 and result['reused_queries']==1
    assert result['completed_queries']==2 and result['observed_videos']==1


def test_hotspot_retry_api_points_to_original_checkpoint(client,store,app):
    old=(datetime.now(timezone.utc)-timedelta(minutes=11)).isoformat()
    i=app.state.engine.enqueue('hotspot_sync')
    result={'terms':[{'keyword':'AI教程'}],'queries':[{'status':'failed','keyword':'AI教程','sort':'最多点赞'}]}
    store.run("UPDATE jobs SET status='attention',created_at=?,result=? WHERE id=?",(old,encode(result),i))
    r=client.post('/api/jobs/'+i+'/retry',json={})
    assert r.status_code==200,r.text
    assert json.loads(store.one('SELECT payload FROM jobs WHERE id=?',(r.json()['job_id'],))['payload'])=={'retry_from':i}


@pytest.mark.parametrize('domain,word,unrelated',[('居家健身','居家训练','AI新模型'),('家常菜','家常菜','旅行攻略'),('装修','装修','美妆护肤')])
def test_non_ai_hot_and_low_feeds_are_industry_specific(client,store,domain,word,unrelated):
    apply_profile(store,{**PROFILE,'domain':domain,'include_keywords':[word],'search_keywords':[word]})
    for i,title in enumerate([word+'教学',unrelated,word+'代餐'],1):
        services.ingest(store,{'id':str(984000000+i),'title':title,'author_id':'SYNTHETIC_'+str(i),'likes':20000,'followers':10000,'followers_at':now(),'published_at':now()},'search')
    for mode in ('hot','focus'):
        r=client.get('/api/discovery',params={'mode':mode})
        assert r.status_code==200
        assert [x['id'] for x in r.json()['items']]==['984000001']
        reasons=r.json()['items'][0]['reference_reasons']
        assert len(reasons)==len(set(reasons))
    assert store.one('SELECT is_ai FROM videos WHERE id=?',('984000001',))['is_ai']==0
    assert store.settings()['automation']['enabled'] is False


def test_tags_are_not_implicitly_searched_or_paid(store):
    tag={'tag':'#我在抖音学ai','platform':'抖音','purpose':'主题','status':'待核实','source_url':'https://betteropc.com/self-media/traffic-tags','checked_at':now(),'valid_until':''}
    changes=apply_profile(store,{**PROFILE,'traffic_tags':[tag]})
    assert changes['keywords']==['居家练腿']
    assert changes['traffic_tags'][0]['tag']=='我在抖音学ai'
    assert store.one('SELECT count(*) n FROM expenses')['n']==0
    assert store.settings()['automation']['enabled'] is False
    with pytest.raises(TaskError):validate_tags([{**tag,'source_url':'https://user:password@example.com'}])


def test_matching_does_not_use_author_name_or_personal_notes_as_content_evidence():
    from app.onboarding import match_domain
    d=validate_profile(PROFILE)['creator_domain']
    assert match_domain({'title':'完全无关','author':'居家训练','notes':'练腿'},d)==[]


def test_collection_choices_follow_interview_without_overwriting_manual_shelves(client,store):
    apply_profile(store,{**PROFILE,'categories':['动作纠错','训练安排','其他']})
    choices=client.get('/api/collection-groups').json()['groups']
    assert '动作纠错' in choices and 'ai_tutorial' not in choices
    assert list(choices.values()).count('其他')==1


def test_expense_context_identifies_job_without_storing_credentials(store):
    from app.providers import Budget
    from app.task_context import cancellation_scope
    store.run('INSERT INTO expenses VALUES(?,?,?,?,?,?,?,?,?)',('fake','2026-11','data','fake',0.008,None,'reserved',None,now()))
    with cancellation_scope(lambda:None,{'job_id':'fake-job','job_kind':'creators_sync'}):
        Budget(store).settle('fake',actual=0.008,usage={'http_status':200})
    usage=json.loads(store.one('SELECT usage FROM expenses WHERE id=?',('fake',))['usage'])
    assert usage=={'job_id':'fake-job','job_kind':'creators_sync','http_status':200}


def test_manual_full_search_before_daily_slot_avoids_another_paid_round(store,monkeypatch):
    from app.jobs import TZ
    prices=store.settings()['pricing'];prices['confirmed']=True;store.set('pricing',prices)
    store.set('data_pricing_confirmed',True)
    auto=store.settings()['automation'];auto['enabled']=True;store.set('automation',auto)
    store.set('keywords',['居家练腿'])
    monkeypatch.setattr('app.jobs.get_secret',lambda *a:'fake')
    e=JobEngine(store);i=e.enqueue('search')
    current=datetime.now(TZ).replace(hour=8,minute=30,second=0,microsecond=0)
    stamp=(current-timedelta(minutes=1)).isoformat()
    store.run("UPDATE jobs SET status='succeeded',result=?,updated_at=? WHERE id=?",(encode({'searched_keywords':['居家练腿'],'completed_keywords':1,'failed_keywords':[]}),stamp,i))
    e.schedule_tick(current)
    assert len(store.rows("SELECT id FROM jobs WHERE kind='search'"))==1


def test_active_partial_manual_search_does_not_consume_full_daily_slot(store):
    e=JobEngine(store);i=e.enqueue('search',{'keywords':['居家练腿']})
    e.once('search:synthetic:8','search')
    assert not store.rows("SELECT slot FROM schedule_runs WHERE slot='search:synthetic:8'")
    store.run("UPDATE jobs SET status='succeeded' WHERE id=?",(i,))
    e.once('search:synthetic:8','search')
    assert len(store.rows("SELECT id FROM jobs WHERE kind='search'"))==2
