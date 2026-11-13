import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app import services
from app.db import Store, now
from app.jobs import JobEngine
from app.providers import Budget, DeepSeek, TaskError, extract_videos, normalize_video
from app.security import get_secret, save_secret, password_hash, verify_password


def material(store, identity="123456789", **extra):
    video = {"id": identity, "title": "Codex搭建可验证的AI知识库", "url": "https://www.douyin.com/video/" + identity, "published_at": now(), **extra}
    services.ingest(store, video)
    return identity


def confirm_prices(store):
    p = store.settings()["pricing"]
    p["confirmed"] = True
    store.set("pricing", p)
    store.set("data_pricing_confirmed", True)


def test_auth_requires_setup_code_and_csrf(app):
    with TestClient(app) as c:
        assert c.get("/api/settings").status_code == 401
        assert c.post("/api/auth/setup", json={"password": "long-password", "setup_token": "bad"}).status_code == 400
        code = (app.state.store.root / "initial-setup-code.txt").read_text()
        r = c.post("/api/auth/setup", json={"password": "long-password", "setup_token": code})
        assert r.status_code == 200
        assert c.get("/api/settings").status_code == 200
        assert c.patch("/api/settings", json={"profile": "changed"}).status_code == 403
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
        assert c.patch("/api/settings", json={"profile": "changed"}).status_code == 200
        assert c.patch("/api/settings", json={"profile": "evil"}, headers={"Origin": "https://evil.example"}).status_code == 403
        assert c.get("/api/health", headers={"Host": "evil.example"}).status_code == 403


def test_password_and_secret_never_returned(client, store):
    assert verify_password("abc12345678", password_hash("abc12345678"))
    assert not verify_password("incorrect", password_hash("abc12345678"))
    save_secret(store, "deepseek", "test-key-not-a-real-credential")
    assert get_secret(store, "deepseek") == "test-key-not-a-real-credential"
    assert "test-key-not-a-real-credential" not in client.get("/api/settings").text
    assert b"test-key-not-a-real-credential" not in store.path.read_bytes()


def test_normalization_preserves_unknown_and_search_nesting():
    raw = {"aweme_id": "123456789", "desc": "标题", "author": {"nickname": "作者"}, "video": {"duration": 97000}, "statistics": {"digg_count": 0}}
    v = normalize_video(raw)
    assert v["duration"] == 97
    assert v["likes"] == 0 and v["followers"] is None and v["favorites"] is None
    assert v["collected_at"] is None
    assert extract_videos({"data": {"business_data": [{"data": {"aweme_info": raw}}]}}) == [raw]
    assert extract_videos({"data": {"aweme_details": [raw]}}) == [raw]


def test_unknown_publication_time_never_qualifies_as_today(store):
    identity=material(store)
    store.run('UPDATE videos SET published_at=NULL WHERE id=?',(identity,))
    assert services.ranked_videos(store,days=1)==[]
    assert [row['id'] for row in services.ranked_videos(store,days=0)]==[identity]


def test_embedded_followers_never_overwrite_a_verified_profile(store):
    identity=material(store,followers=20264)
    raw={"aweme_id":identity,"author":{"nickname":"作者","follower_count":0}}
    services.ingest(store,normalize_video(raw))
    assert store.one('SELECT followers FROM videos WHERE id=?',(identity,))['followers']==20264
    raw['author']['follower_count']=14
    assert normalize_video(raw)['followers'] is None


def test_image_posts_keep_note_urls_and_can_be_imported_by_link(monkeypatch):
    from app.providers import parse_douyin_url
    monkeypatch.setattr('app.providers.approved_public_url',lambda url,**kwargs:url)
    raw={'aweme_id':'123456789','images':[{'url_list':['https://p.douyinpic.com/a.jpg']}]}
    assert normalize_video(raw)['url']=='https://www.douyin.com/note/123456789'
    assert parse_douyin_url('https://www.douyin.com/note/123456789')==('video','123456789','https://www.douyin.com/note/123456789')


