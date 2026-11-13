from datetime import datetime, timedelta, timezone
import pytest
from app import services
from app.db import Store, now
from app.discovery import RETIRED_JOBS
from app.jobs import JobEngine, TZ
from app.providers import TaskError


def seed(store, identity='111111111', source='search', **changes):
    values=dict(id=identity,title='AI教程：如何搭建自己的助手 '+identity,author='本地合成作者',author_id='SYNTHETIC_'+identity,published_at=now(),followers=5000,followers_at=now(),likes=2000)
    values.update(changes)
    services.ingest(store,values,source)
    return identity


def feed(client, mode='focus', **params):
    r=client.get('/api/discovery',params={'mode':mode,**params})
    assert r.status_code==200,r.text
    return r.json()


def ids(data):return {v['id'] for v in data['items']}


def test_feed_keeps_search_failure_visible_after_other_tasks_push_it_out_of_overview(client,store):
    store.run("INSERT INTO jobs(id,kind,payload,status,error_code,message,created_at,updated_at) VALUES ('failed-search','search','{}','failed','provider_error','搜索失败','2026-11-09T00:00:00+00:00','2026-11-09T00:01:00+00:00')")
    for i in range(12):
        store.run("INSERT INTO jobs(id,kind,payload,status,created_at,updated_at) VALUES (?,'backup','{}','succeeded',?,?)",(f'other-{i}',f'2026-11-09T01:{i:02d}:00+00:00',now()))
    assert not any(j['kind']=='search' for j in client.get('/api/overview').json()['jobs'])
    status=feed(client)['search_status']
    assert status['status']=='failed' and status['error_code']=='provider_error'
    assert status['created_at']=='2026-11-09T00:00:00+00:00'
    assert status['message']=='搜索失败'


def test_focus_publication_ranges_include_boundaries_and_default_to_72_hours(client,store,monkeypatch):
    instant=datetime(2026,11,9,0,0,tzinfo=timezone.utc)
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls,tz=None):return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)
    monkeypatch.setattr(services,'datetime',FrozenDatetime)
    ages=[12*3600,24*3600,24*3600+1,72*3600,72*3600+1,168*3600,168*3600+1]
    videos=[seed(store,str(600000000+i),published_at=(instant-timedelta(seconds=age)).isoformat(),followers_at=instant.isoformat()) for i,age in enumerate(ages)]
    for hours in (24,72,168):
        assert ids(feed(client,hours=hours))=={v for v,age in zip(videos,ages) if age<=hours*3600}
    assert ids(feed(client))==ids(feed(client,hours=72))


@pytest.mark.parametrize('hours',[0,-24,48,169])
def test_focus_rejects_unsupported_publication_ranges(client,hours):
    r=client.get('/api/discovery',params={'hours':hours})
    assert r.status_code==400 and r.json()['code']=='invalid_input'


def test_wider_focus_window_includes_favorites_but_preserves_metrics_and_dismissal_gates(client,store):
    older=(datetime.now(timezone.utc)-timedelta(days=5)).isoformat()
    good=seed(store,published_at=older)
    seed(store,'222222222',published_at=older,likes=100)
    seed(store,'333333333',published_at=older,followers=100000)
    seed(store,'444444444',published_at=older,followers=10000,likes=2000)
    seed(store,'555555555',source='favorites',published_at=older)
    seed(store,'666666666',source='manual',published_at=older)
    seed(store,'777777777',published_at=None)
    seed(store,'888888888',published_at=older,followers_at=(datetime.now(timezone.utc)-timedelta(days=3)).isoformat())
    hidden=seed(store,'999999999',published_at=older)
    client.post('/api/discovery/'+hidden+'/feedback',json={'action':'dismiss'})
    assert ids(feed(client))==set()
    assert ids(feed(client,hours=168))=={good,'555555555','666666666'}


def test_focus_range_does_not_shorten_creator_history_or_saved_topics(client,store):
    older=(datetime.now(timezone.utc)-timedelta(days=5)).isoformat()
    creator=seed(store,source='creator',published_at=older,likes=10,followers=100000)
    client.post('/api/discovery/'+creator+'/follow-author')
    saved=seed(store,'222222222',source='favorites',published_at='2020-01-01T00:00:00+00:00')
    client.patch('/api/videos/'+saved,json={'state':'备选'})
    for hours in (24,72,168):
        assert ids(feed(client,'creators',hours=hours))=={creator}
        assert ids(feed(client,'saved',hours=hours))=={saved}


