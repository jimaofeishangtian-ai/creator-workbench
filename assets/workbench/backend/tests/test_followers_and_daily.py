from datetime import datetime, timedelta, timezone

from app import services
from app.db import Store, now
from app.jobs import JobEngine, TZ


def video(store, identity, author="author-one"):
    services.ingest(store, {"id": identity, "title": "AI工具教程", "author": "同名作者", "author_id": author}, "creator", author)


def test_follower_recommendations_compare_authors_across_videos_and_devices(client, store, monkeypatch):
    video(store, "123456789")
    video(store, "123456790")
    first = "2026-11-01T00:00:00+00:00"
    second = "2026-11-08T00:00:00+00:00"
    services.record_author_followers(store, "author-one", "作者", 5000, first)
    a = client.post('/api/recommendations/record', json={'ids':['123456789', '123456790']}).json()['records']
    assert a['123456789']['delta'] is None
    assert a['123456790'] == a['123456789']
    assert store.one('SELECT COUNT(*) n FROM recommendation_records')['n'] == 1
    # New observed count, another video by the same author, then repeat on a second device.
    services.record_author_followers(store, "author-one", "作者改名", 10000, second)
    b = client.post('/api/recommendations/record', json={'ids':['123456790']}).json()['records']['123456790']
    assert b['delta'] == 5000 and b['previous']['followers'] == 5000
    repeated = client.post('/api/recommendations/record', json={'ids':['123456789']}).json()['records']['123456789']
    assert repeated == b
    assert store.one('SELECT COUNT(*) n FROM recommendation_records')['n'] == 2
    detail = client.get('/api/videos/123456789').json()
    assert [h['followers'] for h in detail['follower_history']] == [10000, 5000]
    assert detail['followers_at'] == second
    assert len(detail['recommendation_history']) == 2
    # Browsing/recording must not create paid calls.
    assert store.one('SELECT COUNT(*) n FROM expenses')['n'] == 0


def test_missing_counts_and_stale_samples_do_not_fabricate_growth(store):
    video(store, "123456789")
    unknown = services.record_recommendations(store, ['123456789'])['123456789']
    assert unknown['delta'] is None
    first = "2026-11-01T00:00:00+00:00"
    second = "2026-11-08T00:00:00+00:00"
    services.record_author_followers(store, "author-one", "作者", 10000, second)
    services.record_author_followers(store, "author-one", "作者", 5000, first)
    services.record_author_followers(store, "author-one", "作者", None, now())
    services.record_author_followers(store, "author-one", "作者", True, now())
    current = store.one('SELECT followers,followers_at FROM videos')
    assert current == {'followers':10000, 'followers_at':second}
    result = services.record_recommendations(store, ['123456789'])['123456789']
    assert result['delta'] is None and result['previous']['followers'] is None
    # Same nickname, different stable author ID must never share a baseline.
    video(store, '123456790', author='another-author')
    services.record_author_followers(store, 'another-author', '同名作者', 5000, second)
    assert services.record_recommendations(store, ['123456790'])['123456790']['previous'] is None


def test_follower_migration_is_idempotent_and_never_uses_publication_time(store):
    video(store, '123456789')
    store.run("UPDATE videos SET followers=5000,published_at=?,followers_at=NULL", (now(),))
    Store(store.root)
    assert store.one('SELECT COUNT(*) n FROM author_follower_history')['n'] == 0
    store.run('UPDATE videos SET followers_at=?', (now(),))
    Store(store.root)
    Store(store.root)
    assert store.one('SELECT COUNT(*) n FROM author_follower_history')['n'] == 1


