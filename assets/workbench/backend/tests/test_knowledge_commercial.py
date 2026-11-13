import hashlib
import json
from datetime import datetime, timedelta

import pytest

from app import knowledge, services
from app.db import now
from app.jobs import JobEngine, TZ
from app.providers import TaskError


def seed(store, identity='123456789', title='AI知识库教程', source='favorites'):
    services.ingest(store, {'id':identity,'title':title,'author':'作者','author_id':'author','published_at':now(),'followers':1000,'likes':1000}, source)
    return identity


def configure(store,tmp_path):
    vault=tmp_path/'existing-vault'; (vault/'.obsidian').mkdir(parents=True)
    (vault/'old-note.md').write_text('原有人工笔记',encoding='utf-8')
    config={**store.settings()['knowledge'],'enabled':True,'vault_path':str(vault),'enabled_at':'2026-11-02T06:00:00+00:00'}
    store.set('knowledge',config)
    return vault


def test_index_all_personal_types_preserve_manual_and_skip_generated_conflicts(store,tmp_path):
    vault=configure(store,tmp_path)
    seed(store)
    seed(store,'123456790','生活技巧')
    seed(store,'123456791','Flova广告运镜创意')
    seed(store,'123456792','AI未收藏外部','search')
    first=knowledge.sync_vault(store,lambda *args:None)
    root=vault/knowledge.FOLDER
    assert first['indexed']==3
    assert (vault/'old-note.md').read_text(encoding='utf-8')=='原有人工笔记'
    source=root/'01-收藏素材/抖音/DY-123456789.md'
    source.write_text(source.read_text(encoding='utf-8')+'人工新增备注',encoding='utf-8')
    store.run('UPDATE videos SET likes=3000 WHERE id=?',('123456789',))
    knowledge.sync_vault(store,lambda *args:None)
    assert '人工新增备注' in source.read_text(encoding='utf-8')
    assert '点赞：3000' in source.read_text(encoding='utf-8')
    source.write_text(source.read_text(encoding='utf-8').replace('点赞：3000','点赞：手工值'),encoding='utf-8')
    store.run('UPDATE videos SET likes=4000 WHERE id=?',('123456789',))
    report=knowledge.sync_vault(store,lambda *args:None)
    assert '01-收藏素材/抖音/DY-123456789.md' in report['conflicts']
    assert '点赞：手工值' in source.read_text(encoding='utf-8')
    base=(root/'00-入口与分类/收藏索引.base').read_text(encoding='utf-8')
    assert '<!--' not in base and base.startswith('filters:')
    assert 'note.themes' in base and 'formula.display_title' in base  # Bases property IDs, not bare frontmatter keys.
    assert len(list((root/'01-收藏素材/抖音').glob('*.md')))==3
    assert store.one('SELECT COUNT(*) n FROM expenses')['n']==0


@pytest.mark.skip(reason="已下线的知识库/制作行为；由 test_discovery 的停用与数据保留检查替代")
def test_weekly_sunday_schedule_catches_up_once_without_duplicate_active_job(store,tmp_path):
    configure(store,tmp_path)
    engine=JobEngine(store)
    before=datetime(2026,11,8,20,59,tzinfo=TZ)
    engine.schedule_tick(before)
    assert not store.rows("SELECT id FROM jobs WHERE kind='knowledge_sync'")
    # A manual export still running must not consume the Sunday slot.
    active=engine.enqueue('knowledge_sync',{},'manual-knowledge')
    engine.schedule_tick(before+timedelta(minutes=1))
    assert not store.one("SELECT slot FROM schedule_runs WHERE slot='knowledge:2026-W45'")
    store.run("UPDATE jobs SET status='succeeded' WHERE id=?",(active,))
    after=datetime(2026,11,9,8,0,tzinfo=TZ)
    engine.schedule_tick(after); engine.schedule_tick(after)
    assert len(store.rows("SELECT id FROM jobs WHERE kind='knowledge_sync'"))==2
    job=store.rows("SELECT payload FROM jobs WHERE kind='knowledge_sync' AND status='queued'")[0]
    assert json.loads(job['payload'])=={'period':'2026-W45'}