def test_selected_candidates_survive_expiry_feedback_source_change_and_restart(client,store):
    v=seed(store)
    assert v in ids(feed(client))
    assert client.patch('/api/videos/'+v,json={'state':'备选','notes':'下周用自己的案例讲解这个开头'}).status_code==200
    saved_at=store.one('SELECT saved_at FROM candidate_saves WHERE video_id=?',(v,))['saved_at']
    assert v in ids(feed(client))  # Shortlisting annotates, rather than moving the work.
    client.post('/api/discovery/'+v+'/feedback',json={'action':'viewed'})
    services.ingest(store,dict(id=v,published_at='2020-01-01T00:00:00+00:00',likes=2,followers=999999),'favorites')
    Store(store.root)
    assert ids(feed(client))==set()
    row=feed(client,'saved')['items'][0]
    assert row['id']==v and row['saved_at']==saved_at
    assert '自己的案例' in row['notes']
    assert ids(feed(client,'saved',q='自己的案例'))=={v}
    assert ids(feed(client,'saved',q='没有这个词'))==set()


def test_favorite_and_legacy_selections_share_saved_view_without_fabricating_dates(client,store):
    old=seed(store,source='favorites',published_at=None,followers=None,likes=None)
    store.run("UPDATE videos SET state='备选' WHERE id=?",(old,))
    fresh=seed(store,'222222222',source='manual')
    client.post('/api/videos/batch',json={'ids':[fresh],'changes':{'state':'备选'}})
    assert ids(feed(client,'saved'))=={old,fresh}
    assert next(v for v in feed(client,'saved')['items'] if v['id']==old)['saved_at'] is None
    assert ids(feed(client))=={fresh}


def test_saved_search_filters_pagination_and_removal_do_not_delete_materials(client,store):
    for n in range(13):
        v=seed(store,str(800000000+n),author='合成作者乙' if n==0 else '合成作者甲')
        client.patch('/api/videos/'+v,json={'state':'备选','content_tracks':['AIGC'] if n==0 else ['AI教程']})
        store.run('UPDATE candidate_saves SET saved_at=? WHERE video_id=?',(f'2026-01-{n+1:02d}T00:00:00+00:00',v))
    first=feed(client,'saved');second=feed(client,'saved',offset=10)
    assert first['total']==13 and len(first['items'])==10 and len(second['items'])==3
    assert not ids(first)&ids(second)
    assert feed(client,'saved',sort='oldest')['items'][0]['id']=='800000000'
    assert ids(feed(client,'saved',track='AIGC',q='乙'))=={'800000000'}
    client.patch('/api/videos/800000000',json={'state':'待看'})
    assert feed(client,'saved')['total']==12
    assert store.one('SELECT id FROM videos WHERE id=?',('800000000',))


def test_only_disinterest_hides_candidates_and_restore_preserves_viewing_and_selection(client,store):
    a=seed(store);b=seed(store,'222222222')
    client.patch('/api/videos/'+a,json={'state':'备选'})
    client.post('/api/discovery/'+a+'/feedback',json={'action':'viewed'})
    client.post('/api/discovery/'+b+'/feedback',json={'action':'viewed'})
    client.post('/api/discovery/'+b+'/feedback',json={'action':'dismiss','reason':'暂时不做'})
    assert ids(feed(client))=={a}
    assert ids(feed(client,'seen'))=={a,b} and ids(feed(client,'dismissed'))=={b}
    assert ids(feed(client,'saved'))=={a}
    client.post('/api/discovery/'+b+'/feedback',json={'action':'restore'})
    assert ids(feed(client))=={a,b}
    assert ids(feed(client,'seen'))=={a,b}
    assert client.post('/api/discovery/'+b+'/feedback',json={'action':'unknown'}).status_code==400


def test_favorites_share_candidates_without_duplicates_or_false_collection_badges(client,store):
    favorite=seed(store,source='favorites')
    services.ingest(store,{'id':favorite},'search','AI教程')
    manual=seed(store,'222222222',source='manual')
    rows=feed(client)['items']
    assert len(rows)==2 and ids({'items':rows})=={favorite,manual}
    assert next(v for v in rows if v['id']==favorite)['is_favorite'] is True
    assert next(v for v in rows if v['id']==manual)['is_favorite'] is False
    client.post('/api/discovery/'+favorite+'/feedback',json={'action':'dismiss'})
    assert ids(feed(client))=={manual}
    assert ids(feed(client,'dismissed'))=={favorite}
    assert client.get('/api/videos',params={'scope':'personal'}).json()['total']==2
    client.post('/api/discovery/'+favorite+'/feedback',json={'action':'restore'})
    assert ids(feed(client))=={favorite,manual}
    assert store.one('SELECT COUNT(*) n FROM videos')['n']==2