def test_daily_sync_respects_elapsed_24_hours_and_manual_sync(client, store, monkeypatch):
    automation = store.settings()['automation']
    assert automation['favorites_minutes'] == 1440
    automation['enabled'] = True
    assert client.patch('/api/settings', json={'automation':automation}).status_code == 200
    store.set('collection_state', {'status':'authenticated'})
    current = datetime.now(TZ)
    previous = current - timedelta(hours=23, minutes=59)
    store.set('last_sync', {'favorites':previous.isoformat()})
    engine = JobEngine(store)
    engine.schedule_tick(current)
    assert store.one("SELECT COUNT(*) n FROM jobs WHERE kind='favorites_sync'")['n'] == 0
    monkeypatch.setattr('app.jobs.now', lambda: (current + timedelta(minutes=2)).isoformat())
    engine.schedule_tick(current + timedelta(minutes=2))
    assert store.one("SELECT COUNT(*) n FROM jobs WHERE kind='favorites_sync'")['n'] == 1
    engine.schedule_tick(current + timedelta(minutes=3))
    assert store.one("SELECT COUNT(*) n FROM jobs WHERE kind='favorites_sync'")['n'] == 1
    store.run("UPDATE jobs SET status='succeeded' WHERE kind='favorites_sync'")
    manual_at = current + timedelta(hours=12)
    store.run("INSERT INTO jobs (id,kind,payload,status,created_at,updated_at) VALUES ('manual','favorites_sync','{}','succeeded',?,?)", (manual_at.isoformat(), manual_at.isoformat()))
    engine.schedule_tick(current + timedelta(hours=25))
    assert store.one("SELECT COUNT(*) n FROM jobs WHERE kind='favorites_sync'")['n'] == 2
    # Manual action is available before the next automatic interval.
    assert client.post('/api/jobs', json={'kind':'favorites_sync','payload':{'foreground':True}}).status_code == 200
    assert client.post('/api/jobs', json={'kind':'favorites_sync','payload':{'foreground':'yes'}}).status_code == 400


def test_cost_overview_separates_providers_and_uncertain_reservations(client, store):
    for identity, provider, amount, actual, status in [('a','data',1.97,1.97,'estimated'), ('b','model',0.05,None,'reserved'), ('c','model',0.13,0.13,'estimated')]:
        store.run('INSERT INTO expenses VALUES (?,?,?,?,?,?,?,?,?)', (identity,datetime.now(TZ).strftime('%Y-%m'),provider,'test',amount,actual,status,None,now()))
    rows = {r['provider']:r for r in client.get('/api/overview').json()['expenses']}
    assert rows['data']['used'] == 1.97 and rows['data']['pending'] == 0
    assert round(rows['model']['used'],2) == 0.18 and rows['model']['pending'] == 0.05
    assert rows['model']['calls'] == 2


def test_recommended_profile_cache_and_failures_are_bounded(store, monkeypatch):
    video(store, '123456789')
    video(store, '123456790')
    assert services.recommended_authors_due(store, ['123456789','123456790']) == ['author-one']
    calls = []
    def profile(_, author):
        calls.append(author)
        return {'name':'作者','followers':5000}
    monkeypatch.setattr('app.jobs.TikHub.profile', profile)
    engine = JobEngine(store)
    engine.execute('author_profiles_refresh', {'author_ids':['author-one']}, lambda *args:None)
    engine.execute('author_profiles_refresh', {'author_ids':['author-one']}, lambda *args:None)
    assert calls == ['author-one']
    assert services.recommended_authors_due(store, ['123456789']) == []
    assert services.detail(store,'123456789')['followers'] == 5000
    video(store, '123456791', 'missing-author')
    monkeypatch.setattr('app.jobs.TikHub.profile', lambda *args:{'name':'未知','followers':None})
    result = engine.execute('author_profiles_refresh', {'author_ids':['missing-author']}, lambda *args:None)
    assert result['failed_creators'][0]['code'] == 'missing_followers'
    assert services.recommended_authors_due(store, ['123456791']) == []
    assert store.one("SELECT followers FROM videos WHERE id='123456791'")['followers'] is None


def test_hot_feed_shows_previous_recommended_follower_sample_without_paid_refresh(client,store):
    first=(datetime.now(timezone.utc)-timedelta(days=7)).isoformat()
    second=now()
    services.ingest(store,{'id':'123456789','title':'AI工具教程','author_id':'author-one','published_at':second,'likes':120000},'hotspot','1:AI工具')
    services.record_author_followers(store,'author-one','作者',5000,first)
    services.record_recommendations(store,['123456789'])
    services.record_author_followers(store,'author-one','作者',10000,second)
    before=store.one('SELECT COUNT(*) n FROM recommendation_records')['n']
    row=client.get('/api/discovery',params={'mode':'hot'}).json()['items'][0]
    observation=row['follower_observation']
    assert observation['basis']=='recommendation' and observation['delta']==5000
    assert observation['days']==7
    assert observation['previous']['observed_at']==first and observation['current']['observed_at']==second
    # Viewing a feed never writes a pretend historical recommendation or requests profiles.
    assert store.one('SELECT COUNT(*) n FROM recommendation_records')['n']==before
    assert not store.rows('SELECT * FROM jobs') and not store.rows('SELECT * FROM expenses')