def test_douyin_delivery_hosts_remain_scoped_and_must_resolve_publicly(monkeypatch):
    from app.providers import approved_public_url
    import socket
    monkeypatch.setattr(socket,'getaddrinfo',lambda *args:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443))])
    url='https://v5-dy-ov-experiment.zjcdn.com/media.mp4'
    assert approved_public_url(url)==url
    for blocked in [url.replace('v5-dy-ov-experiment','unrelated'),url.replace('.zjcdn.com','.zjcdn.com.evil.example')]:
        with pytest.raises(TaskError):approved_public_url(blocked)
    with pytest.raises(TaskError):approved_public_url(url,share=True)
    monkeypatch.setattr(socket,'getaddrinfo',lambda *args:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))])
    with pytest.raises(TaskError):approved_public_url(url)


def test_three_sources_deduplicate_and_manual_labels_survive(client, store):
    identity = material(store)
    assert not services.ingest(store, {"id": identity, "title": "Codex新标题"}, "favorites", "my")
    assert client.patch("/api/videos/" + identity, json={"category": "资讯", "tools": ["人工标签"], "is_ai": False}).status_code == 200
    services.ingest(store, {"id": identity, "title": "AI剪辑教程"}, "creator", "author")
    v = services.detail(store, identity)
    assert v["category"] == "资讯" and v["tools"] == ["人工标签"] and not v["is_ai"]
    assert len(v["sources"]) == 3
    assert store.one("SELECT COUNT(*) n FROM videos")["n"] == 1


def test_transcript_search_and_auto_does_not_overwrite_manual(client, store):
    identity = material(store)
    services.save_transcript(store, identity, "人工校对的稀有词：黄鹂知识库")
    assert services.save_transcript(store, identity, "自动转写新文本", source="Whisper", overwrite=False) == 0
    assert "黄鹂" in services.detail(store, identity)["transcript"]["text"]
    results = client.get("/api/videos", params={"q": "黄鹂", "scope": "all"}).json()
    assert results["total"] == 1


def test_low_followers_and_growth_need_real_samples(store):
    store.set("low_ratio",0.2) # This test isolates sampling/growth from the configurable ratio.
    identity = material(store, likes=2000, followers=None)
    assert services.ranked_videos(store, mode="low") == []
    store.run("UPDATE videos SET followers=10000 WHERE id=?", (identity,))
    assert services.ranked_videos(store, mode="low") == []
    stale = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    store.run("UPDATE videos SET followers_at=? WHERE id=?", (stale, identity))
    assert services.ranked_videos(store, mode="low") == []
    store.run("UPDATE videos SET followers_at=? WHERE id=?", (now(), identity))
    row = services.ranked_videos(store, mode="low")[0]
    assert row["growth_per_hour"] is None
    store.run("UPDATE snapshots SET sampled_at=? WHERE video_id=?", ((datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(), identity))
    services.ingest(store, {"id": identity, "title": "AI教程", "likes": 2200, "followers": 10000})
    assert 95 < services.ranked_videos(store)[0]["growth_per_hour"] < 105


def test_project_revision_conflict_and_history_arrays(client, store):
    identity = material(store)
    p = services.create_project(store, identity)
    services.update_project(store, p, 1, {"hooks": [{"text": "开头"}], "shooting_plan": [{"screen": "录屏"}], "verification": ["亲测"], "content": "初版"})
    r = client.patch("/api/projects/" + p, json={"revision": 2, "changes": {"content": "电脑修改"}})
    assert r.status_code == 200
    assert client.patch("/api/projects/" + p, json={"revision": 2, "changes": {"content": "手机旧稿"}}).status_code == 409
    r = client.post("/api/projects/" + p + "/restore", json={"revision": 3, "version": 2})
    assert r.status_code == 200
    restored = client.get("/api/projects/" + p).json()
    assert isinstance(restored["hooks"], list) and restored["hooks"][0]["text"] == "开头"
    assert isinstance(restored["shooting_plan"], list) and restored["content"] == "初版"


def test_budget_reservation_is_atomic_and_timeout_counts(store):
    confirm_prices(store)
    barrier = threading.Barrier(5)
    def reserve(_):
        barrier.wait()
        try:
            return Budget(store).reserve("data", "test", 50)
        except TaskError as e:
            return e.code
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(reserve, range(5)))
    assert results.count("budget_exceeded") == 1
    reservation = next(x for x in results if x != "budget_exceeded")
    Budget(store).settle(reservation, status="unknown")
    with pytest.raises(TaskError, match="预算"):
        Budget(store).reserve("data", "test", 0.01)