def test_track_labels_multiselect_and_automatic_metadata_never_override_manual(client,store):
    identity=seed(store,title='即梦画布教程：做产品广告')
    assert services.detail(store,identity)['content_tracks']==['AI教程','AIGC']
    assert client.get('/api/videos',params={'scope':'personal','track':'AIGC'}).json()['total']==1
    assert client.patch('/api/videos/'+identity,json={'content_tracks':['AIGC']}).status_code==200
    services.ingest(store,{'id':identity,'title':'新的AI教程标题'},touch_source=False)
    assert services.detail(store,identity)['content_tracks']==['AIGC']


@pytest.mark.skip(reason="已下线的知识库/制作行为；由 test_discovery 的停用与数据保留检查替代")
def test_project_reference_triggers_deep_read_without_moving_external_to_collection(client,store,tmp_path):
    configure(store,tmp_path)
    identity=seed(store,source='search')
    p=client.post('/api/projects',json={'creation_mode':'commercial','video_id':identity}).json()['id']
    assert store.one('SELECT priority FROM knowledge_items WHERE video_id=?',(identity,))['priority']==1
    assert len(store.rows("SELECT id FROM jobs WHERE kind='knowledge_deep'"))==1
    assert client.get('/api/videos',params={'scope':'personal'}).json()['total']==0
    assert client.get('/api/videos',params={'scope':'recommendations'}).json()['total']==1
    v=services.project(store,p)
    bad=client.patch('/api/projects/'+p,json={'revision':v['revision'],'changes':{'brief':{'fake':'bad'}}})
    assert bad.status_code==400


def test_commercial_brief_script_and_history_protection(client,store,monkeypatch):
    p=client.post('/api/projects',json={'creation_mode':'commercial','title':'测试商单'}).json()['id']
    first=services.project(store,p)
    assert first['video'] is None and first['creation_mode']=='commercial'
    client.patch('/api/projects/'+p,json={'revision':1,'changes':{'brief':{'product':'测试画布','goal':'展示已验证能力'}}})
    captured=[]
    def generate(self,kind,source,instruction,max_tokens=4000):
        captured.append((kind,source,instruction))
        if kind=='hooks':return {'hooks':[{'text':f'画面方案{i}','visual':'0—5秒画面','audio':'声音建议','screen_text':'结果'} for i in range(3)]}
        return {'content':'正文','shooting_plan':[{'segment':'第一镜','time':'0—5秒','visual':'真实结果画面','tool_point':'待验证能力','asset_method':'录屏'}],'verification':['能力待验证']}
    monkeypatch.setattr('app.services.DeepSeek.json',generate)
    services.generate_project(store,p,'hooks',2)
    assert services.project(store,p)['hooks'][0]['audio']=='声音建议'
    client.patch('/api/projects/'+p,json={'revision':3,'changes':{'selected_hook':'画面方案0'}})
    services.generate_project(store,p,'script',4)
    result=services.project(store,p)
    assert result['shooting_plan'][0]['visual']=='真实结果画面'
    assert result['content'].startswith('画面方案0')
    assert captured[0][1]['brief']['product']=='测试画布'
    assert '不强制真人' in captured[0][2]
    assert client.patch('/api/projects/'+p,json={'revision':2,'changes':{'content':'旧稿覆盖'}}).status_code==409
    restore=client.post('/api/projects/'+p+'/restore',json={'revision':5,'version':1})
    assert restore.status_code==200
    assert restore.json()['brief']=={} and restore.json()['creation_mode']=='commercial'


