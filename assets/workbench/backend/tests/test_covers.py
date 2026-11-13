from pathlib import Path
import pytest,httpx
from app import covers,services
from app.providers import TaskError

JPEG=b'\xff\xd8\xff\xe0cover-test'
HEIC=b'\x00\x00\x00\x1cftypheic\x00\x00\x00\x00mif1'


def seed(store,cover='https://p3-sign.douyinpic.com/source.heic?signature=test'):
    services.ingest(store,dict(id='123456789',title='合成封面测试',cover=cover),'search')


def test_cover_requires_login_before_any_download(app,store,monkeypatch):
    from fastapi.testclient import TestClient
    seed(store)
    monkeypatch.setattr(covers,'fetch_cover',lambda *args:pytest.fail('anonymous request fetched a cover'))
    with TestClient(app) as c:assert c.get('/api/videos/123456789/cover').status_code==401


def test_heic_is_converted_and_cached_without_modifying_source(client,store,monkeypatch):
    seed(store);calls=[]
    def fetch(url,dest):calls.append('download');dest.write_bytes(HEIC);return 'heic'
    def convert(command,**kwargs):
        calls.append('convert');assert command[0]=='test-ffmpeg'
        Path(command[-1]).write_bytes(JPEG)
        return type('Result',(),{'returncode':0})()
    monkeypatch.setattr(covers,'fetch_cover',fetch)
    monkeypatch.setattr(covers.shutil,'which',lambda _: 'test-ffmpeg')
    monkeypatch.setattr(covers.subprocess,'run',convert)
    before=store.one('SELECT * FROM videos WHERE id=?',('123456789',))
    for _ in range(2):
        r=client.get('/api/videos/123456789/cover')
        assert r.status_code==200 and r.headers['content-type']=='image/jpeg' and r.content==JPEG
    assert calls==['download','convert']
    assert store.one('SELECT * FROM videos WHERE id=?',('123456789',))==before


def test_compatible_images_are_cached_without_ffmpeg_and_changed_source_invalidates(store,monkeypatch):
    seed(store);calls=[]
    def fetch(url,dest):calls.append(url);dest.write_bytes(JPEG);return 'jpg'
    monkeypatch.setattr(covers,'fetch_cover',fetch)
    monkeypatch.setattr(covers.subprocess,'run',lambda *a,**k:pytest.fail('should not convert jpeg'))
    first,mime=covers.cached_cover(store,'123456789')
    assert mime=='image/jpeg' and first.read_bytes()==JPEG
    covers.cached_cover(store,'123456789');assert len(calls)==1
    store.run('UPDATE videos SET cover=? WHERE id=?',('https://p3-sign.douyinpic.com/new.jpg','123456789'))
    second,_=covers.cached_cover(store,'123456789')
    assert len(calls)==2 and first!=second


def test_missing_expired_or_failed_images_do_not_trigger_paid_refresh(client,store,monkeypatch):
    seed(store)
    def fail(*args):raise TaskError('cover_unavailable','expired')
    calls=[]
    def once(*args):calls.append(1);return fail()
    monkeypatch.setattr(covers,'fetch_cover',once)
    monkeypatch.setattr('app.providers.TikHub.detail',lambda *a,**k:pytest.fail('must not call paid API'))
    assert client.get('/api/videos/123456789/cover').json()['code']=='cover_unavailable'
    assert client.get('/api/videos/123456789/cover').json()['code']=='cover_unavailable'
    assert len(calls)==1  # Failed URLs do not re-download on every list poll.
    assert client.get('/api/videos/999999999/cover').status_code==404
    assert client.get('/api/videos/..%2Fsettings/cover').status_code!=200


def test_missing_heic_decoder_is_reported_without_rewriting_metadata(store,monkeypatch):
    seed(store)
    monkeypatch.setattr(covers,'fetch_cover',lambda u,p:(p.write_bytes(HEIC),'heic')[1])
    monkeypatch.setattr(covers.shutil,'which',lambda _:None)
    with pytest.raises(TaskError) as error:covers.cached_cover(store,'123456789')
    assert error.value.code=='cover_decoder_required'


@pytest.mark.parametrize('payload,expected',[(JPEG,'jpg'),(HEIC,'heic'),(b'RIFF0000WEBPxxxx','webp'),(b'\x89PNG\r\n\x1a\nxxxx','png'),(b'\x00\x00\x00\x1cftypavif0000','avif'),(b'<html>not an image',None)])
def test_image_type_is_verified_by_bytes(payload,expected):assert covers.image_kind(payload)==expected


def test_download_validates_every_redirect_before_request_and_rejects_non_image(store,monkeypatch,tmp_path):
    requests=[]
    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(302,headers={'Location':'http://127.0.0.1/private'})
    real_client=httpx.Client
    monkeypatch.setattr(covers.httpx,'Client',lambda **kwargs:real_client(transport=httpx.MockTransport(handler)))
    def validate(url):
        if not url.startswith('https://p3-sign.douyinpic.com/'):raise TaskError('invalid_url','untrusted')
    monkeypatch.setattr(covers,'approved_public_url',validate)
    with pytest.raises(TaskError):covers.fetch_cover('https://p3-sign.douyinpic.com/start',tmp_path/'cover')
    assert requests==['https://p3-sign.douyinpic.com/start']
    monkeypatch.setattr(covers.httpx,'Client',lambda **kwargs:real_client(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=b'<html>blocked</html>'))))
    with pytest.raises(TaskError):covers.fetch_cover('https://p3-sign.douyinpic.com/start',tmp_path/'cover')


def test_download_size_is_bounded_before_cache_publication(monkeypatch,tmp_path):
    real_client=httpx.Client
    monkeypatch.setattr(covers,'approved_public_url',lambda url:url)
    transport=httpx.MockTransport(lambda r:httpx.Response(200,headers={'Content-Length':str(covers.MAX_BYTES+1)},content=b''))
    monkeypatch.setattr(covers.httpx,'Client',lambda **kwargs:real_client(transport=transport))
    with pytest.raises(TaskError):covers.fetch_cover('https://p3-sign.douyinpic.com/start',tmp_path/'cover')
    assert not (tmp_path/'cover').exists()