def test_unconfirmed_prices_block_spending(store):
    with pytest.raises(TaskError) as e:
        Budget(store).reserve("model", "test", 0.01)
    assert e.value.code == "pricing_required"
    assert store.one("SELECT COUNT(*) n FROM expenses")["n"] == 0


def test_restore_keeps_expenses_and_login_identity(client, store):
    confirm_prices(store)
    identity = material(store)
    old_backup = services.backup(store)
    charge = Budget(store).reserve("data", "after-backup", 199.99)
    Budget(store).settle(charge, status="unknown")
    store.run("UPDATE videos SET title='备份后修改' WHERE id=?", (identity,))
    result = services.restore(store, old_backup["name"])
    assert result["login_required"]
    assert store.one("SELECT title FROM videos WHERE id=?", (identity,))["title"] != "备份后修改"
    assert store.one("SELECT status FROM expenses WHERE id=?", (charge,))["status"] == "unknown"
    assert store.one("SELECT id FROM auth")
    with pytest.raises(TaskError):
        Budget(store).reserve("data", "test", 1)
    assert client.get("/api/settings").status_code == 401


def test_backup_rejects_path_traversal(store):
    with pytest.raises(TaskError):
        services.restore(store, "../../outside.sqlite3")


def test_scheduler_dedup_and_catches_up_after_restart(store, monkeypatch):
    monkeypatch.setattr("app.jobs.get_secret", lambda *args: "test")
    confirm_prices(store)
    automation = store.settings()["automation"]
    automation["enabled"] = True
    store.set("automation", automation)
    store.set("collection_state", {"status": "collection_failed", "authenticated_once": True})
    engine = JobEngine(store)
    dt = datetime(2026, 11, 1, 19, 0, tzinfo=timezone(timedelta(hours=8)))
    engine.schedule_tick(dt)
    count = store.one("SELECT COUNT(*) n FROM jobs")["n"]
    engine.schedule_tick(dt)
    assert count == store.one("SELECT COUNT(*) n FROM jobs")["n"]
    assert store.one("SELECT id FROM jobs WHERE kind='favorites_sync'")
    assert store.one("SELECT id FROM jobs WHERE kind='search'")
    assert not store.one("SELECT id FROM jobs WHERE kind='creators_sync'")


def test_search_does_not_retry_http_400_and_reports_partial_results(store,monkeypatch):
    import time
    from app.providers import TikHub
    calls=[]
    def search(self,keyword):
        calls.append(keyword)
        if keyword=='失败项':
            raise TaskError('provider_error','接口暂时不可用',http_status=400)
        return {'data':{'aweme_list':[{'aweme_id':'123456789','desc':'Codex教程','create_time':int(datetime.now(timezone.utc).timestamp())}]}}
    monkeypatch.setattr(TikHub,'search',search)
    engine=JobEngine(store)
    monkeypatch.setattr(engine,'classify_pending',lambda:None)
    jid=engine.enqueue('search',{'keywords':['失败项','Codex']})
    engine.start()
    try:
        deadline=time.monotonic()+6
        while time.monotonic()<deadline:
            row=store.one('SELECT status,result FROM jobs WHERE id=?',(jid,))
            if row['status'] not in {'queued','running'}:break
            time.sleep(.02)
        assert row['status']=='attention'
        result=json.loads(row['result'])
        assert result['completed_keywords']==1 and result['added']==1
        assert result['failed_keywords'][0]['keyword']=='失败项'
        assert calls==['失败项','Codex']
        assert result['request_attempts']==2 and result['retry_attempts']==0
        assert result['failed_keywords'][0]['http_status']==400
        assert result['failed_keywords'][0]['attempts']==1
        assert '请求2次，其中自动重试0次' in store.one('SELECT message FROM jobs WHERE id=?',(jid,))['message']
    finally:
        engine.close()
        engine.worker.join(timeout=3)


def test_search_never_retries_a_balance_failure(store,monkeypatch):
    from app.providers import TikHub
    calls=[]
    def search(self,keyword):
        calls.append(keyword)
        raise TaskError('balance_required','余额不足')
    monkeypatch.setattr(TikHub,'search',search)
    with pytest.raises(TaskError):JobEngine(store).execute('search',{'keywords':['Codex','Claude']},lambda *args:None)
    assert calls==['Codex']