def test_creator_work_stays_visible_after_collection_viewing_and_shortlisting(client,store):
    v=seed(store,source='creator')
    client.post('/api/discovery/'+v+'/follow-author')
    services.ingest(store,{'id':v},'favorites')
    client.patch('/api/videos/'+v,json={'state':'备选'})
    client.post('/api/discovery/'+v+'/feedback',json={'action':'viewed'})
    assert ids(feed(client,'creators'))=={v} and ids(feed(client))=={v}
    client.post('/api/discovery/'+v+'/feedback',json={'action':'dismiss'})
    assert ids(feed(client,'creators'))==set() and ids(feed(client))==set()


def test_focus_limits_deduplication_to_matching_titles_not_broad_topic_labels(client,store):
    for i,title in enumerate(['AI教程：搭建个人助手','AI教程：搭建个人助手 #实操','AI教程：数字人配音','AI教程：网页自动化']):
        seed(store,str(700000000+i),title=title)
    store.run("UPDATE videos SET topic_key='工具与新功能'")
    data=feed(client)
    assert data['total']==3 and data['merged']==1
    assert len(feed(client,exclude_ids=','.join(ids(data)))['items'])==1


def test_followed_new_work_does_not_require_low_follower_hit_but_focus_stays_strict(client,store):
    v=seed(store,source='creator',followers=800000,likes=10)
    assert ids(feed(client,'creators'))==set()
    r=client.post('/api/discovery/'+v+'/follow-author')
    assert r.status_code==200 and r.json()['network_requests']==0
    assert ids(feed(client,'creators'))=={v} and ids(feed(client))==set()
    assert client.post('/api/discovery/'+v+'/follow-author').json()['already_followed']
    data=feed(client,'creators')['items'][0]
    assert data['monitored_author'] and any('跟踪' in s for s in data['reference_reasons'])


def test_follow_rejects_missing_author_and_over_limit(client,store):
    v=seed(store,author_id=None)
    assert client.post('/api/discovery/'+v+'/follow-author').status_code==400
    for n in range(100):store.run('INSERT INTO creators(id,name,url) VALUES (?,?,?)',(f'TEST_{n}','合成','https://www.douyin.com/user/TEST_'+str(n)))
    v=seed(store,'222222222')
    assert client.post('/api/discovery/'+v+'/follow-author').json()['code']=='limit'


@pytest.mark.parametrize('kind',sorted(RETIRED_JOBS))
def test_retired_tasks_cannot_be_queued_or_executed_and_leave_drafts_untouched(client,store,kind):
    v=seed(store);p=services.create_project(store,v)
    before=services.project(store,p)
    assert client.post('/api/jobs',json={'kind':kind,'payload':{}}).json()['code']=='feature_retired'
    engine=JobEngine(store)
    with pytest.raises(TaskError) as error:engine.execute(kind,{},lambda *args:None)
    assert error.value.code=='feature_retired'
    with pytest.raises(TaskError):engine.enqueue(kind)
    assert services.project(store,p)==before


def test_knowledge_routes_and_weekly_dispatch_are_gone_but_notes_are_preserved(client,store,tmp_path):
    p=tmp_path/'old-note.md';p.write_text('用户原笔记',encoding='utf-8')
    store.set('knowledge',{**store.settings()['knowledge'],'enabled':True,'vault_path':str(tmp_path)})
    JobEngine(store).schedule_tick(datetime(2026,11,8,22,0,tzinfo=TZ))
    assert not store.rows("SELECT * FROM jobs WHERE kind LIKE 'knowledge_%'")
    assert client.get('/api/knowledge').status_code==404
    assert p.read_text(encoding='utf-8')=='用户原笔记'


def test_feedback_and_candidates_survive_normal_backup(client,store):
    v=seed(store);client.patch('/api/videos/'+v,json={'state':'备选'})
    client.post('/api/discovery/'+v+'/feedback',json={'action':'viewed'})
    name=services.backup(store)['name']
    store.run('DELETE FROM candidate_saves');store.run('DELETE FROM discovery_feedback')
    services.restore(store,name)
    assert store.one('SELECT saved_at FROM candidate_saves WHERE video_id=?',(v,))
    assert store.one('SELECT viewed_at FROM discovery_feedback WHERE video_id=?',(v,))['viewed_at']
