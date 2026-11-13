from __future__ import annotations

import json
import re
import shutil
import subprocess
import platform
import sys
import tempfile
from pathlib import Path
from urllib.parse import urljoin

import httpx

from .providers import TaskError, TikHub, approved_public_url
from .services import ingest, save_transcript
from .task_context import check_cancelled
from .security import get_secret


def download_media(url, path: Path, *, max_bytes=512 * 1024 * 1024):
    approved_public_url(url)
    with httpx.Client(timeout=90, follow_redirects=False) as client:
        for _ in range(5):
            check_cancelled()
            approved_public_url(url)
            with client.stream("GET", url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.douyin.com/"}) as r:
                if r.is_redirect:
                    url = urljoin(url, r.headers.get("location", ""))
                    continue
                if not r.is_success:
                    raise TaskError("media_unavailable", f"视频下载失败（HTTP {r.status_code}），可改为粘贴逐字稿")
                total = 0
                with path.open("wb") as f:
                    for chunk in r.iter_bytes(256 * 1024):
                        check_cancelled()
                        total += len(chunk)
                        if total > max_bytes:
                            raise TaskError("media_too_large", "素材超过512MB，请提供较短片段或直接粘贴逐字稿")
                        f.write(chunk)
                if total == 0:
                    raise TaskError("media_unavailable", "视频文件为空")
                return
    raise TaskError("media_unavailable", "视频下载发生过多重定向")


def read_subtitles(path):
    text = path.read_text(encoding="utf-8-sig")
    if text.lstrip().startswith("{"):
        data = json.loads(text)
        lines = [str(u.get("text") or "") for u in data.get("utterances", [])]
        if not lines:
            lines = ["".join(str(s.get("utf8") or "") for s in event.get("segs", [])) for event in data.get("events", [])]
    else:
        lines = [line.strip() for line in text.splitlines() if line.strip() and "-->" not in line and not line.strip().isdigit() and not line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE", "STYLE"))]
        lines = [re.sub(r"<[^>]+>", "", line) for line in lines]
    return "\n".join(dict.fromkeys(line for line in lines if line.strip())).strip()


def transcribe(store, identity, progress):
    existing = store.one("SELECT * FROM transcripts WHERE video_id=?", (identity,))
    if existing:
        return {"cached": True, "characters": len(existing["text"])}
    row = store.one("SELECT * FROM videos WHERE id=?", (identity,))
    if not row:
        raise TaskError("not_found", "素材不存在")
    if row.get("media_kind") == "image":
        raise TaskError("image_post", "这是图文作品，请粘贴正文；不会把背景音乐当成口播转写")
    attempted_subtitle = row.get('subtitle_url')
    if attempted_subtitle:
        progress(4, '优先读取现有外挂字幕，无需本机转写组件')
        cache = store.root / 'cache'
        cache.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='captions-', dir=cache) as td:
            try:
                caption = Path(td) / 'caption.txt'
                download_media(attempted_subtitle, caption, max_bytes=2 * 1024 * 1024)
                subtitle_text = read_subtitles(caption)
                if len(subtitle_text) > 20:
                    save_transcript(store, identity, subtitle_text, source='作品外挂字幕', overwrite=False)
                    return {'source':'作品外挂字幕','characters':len(subtitle_text)}
            except TaskError as error:
                if error.code == 'cancelled':raise
            except (ValueError, UnicodeError):
                pass
    if not shutil.which("ffmpeg"):
        raise TaskError("dependency_required", "未找到ffmpeg，先完成环境检查")
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise TaskError("dependency_required", "缺少本机转写依赖，请运行安装脚本")
    settings = store.settings()
    if not settings["transcription_download_approved"]:
        raise TaskError("model_download_required", "请先在「我的」允许首次下载Whisper模型，或直接粘贴已有逐字稿")
    if not row.get("media_url") and get_secret(store, "tikhub"):
        progress(4, "正在补全原视频来源，无需先手动更新数据")
        ingest(store, TikHub(store).detail(identity), touch_source=False)
        row = store.one("SELECT * FROM videos WHERE id=?", (identity,))
        if row.get("media_kind") == "image":
            raise TaskError("image_post", "这是图文作品，请在素材研读中粘贴正文")
    progress(5, "准备素材；首次转写需要下载本地Whisper模型")
    temp_root = store.root / "cache"
    temp_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="asr-", dir=temp_root) as td:
        root = Path(td)
        media, audio = root / "source.mp4", root / "audio.wav"
        if row.get("subtitle_url") and row['subtitle_url'] != attempted_subtitle:
            try:
                progress(8, "优先读取作品提供的外挂字幕")
                caption = root / "caption.txt"
                download_media(row["subtitle_url"], caption, max_bytes=2 * 1024 * 1024)
                subtitle_text = read_subtitles(caption)
                if len(subtitle_text) > 20:
                    save_transcript(store, identity, subtitle_text, source="作品外挂字幕", overwrite=False)
                    return {"source": "作品外挂字幕", "characters": len(subtitle_text)}
            except TaskError as error:
                if error.code == "cancelled":
                    raise
            except (ValueError, UnicodeError):
                pass  # An unavailable subtitle is not permission to invent a transcript.
        if row.get("media_url"):
            progress(10, "正在下载原视频音轨来源")
            try:
                download_media(row["media_url"], media)
            except TaskError as error:
                if error.code != "media_unavailable" or not get_secret(store, "tikhub"):
                    raise
                progress(12, "视频地址可能已过期，正在更新一次来源")
                data = TikHub(store).detail(identity)
                ingest(store, data, touch_source=False)
                if not data.get("media_url"):
                    raise error
                download_media(data["media_url"], media)
        else:
            # yt-dlp fallback accepts a canonical, validated video URL only.
            approved_public_url(row["url"], share=True)
            progress(10, "正在解析公开作品；如果受限，可先粘贴逐字稿")
            command = [sys.executable, "-m", "yt_dlp", "--no-playlist", "--no-exec", "--no-progress", "--socket-timeout", "30", "--retries", "1", "--max-filesize", "512M", "--write-subs", "--write-auto-subs", "--sub-langs", "zh.*,zh", "--sub-format", "vtt/srt/best", "--write-info-json", "-f", "bestaudio/best", "-o", str(root / "source.%(ext)s"), "--", row["url"]]
            try:
                result = subprocess.run(command, capture_output=True, timeout=240)
            except subprocess.TimeoutExpired:
                raise TaskError("media_unavailable", "解析视频超时，请提供逐字稿或配置TikHub获取可用媒体地址")
            files = [p for p in root.iterdir() if p.suffix.lower() in {".mp4", ".m4a", ".webm", ".mp3", ".opus", ".wav"}]
            if result.returncode or not files:
                raise TaskError("media_unavailable", "无法取得视频音轨；请在素材详情刷新数据或粘贴逐字稿")
            media = files[0]
            # Prefer genuine timed subtitles, never substitute the video description.
            for path in [*root.glob("*.vtt"), *root.glob("*.srt")]:
                text = read_subtitles(path)
                if len(text) > 20:
                    save_transcript(store, identity, text, source="作品外挂字幕", overwrite=False)
                    return {"source": "作品外挂字幕", "characters": len(text)}
        progress(35, "正在提取人声音轨")
        r = subprocess.run(["ffmpeg", "-nostdin", "-y", "-i", str(media), "-vn", "-ac", "1", "-ar", "16000", str(audio)], capture_output=True, timeout=180)
        if r.returncode or not audio.exists():
            raise TaskError("audio_error", "无法提取视频音轨；图文作品请提供正文，避免把背景音乐当口播")
        device = "cpu" if platform.system() == "Darwin" else settings["whisper_device"]
        if device == "auto":
            try:
                import ctranslate2
                device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
            except Exception:
                device = "cpu"
        progress(45, "正在加载Whisper模型（首次可能需要下载，请保持联网）")
        model_args = {"device": device, "compute_type": "float16" if device == "cuda" else "int8", "download_root": str(store.root / "models")}
        fallback = False
        try:
            model = WhisperModel(settings["whisper_model"], **model_args)
            segments, info = model.transcribe(str(audio), language="zh", vad_filter=True, initial_prompt="人工智能，Codex，Claude，WorkBuddy，DeepSeek，Agent，GitHub，Token，Remotion")
            result = []
            for s in segments:
                check_cancelled()
                result.append({"start": s.start, "end": s.end, "text": s.text.strip()})
                progress(min(95, 50 + int(45 * s.end / max(info.duration, 1))), "本机转写中")
        except RuntimeError:
            if device != "cuda":
                raise TaskError("asr_failed", "本机Whisper加载或转写失败，请检查模型和依赖")
            fallback = True
            progress(50, "GPU运行条件不足，正在回退CPU转写")
            model = WhisperModel(settings["whisper_model"], device="cpu", compute_type="int8", download_root=str(store.root / "models"))
            segments, _ = model.transcribe(str(audio), language="zh", vad_filter=True)
            result = []
            for s in segments:
                check_cancelled()
                result.append({"start": s.start, "end": s.end, "text": s.text.strip()})
                progress(75, "本机CPU转写中，可取消")
        text = "\n".join(s["text"] for s in result if s["text"])
        if not text.strip():
            raise TaskError("no_speech", "未检测到有效口播，未将简介或背景音乐当成逐字稿")
        check_cancelled()
        written = save_transcript(store, identity, text, result, "本机Whisper·" + ("cpu" if fallback else device), overwrite=False)
        return {"characters": len(text), "segments": len(result), "cpu_fallback": fallback, "preserved_existing_manual_text": not bool(written)}