def test_read_only_recommendation_recording_suppresses_optional_profile_jobs(client,store,monkeypatch):
    video(store,'123456789')
    monkeypatch.setattr('app.main.get_secret',lambda *a:'synthetic-only')
    prices=store.settings()['pricing'];prices['confirmed']=True;store.set('pricing',prices);store.set('data_pricing_confirmed',True)
    response=client.post('/api/recommendations/record',json={'ids':['123456789'],'refresh_profiles':False}).json()
    assert '123456789' in response['records'] and response['profile_refresh_job'] is None
    local=client.post('/api/recommendations/record-local',json={'ids':['123456789'],'refresh_profiles':True}).json()
    assert local['profile_refresh_job'] is None
    assert not store.rows('SELECT * FROM jobs') and not store.rows('SELECT * FROM expenses')
    # Existing low-follower callers retain their bounded refresh behavior.
    response=client.post('/api/recommendations/record',json={'ids':['123456789']}).json()
    assert response['profile_refresh_job'] is not None


def test_repeated_manual_search_reuses_recent_success_but_new_keywords_run(client,store):
    import json
    first=client.post('/api/jobs',json={'kind':'search','payload':{}}).json()['job_id']
    result={'completed_keywords':1,'searched_keywords':store.settings()['keywords']}
    store.run("UPDATE jobs SET status='succeeded',result=?,updated_at=? WHERE id=?",(json.dumps(result),now(),first))
    repeated=client.post('/api/jobs',json={'kind':'search','payload':{}}).json()
    assert repeated['job_id']==first and repeated['cached']
    assert store.one("SELECT COUNT(*) n FROM jobs WHERE kind='search'")['n']==1
    client.patch('/api/settings',json={'keywords':['新的AI教程词']})
    changed=client.post('/api/jobs',json={'kind':'search','payload':{}}).json()
    assert changed['job_id']!=first and not changed.get('cached')


def test_follower_observation_uses_real_sampling_fallback_and_handles_drops(store):
    video(store,'123456789')
    older=(datetime.now(timezone.utc)-timedelta(days=4)).isoformat()
    current=now()
    services.record_author_followers(store,'author-one','作者',4800,current)
    services.record_author_followers(store,'author-one','作者',5000,older)
    row=store.one("SELECT * FROM videos WHERE id='123456789'")
    observation=services.follower_observation(store,row)
    assert observation['basis']=='sample' and observation['delta']==-200
    assert observation['previous']['observed_at']==older
    services.record_recommendations(store,['123456789'])
    assert services.follower_observation(store,row)==observation
    assert services.follower_observation(store,{**row,'followers':None})['delta'] is None
    assert services.follower_observation(store,{**row,'followers_at':None})['delta'] is None
    assert services.follower_observation(store,{**row,'author_id':'different-author'})['delta'] is None


def test_recommendation_history_survives_backup_and_export(client, store):
    video(store, '123456789')
    services.record_author_followers(store,'author-one','作者',5000,now())
    services.record_recommendations(store,['123456789'])
    saved = services.backup(store)
    store.run('DELETE FROM recommendation_records')
    services.restore(store,saved['name'])
    assert len(services.detail(store,'123456789')['recommendation_history']) == 1
    # Restore revokes sessions; use a fresh normal login before verifying export.
    result = client.post('/api/auth/login',json={'password':'test-password-12345'})
    client.headers['X-CSRF-Token'] = result.json()['csrf']
    exported = client.get('/api/export').json()
    assert len(exported['recommendation_records']) == 1
    assert len(exported['author_follower_history']) == 1
