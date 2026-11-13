"""Bounded, time-labelled frame samples; no assertion of full video viewing."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from .db import encode, now
from .providers import DeepSeek, PROMPT_VERSION, TaskError, TikHub, cache_key
from .task_context import check_cancelled


def sample_times(duration):
    if not 0 < duration <= 7200:
        raise TaskError("media_unavailable", "视频时长未知或超过2小时，先提供较短参考")
    last = max(0, duration - .15)
    return sorted(set(round(min(last, n), 2) for n in [0, 1, 2, 3, 4, 5, *[duration*i/7 for i in range(1, 7)]]))[:12]


def ensure_frames(store, identity, progress):
    from .services import ingest
    from .transcription import download_media
    if not identity.isdigit():
        raise TaskError("invalid_input", "作品ID格式无效")
    root = store.root / "cache" / "visual" / identity
    manifest = root / "samples.json"
    if manifest.exists():
        value = json.loads(manifest.read_text(encoding="utf-8"))
        if all((root / f"{i}.jpg").is_file() for i in range(len(value["seconds"]))):
            return [{"second": second, "path": root / f"{i}.jpg"} for i, second in enumerate(value["seconds"])], value["hash"]
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise TaskError("dependency_required", "画面采样需要ffmpeg与ffprobe")
    row = store.one("SELECT * FROM videos WHERE id=?", (identity,))
    if not row:
        raise TaskError("not_found", "素材不存在")
    if row["media_kind"] == "image":
        raise TaskError("image_post", "当前画面拆解面向视频，图文请先提供正文")
    root.mkdir(parents=True, exist_ok=True)
    media = root / "source.mp4"
    if not row.get("media_url"):
        ingest(store, TikHub(store).detail(identity), touch_source=False)
        row = store.one("SELECT * FROM videos WHERE id=?", (identity,))
    if not row.get("media_url"):
        raise TaskError("media_unavailable", "没有可用的视频地址，未使用封面冒充完整画面")
    progress(5, "准备真实参考视频，使用公开数据通道")
    try:
        download_media(row["media_url"], media)
    except TaskError as error:
        if error.code != "media_unavailable": raise
        refreshed = TikHub(store).detail(identity)
        ingest(store, refreshed, touch_source=False)
        if not refreshed.get("media_url"): raise
        download_media(refreshed["media_url"], media)
    try:
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(media)], capture_output=True, timeout=20)
        duration = float(json.loads(probe.stdout)["format"]["duration"])
        times = sample_times(duration)
        for i, second in enumerate(times):
            check_cancelled()
            progress(10 + int(i / len(times) * 40), f"采样画面 {second:g}秒")
            target = root / f"{i}.jpg"
            result = subprocess.run(["ffmpeg", "-nostdin", "-y", "-ss", str(second), "-i", str(media), "-frames:v", "1", "-vf", "scale=640:640:force_original_aspect_ratio=decrease", "-q:v", "3", str(target)], capture_output=True, timeout=25)
            if result.returncode or not target.exists():
                raise TaskError("media_unavailable", "关键帧采样失败，未伪造画面分析")
        digest = hashlib.sha256(b"".join((root / f"{i}.jpg").read_bytes() for i in range(len(times)))).hexdigest()
        manifest.write_text(encode({"seconds": times, "duration": duration, "hash": digest}), encoding="utf-8")
    except (ValueError, KeyError, subprocess.TimeoutExpired):
        raise TaskError("media_unavailable", "无法解析参考视频或采样超时")
    finally:
        # The persistent result consists of small frame samples, not full videos.
        if media.exists(): media.unlink()
    return [{"second": s, "path": root / f"{i}.jpg"} for i, s in enumerate(times)], digest


def analyze_visual(store, identity, progress):
    frames, fingerprint = ensure_frames(store, identity, progress)
    key = cache_key(identity, "visual_analysis", fingerprint, store.settings()["model"])
    cached = store.one("SELECT payload FROM analyses WHERE cache_key=?", (key,))
    if cached:
        return json.loads(cached["payload"])
    row = store.one("SELECT title,author FROM videos WHERE id=?", (identity,))
    progress(60, "分析真实采样画面与前5秒钩子；未采到的部分明确留空")
    result = DeepSeek(store).json("visual_analysis", {**row, "sample_seconds": [f["second"] for f in frames], "coverage": "0—5秒每秒采样及全片稀疏采样，无原始音轨输入"}, '输出json：{"summary":"画面内容与创意概述","opening":[{"second":0,"visual":"该采样帧可见内容","role":"画面钩子作用（推断）"}],"shot_structure":[{"range":"采样时间范围","visual":"可见画面结构","purpose":"可能的叙事或商业作用"}],"reuse":["可复用结构与适用边界"],"verification":["需回原视频核对的运动/声音/未采样部分"]}。只描述已提供画面，逐项区分可见事实与推断；不能声称看过连续视频，不能推断具体音乐、音效、精确剪切点或未提供的提示词。', images=frames)
    if not isinstance(result.get("summary"), str) or not isinstance(result.get("opening"), list) or not isinstance(result.get("shot_structure"), list):
        raise TaskError("invalid_response", "画面拆解结构不完整，未保存为有效结果")
    result.update(sample_seconds=[f["second"] for f in frames], sample_hash=fingerprint, coverage="0—5秒每秒采样＋全片稀疏采样；未分析音轨，连续运动和精确剪切需回原片核对")
    check_cancelled()
    store.run("INSERT OR REPLACE INTO analyses VALUES (?,?,?,?,?,?,?)", (key, identity, "visual_analysis", store.settings()["model"], PROMPT_VERSION, encode(result), now()))
    return result
