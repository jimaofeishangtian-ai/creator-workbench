from app import services
from app.db import now
from app.jobs import JobEngine


def seed(store, identity, source, **changes):
    values={'id':identity,'title':'AI教程工作流','author_id':'author-'+identity,'author':'作者','published_at':now(),'followers':5000,'likes':2000}
    values.update(changes)
    services.ingest(store,values,source,'source')


def ids(response):
    assert response.status_code==200
    return {row['id'] for row in response.json()['items']}


def test_recommendations_contain_qualifying_candidates_from_all_sources(client,store):
    seed(store,'111111111','creator')
    seed(store,'222222222','search')
    seed(store,'333333333','favorites')
    seed(store,'444444444','manual')
    seed(store,'555555555','search',likes=900)
    seed(store,'666666666','creator',followers=50001)
    seed(store,'777777777','search',followers=None)
    seed(store,'888888888','search',title='今天做饭技巧')
    for mode in ['recommended','hot','low','recent']:
        assert ids(client.get('/api/videos',params={'scope':'recommendations','mode':mode,'days':7}))=={'111111111','222222222','333333333','444444444'}
    assert ids(client.get('/api/videos'))=={'111111111','222222222','333333333','444444444'}
    assert ids(client.get('/api/videos',params={'scope':'personal'}))=={'333333333','444444444'}
    assert len(ids(client.get('/api/videos',params={'scope':'all'})))==8


def test_collected_recommendation_keeps_one_record_and_all_sources(client,store):
    seed(store,'111111111','creator')
    services.ingest(store,{'id':'111111111','title':'AI教程工作流'},'search','AI教程')
    assert ids(client.get('/api/videos'))=={'111111111'}
    services.ingest(store,{'id':'111111111','title':'AI教程工作流'},'favorites','my')
    assert ids(client.get('/api/videos'))=={'111111111'}
    assert ids(client.get('/api/videos',params={'scope':'personal'}))=={'111111111'}
    assert store.one('SELECT COUNT(*) n FROM videos')['n']==1
    assert len(services.detail(store,'111111111')['sources'])==3
    # Collected recommendations retain the same follower-recording route.
    result=client.post('/api/recommendations/record',json={'ids':['111111111']}).json()
    assert '111111111' in result['records'] and result['profile_refresh_job'] is None
    assert store.one('SELECT COUNT(*) n FROM recommendation_records')['n']==1


def test_saved_candidates_survive_ranking_expiry_without_mixing_sources(client,store):
    seed(store,'111111111','creator',published_at='2025-01-01T00:00:00+00:00',followers=None)
    seed(store,'222222222','favorites')
    seed(store,'333333333','search',likes=900)
    for identity in ('111111111','222222222'):
        assert client.patch('/api/videos/'+identity,json={'state':'备选'}).status_code==200
    params = {'mode':'recent','state':'备选'}
    assert ids(client.get('/api/videos',params={**params,'scope':'recommendations'}))=={'222222222'}
    assert ids(client.get('/api/videos',params={**params,'scope':'discovery'}))=={'111111111'}
    assert ids(client.get('/api/videos',params={**params,'scope':'personal'}))=={'222222222'}
    services.ingest(store,{'id':'111111111'},'favorites','my')
    assert ids(client.get('/api/videos',params={**params,'scope':'discovery'}))==set()
    assert ids(client.get('/api/videos',params={**params,'scope':'personal'}))=={'111111111','222222222'}


def test_personal_materials_keep_low_metrics_unknown_dates_and_non_ai(client,store):
    seed(store,'111111111','favorites',title='做饭技巧',likes=2,followers=None,published_at=None)
    seed(store,'222222222','manual',title='以后要学习的内容',likes=None,followers=None,published_at=None)
    assert ids(client.get('/api/videos',params={'scope':'personal','mode':'recent'}))=={'111111111','222222222'}
    assert ids(client.get('/api/videos'))==set()
    counts=client.get('/api/overview').json()['counts']
    assert counts['personal_materials']==2 and counts['discovery_materials']==0


