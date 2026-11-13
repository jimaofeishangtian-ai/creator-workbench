import pytest

from app import services
from app.providers import TaskError
from app.transcription import transcribe


def test_existing_captions_work_without_ffmpeg_whisper_or_model_download(store,monkeypatch):
    services.ingest(store,{'id':'986000001','title':'现成字幕验收','subtitle_url':'https://example.com/captions.vtt'},'manual')
    monkeypatch.setattr('app.transcription.shutil.which',lambda *a:None)
    calls=[]
    def download(url,path,**kw):
        calls.append(url)
        path.write_text('WEBVTT\n\n00:00:00.000 --> 00:00:08.000\n这是一段已经由作品提供的真实外挂字幕内容，用于验证不安装语音转写组件也能读取。\n',encoding='utf-8')
    monkeypatch.setattr('app.transcription.download_media',download)
    result=transcribe(store,'986000001',lambda *a:None)
    assert result['source']=='作品外挂字幕'
    assert store.one('SELECT text FROM transcripts WHERE video_id=?',('986000001',))['text'].startswith('这是一段')
    assert transcribe(store,'986000001',lambda *a:None)['cached']
    assert len(calls)==1 and store.one('SELECT count(*) n FROM expenses')['n']==0


def test_cancelled_caption_download_does_not_start_audio_transcription(store,monkeypatch):
    services.ingest(store,{'id':'986000001','title':'字幕取消验收','subtitle_url':'https://example.com/captions.vtt'},'manual')
    def cancel(*a,**kw):raise TaskError('cancelled','已取消')
    monkeypatch.setattr('app.transcription.download_media',cancel)
    monkeypatch.setattr('app.transcription.shutil.which',lambda *a:pytest.fail('must not fall through to ASR'))
    with pytest.raises(TaskError) as error:transcribe(store,'986000001',lambda *a:None)
    assert error.value.code=='cancelled'
