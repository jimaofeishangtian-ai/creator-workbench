"""Serve authenticated, locally cached thumbnails, including Douyin HEIC covers."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urljoin

import httpx

from .providers import TaskError, approved_public_url

MAX_BYTES = 5 * 1024 * 1024
SLOTS = threading.BoundedSemaphore(4)
LOCKS = [threading.Lock() for _ in range(32)]
FAILURES: OrderedDict[str, float] = OrderedDict()
FAILURE_LOCK = threading.Lock()


def image_kind(data: bytes):
    if data.startswith(b'\xff\xd8\xff'):return 'jpg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):return 'png'
    if data[:6] in {b'GIF87a',b'GIF89a'}:return 'gif'
    if data[:4]==b'RIFF' and data[8:12]==b'WEBP':return 'webp'
    if data[4:8]==b'ftyp':
        brands=data[8:64]
        if b'avif' in brands or b'avis' in brands:return 'avif'
        if any(v in brands for v in (b'heic',b'heix',b'hevc',b'hevx',b'mif1',b'msf1')):return 'heic'
    return None


def fetch_cover(url: str, destination: Path):
    # Only the URL already stored for this video, never a caller-supplied proxy target.
    with httpx.Client(timeout=httpx.Timeout(12,connect=5),follow_redirects=False) as client:
        for _ in range(4):
            approved_public_url(url)
            with client.stream('GET',url,headers={'User-Agent':'Mozilla/5.0','Referer':'https://www.douyin.com/','Accept':'image/avif,image/webp,image/*'}) as response:
                if response.is_redirect:
                    location=response.headers.get('location')
                    if not location:break
                    url=urljoin(url,location)
                    continue
                if response.status_code!=200:raise TaskError('cover_unavailable','封面暂不可用，可打开原视频查看')
                length=response.headers.get('content-length','')
                if length.isdigit() and int(length)>MAX_BYTES:raise TaskError('cover_unavailable','封面超过大小限制')
                size=0
                with destination.open('wb') as f:
                    for chunk in response.iter_bytes(64*1024):
                        size+=len(chunk)
                        if size>MAX_BYTES:raise TaskError('cover_unavailable','封面超过大小限制')
                        f.write(chunk)
                kind=image_kind(destination.read_bytes()[:64])
                if not kind:raise TaskError('cover_unavailable','封面不是可识别的图片')
                return kind
    raise TaskError('cover_unavailable','封面重定向次数过多')


def cached_cover(store, identity: str):
    if not re.fullmatch(r'\d{6,30}',identity):raise TaskError('not_found','封面不存在')
    row=store.one('SELECT cover FROM videos WHERE id=?',(identity,))
    if not row or not row['cover']:raise TaskError('not_found','没有可用封面')
    # Include the exact signed source URL so a future metadata refresh invalidates old failures.
    key=hashlib.sha256(('cover-v1:'+row['cover']).encode()).hexdigest()
    folder=store.root/'cache'/'covers';folder.mkdir(parents=True,exist_ok=True)
    target=folder/(identity+'-'+key)
    with LOCKS[int(key[:4],16)%len(LOCKS)]:
        for ext in ('jpg','png','webp','avif','gif'):
            path=target.with_suffix('.'+ext)
            if path.is_file():return path,{'jpg':'image/jpeg'}.get(ext,'image/'+ext)
        cache_key=str(target)
        with FAILURE_LOCK:
            failed_at=FAILURES.get(cache_key)
        if failed_at and time.monotonic()-failed_at<300:raise TaskError('cover_unavailable','封面暂不可用，可打开原视频查看')
        if not SLOTS.acquire(timeout=15):raise TaskError('cover_unavailable','封面加载繁忙，请稍后重试')
        try:
            with tempfile.TemporaryDirectory(prefix='thumb-',dir=folder) as temp:
                source=Path(temp)/'source';kind=fetch_cover(row['cover'],source)
                if kind=='heic':
                    ffmpeg=shutil.which('ffmpeg')
                    if not ffmpeg:raise TaskError('cover_decoder_required','这张封面需要本机图片转换组件，可先打开原视频查看')
                    output=Path(temp)/'cover.jpg'
                    result=subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-nostdin','-threads','1','-protocol_whitelist','file,pipe','-i',str(source),'-frames:v','1','-vf',"scale='min(540,iw)':-2",'-q:v','3','-threads','1','-y',str(output)],capture_output=True,timeout=20,creationflags=0x08000000 if os.name=='nt' else 0)
                    if result.returncode or not output.is_file() or image_kind(output.read_bytes()[:64])!='jpg':raise TaskError('cover_unavailable','封面转换失败，可打开原视频查看')
                    source,kind=output,'jpg'
                path=target.with_suffix('.'+kind)
                os.replace(source,path)
                return path,{'jpg':'image/jpeg'}.get(kind,'image/'+kind)
        except (TaskError,httpx.HTTPError,subprocess.SubprocessError,OSError) as exc:
            with FAILURE_LOCK:
                FAILURES[cache_key]=time.monotonic()
                while len(FAILURES)>256:FAILURES.popitem(last=False)
            if isinstance(exc,TaskError):raise
            # Network/process exception strings may contain signed URLs; do not persist them.
            raise TaskError('cover_unavailable','封面暂不可用，可打开原视频查看') from None
        finally:SLOTS.release()
