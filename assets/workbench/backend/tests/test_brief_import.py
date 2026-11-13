import io
import json
import zipfile
from urllib.parse import quote

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app import brief_import, services
from app.jobs import JobEngine
from app.providers import DeepSeek, TaskError


TEXT = '产品是示例画布。面向AI小白。推广目标：引导体验。核心卖点：统一管理素材。必须提到示例助手。禁止说永久免费。风格自然口语。交付1条60秒竖屏视频，11月10日前提交。'


def model_result():
    pairs = {
        'product': ('示例画布', '产品是示例画布'),
        'audience': ('AI小白', '面向AI小白'),
        'goal': ('引导体验', '推广目标：引导体验'),
        'selling_points': ('统一管理素材', '核心卖点：统一管理素材'),
        'must_include': ('示例助手', '必须提到示例助手'),
        'forbidden': ('禁止说永久免费', '禁止说永久免费'),
        'style': ('自然口语', '风格自然口语'),
        'deliverables': ('1条60秒竖屏视频，11月10日前提交', '交付1条60秒竖屏视频，11月10日前提交'),
    }
    return {'fields': {k: v[0] for k, v in pairs.items()}, 'evidence': {k: [v[1]] for k, v in pairs.items()}, 'questions': []}


def docx_bytes(xml=None):
    data = io.BytesIO()
    xml = xml or '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>产品：示例画布</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>交付：60秒竖屏</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('word/document.xml', xml)
    return data.getvalue()


def pdf_bytes(blank=False, password=None):
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=200)
    if not blank:
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        content = DecodedStreamObject(); content.set_data(b'BT /F1 12 Tf 10 150 Td (Product: Demo Canvas. Deliver one 60-second video.) Tj ET')
        page[NameObject('/Contents')] = content
    if password: writer.encrypt(password)
    data = io.BytesIO(); writer.write(data)
    return data.getvalue()


@pytest.mark.parametrize('encoding', ['utf-8-sig', 'utf-16', 'gb18030'])
def test_text_encodings_are_read_without_silent_loss(encoding):
    result = brief_import.read_file(TEXT.encode(encoding), '甲方Brief.txt')
    assert result['text'] == TEXT and result['source_name'] == '甲方Brief.txt'


def test_docx_reads_body_and_tables_without_extracting_files(tmp_path):
    result = brief_import.read_file(docx_bytes(), '../甲方Brief.docx')
    assert '示例画布' in result['text'] and '60秒竖屏' in result['text']
    assert result['source_name'] == '甲方Brief.docx' and result['warnings']
    assert not list(tmp_path.iterdir())


def test_pdf_text_encrypted_and_scanned_cases():
    assert 'Demo Canvas' in brief_import.read_file(pdf_bytes(), 'brief.pdf')['text']
    for value, expected in [(pdf_bytes(blank=True), '扫描件'), (pdf_bytes(password='test-only'), '加密')]:
        with pytest.raises(TaskError, match=expected):
            brief_import.read_file(value, 'brief.pdf')


def test_import_rejects_unsupported_oversized_corrupt_or_entity_input():
    for data, name in [(b'content', 'brief.doc'), (b'no-pdf', 'brief.pdf'), (b'x' * (brief_import.MAX_FILE_BYTES + 1), 'brief.txt'), (docx_bytes('<!DOCTYPE x [<!ENTITY a "x">]><x>&a;</x>'), 'brief.docx')]:
        with pytest.raises(TaskError): brief_import.read_file(data, name)
    with pytest.raises(TaskError, match='30000'):
        brief_import.validate_text('字' * 30001)
    with pytest.raises(TaskError, match='正文'):
        brief_import.validate_text('https://example.com/private-brief')


def test_file_route_requires_auth_csrf_and_does_not_call_model(client, store, monkeypatch):
    monkeypatch.setattr(DeepSeek, 'json', lambda *args: pytest.fail('reading a file must be local and free'))
    response = client.post('/api/brief/read-file', content=docx_bytes(), headers={'X-File-Name': quote('甲方Brief.docx')})
    assert response.status_code == 200 and '示例画布' in response.json()['text']
    assert client.post('/api/brief/read-file', content=TEXT.encode(), headers={'X-File-Name':'brief.txt','X-CSRF-Token':'wrong'}).status_code == 403
    assert not store.rows('SELECT id FROM expenses')


def test_extraction_returns_grounded_preview_without_editing_project(store, monkeypatch):
    identity = services.create_project(store, None, 'commercial', '保留原项目')
    original = services.project(store, identity)
    monkeypatch.setattr(DeepSeek, 'json', lambda *args: model_result())
    result = brief_import.extract(store, identity, TEXT, 'brief.txt', lambda *args: None)
    assert result['fields']['product'] == '示例画布' and not result['missing_fields']
    assert services.project(store, identity) == original
    assert result['evidence']['forbidden'] == ['禁止说永久免费']