def test_data_retry_is_bounded_and_does_not_multiply_search_retries(store,monkeypatch):
    from app.providers import TikHub
    calls=[]
    def once(self,path,params=None,body=None):
        calls.append(path)
        raise TaskError('provider_error','暂时不可用',http_status=400)
    monkeypatch.setattr(TikHub,'_request_once',once)
    monkeypatch.setattr('app.providers.time.sleep',lambda seconds:None)
    with pytest.raises(TaskError):TikHub(store).profile('test-user')
    assert len(calls)==1  # Rejected ordinary queries must not silently double billing.
    calls.clear()
    with pytest.raises(TaskError):TikHub(store).search('Codex')
    assert len(calls)==1  # The job owns the single retry for search.


def test_one_creator_failure_does_not_block_the_remaining_creators(store,monkeypatch):
    from app.providers import TikHub
    for cid in ['bad-user','good-user']:
        store.run('INSERT INTO creators VALUES (?,?,?,?,?,?)',(cid,cid,'https://www.douyin.com/user/'+cid,10000,1,now()))
    def posts(self,cid,cursor=0):
        if cid=='bad-user':raise TaskError('provider_error','暂时不可用',http_status=400)
        return {'data':{'aweme_list':[{'aweme_id':'987654321','desc':'AI新教程','create_time':int(datetime.now(timezone.utc).timestamp())}],'has_more':False}}
    monkeypatch.setattr(TikHub,'posts',posts)
    result=JobEngine(store).execute('creators_sync',{},lambda *args:None)
    assert result['creators']==2 and result['completed_creators']==1
    assert result['failed_creators'][0]['id']=='bad-user'
    assert result['scanned']==1 and result['added']==1
    assert store.one("SELECT author_id FROM videos WHERE id='987654321'")['author_id']=='good-user'


def test_hooks_then_script_and_paid_conflict_result_preserved(store, monkeypatch):
    identity = material(store)
    services.save_transcript(store, identity, "原始演示：打开Codex，选择自己的文件夹，先验证结果再讲步骤。")
    p = services.create_project(store, identity)
    def fake(self, purpose, source, instruction, max_tokens=4000):
        if purpose == "hooks":
            return {"hooks": [{"text": "这是第一个开头"}, {"text": "这是第二个开头"}, {"text": "这是第三个开头"}]}
        return {"content": "正文", "shooting_plan": [{"segment": "第一段", "speech": "说明结果", "screen": "展示真实界面", "evidence": "实际结果"}], "verification": ["核实工具版本"]}
    monkeypatch.setattr(DeepSeek, "json", fake)
    assert services.generate_project(store, p, "hooks", 1)["revision"] == 2
    with pytest.raises(TaskError) as e:
        services.generate_project(store, p, "script", 2)
    assert e.value.code == "hook_required"
    services.update_project(store, p, 2, {"selected_hook": "自定义开头", "stage": "写正文"})
    result = services.generate_project(store, p, "script", 3)
    assert result["revision"] == 4
    assert services.project(store, p)["content"].startswith("自定义开头")
    def concurrent(self, *args, **kwargs):
        services.update_project(store, p, 4, {"content": "人工正在改稿"})
        return {"content": "AI新稿", "shooting_plan": [], "verification": []}
    monkeypatch.setattr(DeepSeek, "json", concurrent)
    r = services.generate_project(store, p, "script", 4)
    assert r["conflict"] and "AI新稿" in r["generated"]["content"]
    assert services.project(store, p)["content"] == "人工正在改稿"


@pytest.mark.skip(reason="已下线的知识库/制作行为；由 test_discovery 的停用与数据保留检查替代")
def test_cancelled_generation_does_not_apply(store, monkeypatch):
    identity = material(store)
    services.save_transcript(store, identity, "参考逐字稿")
    p = services.create_project(store, identity)
    engine = JobEngine(store)
    job = engine.enqueue("hooks", {"project_id": p, "revision": 1})
    def cancel(self, *args, **kwargs):
        store.run("UPDATE jobs SET status='cancelled' WHERE id=?", (job,))
        return {"hooks": [{"text": "1"}, {"text": "2"}, {"text": "3"}]}
    monkeypatch.setattr(DeepSeek, "json", cancel)
    result = services.generate_project(store, p, "hooks", 1, job_id=job)
    assert result["cancelled"] and result["generated"]["hooks"]
    assert services.project(store, p)["revision"] == 1