def test_deep_read_uses_real_transcript_does_not_call_asr_again_and_exports_knowledge(store,tmp_path,monkeypatch):
    vault=configure(store,tmp_path)
    identity=seed(store)
    services.save_transcript(store,identity,'真实原文：如何搭建知识库，尚未验证效果。')
    monkeypatch.setattr('app.providers.DeepSeek.json',lambda self,*args,**kwargs:{'summary':'原文方法摘要','takeaways':['文件索引'],'steps':['建立文件夹'],'applications':['知识库教程'],'verification':['版本需核实']})
    monkeypatch.setattr('app.transcription.transcribe',lambda *args:pytest.fail('existing transcript must be reused'))
    knowledge.deep_read(store,identity,lambda *args:None)
    knowledge.sync_vault(store,lambda *args:None)
    note=(vault/knowledge.FOLDER/'02-可复用知识/CAS-DY-123456789.md').read_text(encoding='utf-8')
    assert 'source_documents:' in note and 'type: "CAS"' in note and '原文方法摘要' in note
    assert services.detail(store,identity)['knowledge']['state']=='ready'


def test_saving_analysis_to_knowledge_reuses_steps_without_another_model_call(store, monkeypatch):
    identity = seed(store)
    services.save_transcript(store, identity, '真实原文：新建文件夹，然后建立索引。')
    calls = []

    def generate(self, purpose, *args, **kwargs):
        calls.append(purpose)
        return {'summary': '为文件建立索引', 'hook': '先看索引结果', 'structure': [],
                'steps': ['新建文件夹', '建立索引'], 'verification': ['核对工具版本']}

    monkeypatch.setattr('app.providers.DeepSeek.json', generate)
    services.analyze(store, identity)
    knowledge.deep_read(store, identity, lambda *args: None)
    item = services.detail(store, identity)['knowledge']
    assert calls == ['analysis']
    assert item['payload']['steps'] == ['新建文件夹', '建立索引']
    assert item['payload']['opening_reference'] == '先看索引结果'
    assert knowledge.knowledge_current(item, services.detail(store, identity)['transcript'])
    services.save_transcript(store, identity, '原文校正为先新建项目。')
    assert not knowledge.knowledge_current(item, services.detail(store, identity)['transcript'])


def test_visual_only_knowledge_reuses_sampled_evidence_without_inventing_steps(store, monkeypatch):
    identity = seed(store, title='即梦分镜运镜案例')
    store.run('INSERT INTO analyses VALUES (?,?,?,?,?,?,?)', ('visual', identity, 'visual_analysis', 'test', 'v1', json.dumps({
        'summary': '同一场景的两种镜头', 'opening': [{'second': 1, 'visual': '低机位画面'}],
        'reuse': ['先给出画面结果'], 'verification': ['原声需回看'],
    }), now()))
    monkeypatch.setattr('app.providers.DeepSeek.json', lambda *a, **k: pytest.fail('existing visual evidence should be reused'))
    monkeypatch.setattr('app.transcription.transcribe', lambda *a: pytest.fail('visual-only card needs no new transcript'))
    knowledge.deep_read(store, identity, lambda *args: None)
    payload = services.detail(store, identity)['knowledge']['payload']
    assert payload['opening_reference'] == '1秒：低机位画面'
    assert payload['steps'] == []
    assert payload['verification'] == ['原声需回看']


def test_owned_export_never_overwrites_unregistered_existing_file_and_scrubs_credentials(store,tmp_path):
    root=configure(store,tmp_path)/knowledge.FOLDER;root.mkdir()
    (root/'existing.md').write_text('用户原文',encoding='utf-8')
    assert knowledge.write_owned(store,root,'existing.md','生成内容')=='conflict'
    knowledge.write_owned(store,root,'safe.md','密钥 '+'sk-'+'0'*32)
    assert 'sk-' not in (root/'safe.md').read_text(encoding='utf-8')
    with pytest.raises(TaskError):knowledge.write_owned(store,root,'../escape.md','内容')


