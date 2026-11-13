from datetime import datetime,timezone
import pytest
from app import services
from app.onboarding import apply_profile,validate_profile,validate_following,select_following
from app.providers import TaskError
from app.db import now

PROFILE={'domain':'居家健身','audience':'无器械训练新手','goal':'可复现动作选题','formats':['动作讲解'],'include_keywords':['练腿','居家训练'],'search_keywords':['居家练腿'],'exclude_keywords':['代餐'],'prefer_low_followers':False}


def test_general_domain_accepts_non_ai_content_including_qualified_favorites(client,store):
    changes=apply_profile(store,PROFILE)
    assert changes['keywords']==['居家练腿']
    for identity,title,kind in [('111111111','居家训练练腿实操','search'),('222222222','AI新工具教程','creator'),('333333333','居家训练代餐广告','search'),('444444444','居家训练练腿收藏','favorites')]:
        services.ingest(store,{'id':identity,'title':title,'published_at':now(),'likes':2000,'followers':5000,'followers_at':now()},kind)
    result=client.get('/api/videos',params={'scope':'recommendations','days':3,'ai_only':True}).json()['items']
    assert {r['id'] for r in result}=={'111111111','444444444'}
    assert not result[0]['is_ai']
    assert result[0]['domain_matches']==['练腿','居家训练']
    assert len(client.get('/api/videos',params={'scope':'personal'}).json()['items'])==1
    assert store.settings()['automation']['enabled'] is False
    assert store.one('SELECT COUNT(*) n FROM expenses')['n']==0


def test_unconfigured_domain_gives_no_recommendations(store):
    store.set('creator_domain',{'configured':False})
    services.ingest(store,{'id':'111111111','title':'AI教程','published_at':now(),'likes':2000,'followers':1000,'followers_at':now()},'search')
    assert services.ranked_videos(store,scope='recommendations')==[]


def test_invalid_interview_does_not_half_write(store):
    before=store.settings()
    with pytest.raises(TaskError):apply_profile(store,{**PROFILE,'search_keywords':[]})
    assert store.settings()==before


def following():
    return {'captured_at':datetime.now(timezone.utc).isoformat(),'page_url':'https://www.douyin.com/user/self','status':'partial','items':[{'name':'训练博主','url':'https://www.douyin.com/user/FAKE_TEST_ONE','evidence':'本人关注页可见'}, {'name':'生活博主','url':'https://www.douyin.com/user/FAKE_TEST_TWO','evidence':'本人关注页可见'}]}


def test_following_import_enables_only_selected_candidates(store):
    payload=following()
    assert len(validate_following(payload)['items'])==2
    assert store.one('SELECT COUNT(*) n FROM creators')['n']==0
    result=select_following(store,payload,['FAKE_TEST_ONE'])
    assert result['selected']==1 and result['read_status']=='partial'
    assert [r['id'] for r in store.rows('SELECT id FROM creators WHERE enabled=1')]==['FAKE_TEST_ONE']
    with pytest.raises(TaskError):select_following(store,payload,['NOT_IN_CANDIDATES'])
    assert store.one('SELECT COUNT(*) n FROM creators')['n']==1


@pytest.mark.parametrize('url',['https://evil.example/user/FAKE_TEST_ONE','https://www.douyin.com/user/FAKE_TEST_ONE?cookie=bad','https://www.douyin.com/user/self'])
def test_following_rejects_offsite_urls_and_credentials(url):
    payload=following();payload['items'][0]['url']=url
    with pytest.raises(TaskError):validate_following(payload)


def test_domain_ui_settings_roundtrip(client):
    domain=validate_profile(PROFILE)['creator_domain']
    assert client.patch('/api/settings',json={'creator_domain':domain}).status_code==200
    assert client.get('/api/settings').json()['creator_domain']==domain


def test_fresh_install_is_neutral_and_paid_automation_is_off(app):
    s=app.state.store.settings()
    assert s['creator_domain']['configured'] is False and s['keywords']==[]
    assert s['automation']['enabled'] is False and s['hotspots']['enabled'] is False
    assert s['traffic_tags']==[] and app.state.store.one('SELECT count(*) n FROM secrets')['n']==0