def test_setting_validation_and_backup_confirmation(client):
    assert client.patch("/api/settings", json={"automation": {"enabled": True}}).status_code == 400
    assert client.patch("/api/settings", json={"low_followers": -2}).status_code == 400
    assert client.patch("/api/settings", json={"remote_access": {"url": "http://evil.example", "allowed_device": "phone"}}).status_code == 400
    assert client.post("/api/backups/restore", json={"name": "x"}).status_code == 400


def test_private_urls_rejected_before_network(client):
    for url in ["https://127.0.0.1/private", "http://www.douyin.com/video/123456789", "https://www.douyin.com.evil.example/video/123456789", "https://username:password@www.douyin.com/video/123456789"]:
        assert client.post("/api/import", json={"url": url}).status_code == 400


def test_import_link_without_provider_does_not_fake_metadata(client, monkeypatch):
    monkeypatch.setattr("app.providers.socket.getaddrinfo", lambda *a: [(2, 1, 6, '', ('8.8.8.8', 443))])
    r = client.post("/api/import", json={"url": "https://www.douyin.com/video/123456789", "title": "手动AI教程"})
    assert r.status_code == 200 and r.json()["job_id"] is None
    video = client.get("/api/videos/123456789").json()
    assert video["likes"] is None and video["followers"] is None
    assert video["collected_at"] is None


def test_tikhub_http_200_empty_is_still_charged(store, monkeypatch):
    from app.providers import TikHub
    import httpx
    confirm_prices(store)
    monkeypatch.setattr("app.providers.get_secret", lambda *a: "test-key")
    fake = Mock()
    fake.__enter__ = Mock(return_value=fake)
    fake.__exit__ = Mock(return_value=False)
    fake.request.return_value = httpx.Response(200, json={"code": 200, "data": {"filter_list": [{"reason": 5}]}})
    monkeypatch.setattr("app.providers.httpx.Client", lambda **kwargs: fake)
    with pytest.raises(TaskError) as e:
        TikHub(store).detail("123456789")
    assert e.value.code == "unavailable"
    assert store.one("SELECT actual FROM expenses")["actual"] > 0


def test_tikhub_insufficient_balance_is_actionable_and_releases_reservation(store,monkeypatch):
    from app.providers import TikHub
    import httpx
    confirm_prices(store)
    monkeypatch.setattr('app.providers.get_secret',lambda *a:'test-key')
    fake=Mock()
    fake.__enter__=Mock(return_value=fake)
    fake.__exit__=Mock(return_value=False)
    fake.request.return_value=httpx.Response(402,json={'code':402})
    monkeypatch.setattr('app.providers.httpx.Client',lambda **kwargs:fake)
    with pytest.raises(TaskError) as error:
        TikHub(store).search('Codex')
    assert error.value.code=='balance_required'
    assert store.one('SELECT status,actual FROM expenses')=={'status':'released','actual':0}


def test_replacing_data_key_invalidates_previous_verification(client,store):
    store.set('data_connection_state',{'status':'verified'})
    response=client.put('/api/secrets/tikhub',json={'value':'test-new-key-no-real-credential'})
    assert response.status_code==200
    assert store.settings()['data_connection_state']['status']=='unverified'


def test_data_prices_must_be_confirmed_separately(store):
    p = store.settings()["pricing"]
    p["confirmed"] = True
    store.set("pricing", p)
    Budget(store).reserve("model", "allowed", .001)
    with pytest.raises(TaskError) as e:
        Budget(store).reserve("data", "not-verified", .01)
    assert e.value.code == "pricing_required"


def test_correcting_transcript_invalidates_visible_analysis(store, monkeypatch):
    identity = material(store)
    services.save_transcript(store, identity, "原来的口播")
    monkeypatch.setattr(DeepSeek, "json", lambda *a, **k: {"summary": "旧拆解", "structure": []})
    services.analyze(store, identity)
    assert services.detail(store, identity)["analysis"]
    services.save_transcript(store, identity, "人工纠正后的新口播")
    detail = services.detail(store, identity)
    assert detail["analysis"] is None and detail["analysis_stale"]