def test_non_ai_tutorials_and_ads_are_not_misclassified_as_ai():
    assert knowledge.infer_tracks('做饭教程：番茄炒蛋')==['其他']
    assert knowledge.infer_tracks('传统产品广告运镜')==['其他']
    assert knowledge.infer_tracks('Flova画布广告教程')==['AI教程','AIGC']
    assert services.preliminary('AI知识库实操教程')['is_ai']==1


def test_visual_samples_are_bounded_and_include_first_five_seconds():
    from app.visual import sample_times
    times=sample_times(90)
    assert times[:6]==[0,1,2,3,4,5] and len(times)<=12
    assert max(sample_times(2))<2
    with pytest.raises(TaskError):sample_times(0)


def test_vision_request_uses_real_image_blocks_and_accounts_image_budget(store,tmp_path,monkeypatch):
    import httpx
    from app.providers import DeepSeek
    settings=store.settings()['pricing'];settings['confirmed']=True;store.set('pricing',settings)
    monkeypatch.setattr('app.providers.get_secret',lambda *args:'test-key-not-a-real-secret')
    captured=[]
    original=httpx.Client
    def handler(request):
        body=json.loads(request.content);captured.append(body)
        events=[{'choices':[{'delta':{'content':'{"summary":"可见画面"}'},'finish_reason':'stop'}],'usage':{'prompt_tokens':1500,'completion_tokens':20}}]
        return httpx.Response(200,text=''.join('data: '+json.dumps(e)+'\n\n' for e in events)+'data: [DONE]\n\n')
    monkeypatch.setattr('app.providers.httpx.Client',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    image=tmp_path/'0.jpg';image.write_bytes(b'jpeg-test-fixture')
    result=DeepSeek(store).json('visual_analysis',{'title':'参考'},'描述画面',images=[{'second':1,'path':image}])
    assert result['summary']=='可见画面'
    blocks=captured[0]['messages'][1]['content']
    assert blocks[-1]['image_url']['url'].startswith('data:image/jpeg;base64,')
    assert blocks[-2]['text']=='采样时间：1秒'
    expense=store.one('SELECT * FROM expenses')
    assert expense['reserved'] >= 1024*settings['model_input_cny_million']/1000000
    assert 'base64' not in expense['usage']


def test_commercial_references_analyzed_and_invalidated_when_brief_changes(client,store,monkeypatch):
    identity=seed(store,title='Flova广告创意')
    p=client.post('/api/projects',json={'creation_mode':'commercial','video_id':identity}).json()['id']
    monkeypatch.setattr('app.commercial.analyze_visual',lambda *args:{'summary':'采样画面','sample_seconds':[0,1,2,3,4,5]})
    monkeypatch.setattr('app.commercial.DeepSeek.json',lambda *args,**kwargs:{'summary':'参考结构','opening_reference':['先展示结果']})
    from app.commercial import analyze_references
    analyze_references(store,p,1,lambda *args:None)
    assert services.project(store,p)['reference_analysis']['summary']=='参考结构'
    changed=client.patch('/api/projects/'+p,json={'revision':2,'changes':{'brief':{'product':'新产品'}}})
    assert changed.status_code==200 and changed.json()['reference_analysis']=={}


def test_deep_read_does_not_overwrite_with_old_transcript_result(store,tmp_path,monkeypatch):
    configure(store,tmp_path);identity=seed(store)
    services.save_transcript(store,identity,'旧原文')
    def generate(*args,**kwargs):
        services.save_transcript(store,identity,'更新后的原文')
        return {'summary':'旧原文提炼','takeaways':[]}
    monkeypatch.setattr('app.providers.DeepSeek.json',generate)
    with pytest.raises(TaskError) as e:knowledge.deep_read(store,identity,lambda *args:None)
    assert e.value.code=='revision_conflict'
    assert services.detail(store,identity)['knowledge']['state']=='attention'