def test_unquoted_or_invented_fields_are_left_empty(store, monkeypatch):
    identity = services.create_project(store, None, 'commercial')
    result = model_result()
    result['evidence']['audience'] = ['面向企业老板']
    result['fields']['goal'] = '编造要求'; result['evidence']['goal'] = []
    monkeypatch.setattr(DeepSeek, 'json', lambda *args: result)
    preview = brief_import.extract(store, identity, TEXT, '粘贴的Brief', lambda *args: None)
    assert preview['fields']['audience'] == preview['fields']['goal'] == ''
    assert set(preview['missing_fields']) == {'audience', 'goal'}
    assert len(preview['warnings']) == 2


def test_malformed_extraction_keeps_original_and_does_not_claim_success(store, monkeypatch):
    identity = services.create_project(store, None, 'commercial')
    monkeypatch.setattr(DeepSeek, 'json', lambda *args: {'fields': {}, 'evidence': {}})
    with pytest.raises(TaskError): brief_import.extract(store, identity, TEXT, '', lambda *args: None)
    assert services.project(store, identity)['brief'] == {}


@pytest.mark.skip(reason="已下线的知识库/制作行为；由 test_discovery 的停用与数据保留检查替代")
def test_duplicate_import_reuses_finished_or_active_job_and_preserves_revision(client, store, app, monkeypatch):
    identity = client.post('/api/projects', json={'creation_mode':'commercial'}).json()['id']
    payload = {'project_id':identity,'revision':1,'text':TEXT,'source_name':'brief.txt'}
    first = client.post('/api/jobs', json={'kind':'brief_extract','payload':payload}).json()['job_id']
    assert client.post('/api/jobs', json={'kind':'brief_extract','payload':payload}).json()['job_id'] == first
    monkeypatch.setattr(DeepSeek, 'json', lambda *args: model_result())
    preview = app.state.engine.execute('brief_extract', payload, lambda *args: None, job_id=first)
    store.run("UPDATE jobs SET status='succeeded',result=? WHERE id=?", (json.dumps(preview), first))
    cached = client.post('/api/jobs', json={'kind':'brief_extract','payload':payload}).json()
    assert cached == {'job_id':first,'cached':True}
    assert client.get('/api/projects/' + identity).json()['revision'] == 1
    assert client.get('/api/jobs/' + first).json()['result']['fields']['product'] == '示例画布'
    assert client.get('/api/jobs').json()[0]['result']['fields']['product'] == '示例画布'


def test_apply_remains_an_explicit_versioned_edit_with_conflict_guard(client, store, monkeypatch):
    identity = client.post('/api/projects', json={'creation_mode':'commercial'}).json()['id']
    first = client.patch('/api/projects/' + identity, json={'revision':1,'changes':{'brief':{'product':'人工填写的平台','forbidden':'保留这句'}}}).json()
    monkeypatch.setattr(DeepSeek, 'json', lambda *args: model_result())
    preview = brief_import.extract(store, identity, TEXT, 'brief.txt', lambda *args: None)
    assert client.get('/api/projects/' + identity).json()['brief'] == first['brief']
    merged = {**preview['fields'], 'product':first['brief']['product'],'forbidden':first['brief']['forbidden']}
    applied = client.patch('/api/projects/' + identity, json={'revision':2,'changes':{'brief':merged}})
    assert applied.status_code == 200 and applied.json()['revision'] == 3
    assert applied.json()['brief']['product'] == '人工填写的平台'
    assert applied.json()['brief']['forbidden'] == '保留这句'
    conflict = client.patch('/api/projects/' + identity, json={'revision':2,'changes':{'brief':preview['fields']}})
    assert conflict.status_code == 409


@pytest.mark.skip(reason="已下线的知识库/制作行为；由 test_discovery 的停用与数据保留检查替代")
def test_cancelled_import_cannot_make_a_model_request(store, monkeypatch):
    identity = services.create_project(store, None, 'commercial')
    engine = JobEngine(store)
    job = engine.enqueue('brief_extract', {'project_id':identity,'revision':1,'text':TEXT})
    engine.cancel(job)
    monkeypatch.setattr(DeepSeek, 'json', lambda *args: pytest.fail('cancelled job must not request a model'))
    with pytest.raises(TaskError) as error:
        engine.execute('brief_extract', {'project_id':identity,'revision':1,'text':TEXT}, lambda *args: None, job_id=job)
    assert error.value.code == 'cancelled'