def test_creator_profile_ttl_does_not_extend_on_cached_read(store, monkeypatch):
    from app.providers import TikHub
    cid = "MS4wLjAB12345678"
    sampled = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
    store.run("INSERT INTO creators VALUES (?,?,?,?,?,?)", (cid, "测试博主", "https://www.douyin.com/user/"+cid, 10000, 1, sampled))
    called = []
    monkeypatch.setattr(TikHub, "profile", lambda self, cid: called.append(cid) or {"name": "测试博主", "followers": 15000})
    monkeypatch.setattr(TikHub, "posts", lambda *a, **k: {"data": {"aweme_list": [], "has_more": False}})
    engine = JobEngine(store)
    engine.execute("creators_sync", {}, lambda *a: None)
    assert called == []
    assert store.one("SELECT updated_at FROM creators WHERE id=?", (cid,))["updated_at"] == sampled
    store.run("UPDATE creators SET updated_at=? WHERE id=?", ((datetime.now(timezone.utc)-timedelta(hours=25)).isoformat(),cid))
    engine.execute("creators_sync", {}, lambda *a: None)
    assert called == [cid]


def test_pinned_old_video_does_not_stop_new_post_pagination(store, monkeypatch):
    from app.providers import TikHub
    cid="MS4wLjAB12345678"
    store.run("INSERT INTO creators VALUES (?,?,?,?,?,?)", (cid,"测试博主","https://www.douyin.com/user/"+cid,10000,1,now()))
    requested=[]
    def posts(self, cid, cursor=0):
        requested.append(cursor)
        if cursor==0:
            return {"data":{"aweme_list":[{"aweme_id":"111111111","desc":"置顶旧作","is_top":1,"create_time":int((datetime.now(timezone.utc)-timedelta(days=90)).timestamp())}],"has_more":True,"max_cursor":20}}
        return {"data":{"aweme_list":[{"aweme_id":"222222222","desc":"今天AI新教程","create_time":int(datetime.now(timezone.utc).timestamp())}],"has_more":False}}
    monkeypatch.setattr(TikHub,"posts",posts)
    JobEngine(store).execute("creators_sync",{},lambda *a:None)
    assert requested==[0,20]
    assert store.one("SELECT id FROM videos WHERE id='222222222'")


def test_streamed_json_response_usage_is_accounted(store, monkeypatch):
    import httpx
    confirm_prices(store)
    monkeypatch.setattr("app.providers.get_secret", lambda *a: "test-not-real-key")
    parts = [
        {"choices":[{"delta":{"content":'{"ok":'},"finish_reason":None}]},
        {"choices":[{"delta":{"content":'true}'},"finish_reason":"stop"}]},
        {"choices":[],"usage":{"prompt_tokens":20,"completion_tokens":10}},
    ]
    stream = "\n\n".join("data: "+json.dumps(p) for p in parts)+"\n\ndata: [DONE]\n\n"
    original = httpx.Client
    def handler(request):
        body=json.loads(request.content)
        assert body["stream"] is True and body["thinking"]["type"]=="disabled"
        return httpx.Response(200,text=stream,headers={"Content-Type":"text/event-stream"})
    monkeypatch.setattr("app.providers.httpx.Client",lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    assert DeepSeek(store).json("test",{},"输出json {\"ok\":true}",100)=={"ok":True}
    expense=store.one("SELECT * FROM expenses")
    assert expense["actual"]==pytest.approx(.0008)


def test_stream_timeout_keeps_reservation(store, monkeypatch):
    import httpx
    confirm_prices(store)
    monkeypatch.setattr("app.providers.get_secret", lambda *a:"test-not-real-key")
    original=httpx.Client
    def handler(request):raise httpx.ReadTimeout("network timeout",request=request)
    monkeypatch.setattr("app.providers.httpx.Client",lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    with pytest.raises(TaskError) as e:DeepSeek(store).json("test",{},"输出json",100)
    assert e.value.code=="network_error"
    expense=store.one("SELECT * FROM expenses")
    assert expense["status"]=="unknown" and expense["actual"] is None and expense["reserved"]>0