def test_collection_shelves_filter_locally_and_keep_manual_corrections(client, store):
    seed(store, '111111111', 'favorites', title='Codex搭知识库实操教程')
    seed(store, '222222222', 'favorites', title='即梦运镜作品展示：如何制作同款')
    seed(store, '333333333', 'favorites', title='原创AI短片《月球》作品展示 #aigc #ai教程')
    seed(store, '444444444', 'manual', title='可灵AI生成了一段画面')
    seed(store, '555555555', 'favorites', title='周末去看海')
    seed(store, '666666666', 'search', title='即梦教程')
    for group, identity in [
        ('ai_tutorial', '111111111'), ('aigc_tutorial', '222222222'),
        ('aigc_example', '333333333'), ('review', '444444444'), ('other', '555555555'),
    ]:
        assert ids(client.get('/api/videos', params={'scope': 'personal', 'collection_group': group})) == {identity}
    assert client.patch('/api/videos/666666666', json={'collection_group': 'aigc_example'}).status_code == 400
    result = client.patch('/api/videos/444444444', json={'collection_group': 'aigc_example'}).json()
    assert result['collection_group_manual'] and result['collection_group'] == 'aigc_example'
    services.ingest(store, {'id': '444444444', 'title': '可灵制作教程'}, 'favorites')
    params = {'scope': 'personal', 'collection_group': 'aigc_example', 'limit': 1, 'offset': 1}
    assert len(ids(client.get('/api/videos', params=params))) == 1
    result = client.patch('/api/videos/444444444', json={'collection_group': ''}).json()
    assert not result['collection_group_manual'] and result['collection_group'] == 'aigc_tutorial'


def test_updating_external_metadata_never_moves_video_to_personal_library(store,monkeypatch):
    seed(store,'111111111','creator')
    monkeypatch.setattr('app.jobs.TikHub.detail',lambda *args:{'id':'111111111','title':'AI教程工作流','author_id':'author-111111111','likes':2000})
    monkeypatch.setattr('app.jobs.TikHub.profile',lambda *args:{'name':'作者','followers':5000})
    JobEngine(store).execute('video_refresh',{'video_id':'111111111'},lambda *args:None)
    assert [row['id'] for row in services.ranked_videos(store,scope='recommendations')]==['111111111']
    assert services.ranked_videos(store,scope='personal')==[]
    assert store.one("SELECT COUNT(*) n FROM sources WHERE kind='manual'")['n']==0


def test_recommendation_threshold_boundaries_and_combined_conditions(client,store):
    store.set('low_ratio',0.35)  # Preserve the earlier configurable-threshold boundary regression.
    assert store.settings()['low_ratio']==0.35
    seed(store,'111111111','creator',followers=50000,likes=17500)  # Both exact boundaries qualify.
    seed(store,'222222222','creator',followers=50001,likes=20000)  # Too many followers, despite strong likes.
    seed(store,'333333333','search',followers=2500,likes=999)      # Ratio >35%, likes still too few.
    seed(store,'444444444','search',followers=2500,likes=1000)    # Exact minimum likes qualifies.
    seed(store,'555555555','creator',followers=5000,likes=1749)   # Just below 35%.
    seed(store,'666666666','creator',followers=5000,likes=1750)   # Exact ratio qualifies.
    seed(store,'777777777','favorites',followers=1000,likes=1000)
    assert ids(client.get('/api/videos',params={'scope':'recommendations','days':7}))=={'111111111','444444444','666666666','777777777'}
    assert ids(client.get('/api/videos',params={'scope':'personal'}))=={'777777777'}


def test_recommendations_recheck_current_metrics_settings_and_favorite_source(client,store):
    seed(store,'111111111','creator',followers=5000,likes=1800)
    seed(store,'222222222','search',followers=5000,likes=1800)
    assert ids(client.get('/api/videos'))=={'111111111','222222222'}
    assert client.patch('/api/settings',json={'low_ratio':0.4}).status_code==200
    assert ids(client.get('/api/videos'))==set()
    assert client.patch('/api/settings',json={'low_ratio':0.35}).status_code==200
    store.run('UPDATE videos SET likes=999 WHERE id=?',('111111111',))
    assert ids(client.get('/api/videos'))=={'222222222'}
    services.ingest(store,{'id':'222222222'},'favorites','my')
    assert ids(client.get('/api/videos'))=={'222222222'}
    assert ids(client.get('/api/videos',params={'scope':'personal'}))=={'222222222'}


def test_shell_is_revalidated_instead_of_reusing_old_frontend(client):
    for path in ['/', '/index.html', '/sw.js', '/manifest.webmanifest']:
        response=client.get(path)
        assert response.status_code==200
        assert response.headers['cache-control']=='no-cache'


def test_current_25_percent_default_and_72_hour_window(client,store):
    from datetime import datetime,timedelta,timezone
    assert store.settings()['low_ratio']==0.25
    seed(store,'111111111','creator',followers=6000,likes=1500) # Exact 25%.
    seed(store,'222222222','search',followers=6000,likes=1499)
    seed(store,'333333333','creator',followers=4000,likes=1000) # Both exact minimum likes and ratio.
    seed(store,'444444444','search',followers=3000,likes=999) # Ratio passes; likes fail.
    seed(store,'555555555','creator',followers=4000,likes=2000,published_at=(datetime.now(timezone.utc)-timedelta(hours=73)).isoformat())
    seed(store,'666666666','favorites',followers=4000,likes=2000)
    assert ids(client.get('/api/videos',params={'scope':'recommendations','days':3}))=={'111111111','333333333','666666666'}
