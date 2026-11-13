from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .db import Store, encode, now
from .providers import DeepSeek, PROMPT_VERSION, TaskError, cache_key
from .output_models import AnalysisOutput, HooksOutput, ScriptOutput
from .knowledge import infer_tracks, tracks_for
from .collection_groups import COLLECTION_GROUPS, COLLECTION_GROUP_LABELS, normalize_collection_group, collection_group_label

TOOLS = {"codex": "Codex", "claude": "Claude", "workbuddy": "WorkBuddy", "豆包": "豆包", "deepseek": "DeepSeek", "github": "GitHub", "agent": "Agent", "remotion": "Remotion", "heygen": "HeyGen", "chatgpt": "ChatGPT", "gemini": "Gemini", "即梦": "即梦", "obsidian": "Obsidian"}
TOOLS.update({"flova": "Flova", "可灵": "可灵", "海螺": "海螺", "vidu": "Vidu", "seedance": "Seedance", "liblib": "Liblib"})


def collection_group_for(row):
    """Shelf based on how a saved work can be reused, independent of its subject."""
    if row.get("collection_group"):
        return row["collection_group"]
    title = re.split(r"[#＃]", row["title"], maxsplit=1)[0]
    tags = set(re.findall(r"[#＃]([^\s#＃]+)", row["title"].lower()))
    tracks = row["content_tracks"]
    instruction = re.search(r"教程|教学|实操|工作流|如何|怎么[做用拍剪生成选]|从零开始|提示词(?:[：:]|合集|展示|分享)|制作流程|全流程|完整自动化链路|教你|复刻|详细分享|\d+\s*(?:个)?[招步]|技巧|小妙招|邪修|轻松学会", title)
    visual = "AIGC" in tracks or bool(tags & {"aigc", "aigc教程", "ai视频教程", "ai短剧", "ai漫剧", "ai电影", "ai动画", "ai变装"})
    if visual:
        if instruction:
            return "aigc_tutorial"
        if tags & {"aigc教程", "ai视频教程", "ai短剧教程", "运镜教程", "即梦教程", "flovaai教程", "ai教学"}:
            return "aigc_tutorial"
        clip_title = re.search(r"(?:作品|成片|效果)展示|(?:原创|自制|自导)(?:ai)?(?:短片|短剧|漫剧|动画|电影)|(?:ai)?(?:短片|短剧|动画|电影)[《「]|预告片|样片|导演作品|共创作品", title, re.IGNORECASE)
        clip_tags = tags & {"原创漫剧", "原创动画", "原创短片", "意境美学", "ai变装"}
        story_tags = "短剧" in tags and bool(tags & {"ai短剧", "穿越"})
        if row["media_kind"] != "image" and (clip_title or clip_tags or story_tags):
            return "aigc_example"
        return "review"
    if "AI教程" in tracks and (instruction or re.search(r"工具|新功能|安装|搭建|操作|指南|推荐|用法|手搓|实测|评测|选型|演示", title) or tags & {"ai教程", "ai工具"}):
        return "ai_tutorial"
    return "other"


def preliminary(title):
    lower = title.lower()
    tools = [name for key, name in TOOLS.items() if key in lower]
    is_ai = bool(tools or re.search(r"(?<![a-z])ai(?![a-z])", lower) or any(k in lower for k in ["人工智能", "大模型", "ai工具", "ai教程", "aigc", "ai ", " ai", "ai视频", "ai剪辑", "提示词", "token"]))
    category = "其他"
    for words, name in [(["token", "省钱", "提效"], "效率与Token"), (["记忆", "知识库", "obsidian", "rag"], "知识库与记忆"), (["视频", "剪辑", "remotion", "配音", "数字人"], "AI视频与剪辑"), (["github", "编程", "代码", "开源", "codex"], "编程与开源"), (["agent", "工作流", "自动化"], "Agent工作流"), (["发布", "更新", "新功能", "工具"], "工具与新功能")]:
        if any(w in lower for w in words):
            category = name
            break
    topic = re.sub(r"#[^\s#]+|[^\w\u4e00-\u9fff]", "", lower)[:100] or title[:100]
    finer = [(["商单", "广告", "分镜", "运镜", "镜头语言"], "商单创意与分镜"), (["数字人", "配音", "heygen"], "数字人与配音"), (["画布", "flova"], "画布平台"), (["文生视频", "图生视频", "文生图", "图像生成", "可灵", "海螺", "vidu", "即梦", "seedance"], "AI图像与视频生成")]
    if is_ai:
        for words, label in finer:
            if any(word in lower for word in words):
                category = label
                break
    else:
        category = "其他"
        for words, label in [(["理财", "股票", "财经", "财报", "投资"], "理财与财经"), (["商业", "营销", "广告", "商单", "销售"], "商业与营销"), (["学习", "读书", "成长", "认知"], "学习与成长"), (["生活", "做饭", "旅行", "美食", "健身", "摄影"], "生活与兴趣")]:
            if any(word in lower for word in words):
                category = label
                break
    return {"is_ai": int(is_ai), "category": category, "tools": encode(tools), "content_tracks": encode(infer_tracks(title, is_ai)), "topic_key": topic, "classification_method": "初步分类·标题规则", "confidence": None}


def record_author_followers(store, author_id, name, followers, observed_at, source="TikHub作者资料"):
    if not author_id or isinstance(followers, bool) or not isinstance(followers, int) or followers < 0 or not observed_at:
        return
    with store.connect() as c:
        c.execute("INSERT OR IGNORE INTO author_follower_history (author_id,observed_at,followers,source) VALUES (?,?,?,?)", (author_id, observed_at, followers, source))
        c.execute("INSERT INTO author_profiles VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=CASE WHEN excluded.name!='' THEN excluded.name ELSE author_profiles.name END,followers=excluded.followers,updated_at=excluded.updated_at WHERE excluded.updated_at>=author_profiles.updated_at", (author_id, name or "", followers, observed_at))
        c.execute("UPDATE videos SET followers=?,followers_at=? WHERE author_id=? AND (followers_at IS NULL OR followers_at<=?)", (followers, observed_at, author_id, observed_at))
        c.execute("UPDATE creators SET followers=?,updated_at=?,name=CASE WHEN ?!='' THEN ? ELSE name END WHERE id=? AND (updated_at IS NULL OR updated_at<=?)", (followers, observed_at, name or "", name or "", author_id, observed_at))


def recommended_authors_due(store, identities, limit=20):
    due = []
    seen = set()
    for identity in identities:
        video = store.one("SELECT author_id,followers,followers_at FROM videos WHERE id=?", (identity,))
        if not video or not video["author_id"] or video["author_id"] in seen:
            continue
        author_id = video["author_id"]
        seen.add(author_id)
        age = age_hours(video["followers_at"])
        attempted = store.one("SELECT attempted_at FROM author_profile_refreshes WHERE author_id=?", (author_id,))
        attempt_age = age_hours(attempted["attempted_at"]) if attempted else None
        if (video["followers"] is None or age is None or age >= 24) and (attempt_age is None or attempt_age >= 24):
            due.append(author_id)
            if len(due) >= limit:
                break
    return due


def author_profile(store, api, identity, *, force=False):
    """Share real 24h samples and failed-attempt cooldowns across all collectors."""
    cached = store.one('SELECT * FROM author_profiles WHERE id=?', (identity,))
    age = age_hours(cached['updated_at']) if cached else None
    if not force and cached and cached['followers'] is not None and age is not None and 0 <= age < 24:
        return {**cached, 'sampled_at': cached['updated_at'], 'cached': True}
    attempt = store.one('SELECT * FROM author_profile_refreshes WHERE author_id=?', (identity,))
    attempted_age = age_hours(attempt['attempted_at']) if attempt else None
    if not force and attempt and attempted_age is not None and 0 <= attempted_age < 24:
        if cached:
            return {**cached, 'sampled_at': cached['updated_at'], 'cached': True}
        raise TaskError('profile_cooldown', '该作者资料今天已尝试获取，暂不重复付费；粉丝数保留未知')
    store.run('INSERT OR REPLACE INTO author_profile_refreshes VALUES (?,?,NULL)', (identity, now()))
    try:
        profile = api.profile(identity)
        sampled_at = now()
        record_author_followers(store, identity, profile['name'], profile['followers'], sampled_at)
        return {**profile, 'sampled_at': sampled_at, 'cached': False}
    except TaskError as error:
        store.run('UPDATE author_profile_refreshes SET error_code=? WHERE author_id=?', (error.code, identity))
        raise


def record_recommendations(store, identities):
    """Record displayed samples locally; reopening a sample keeps its comparison."""
    result, author_context = {}, {}
    with store.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        for identity in dict.fromkeys(identities):
            video = c.execute("SELECT author_id,followers,followers_at FROM videos WHERE id=?", (identity,)).fetchone()
            if not video or not video["author_id"]:
                continue
            author_id, followers, observed_at = video["author_id"], video["followers"], video["followers_at"]
            if author_id in author_context:
                result[identity] = author_context[author_id]
                continue
            stamp = now()
            if not observed_at:
                followers = None
            key = hashlib.sha256(encode([author_id, observed_at or stamp[:10], followers]).encode()).hexdigest()
            c.execute("INSERT OR IGNORE INTO recommendation_records (record_key,author_id,video_id,shown_at,followers,followers_observed_at) VALUES (?,?,?,?,?,?)", (key, author_id, identity, stamp, followers, observed_at))
            current = c.execute("SELECT * FROM recommendation_records WHERE record_key=?", (key,)).fetchone()
            previous = c.execute("SELECT followers,shown_at,followers_observed_at FROM recommendation_records WHERE author_id=? AND id<? ORDER BY id DESC LIMIT 1", (author_id, current["id"])).fetchone()
            context = {"shown_at": current["shown_at"], "followers_observed_at": observed_at, "previous": dict(previous) if previous else None, "delta": followers - previous["followers"] if followers is not None and previous and previous["followers"] is not None else None}
            author_context[author_id] = context
            result[identity] = context
    return result


def follower_observation(store, video):
    """Compare actual author samples without fetching data or inventing past views."""
    current = {"followers": video.get("followers"), "observed_at": video.get("followers_at")}
    result = {"current": current, "previous": None, "delta": None, "days": None, "basis": None}
    author_id = video.get("author_id")
    if not author_id or current["followers"] is None or not current["observed_at"]:
        return result
    with store.connect() as c:
        # The same sample can be shown on many videos/devices. Compare to an
        # earlier observed sample, not another display of the same count.
        previous = c.execute("SELECT followers,followers_observed_at observed_at,shown_at FROM recommendation_records WHERE author_id=? AND followers IS NOT NULL AND followers_observed_at<? ORDER BY followers_observed_at DESC,id DESC LIMIT 1", (author_id, current["observed_at"])).fetchone()
        basis = "recommendation"
        if not previous:
            previous = c.execute("SELECT followers,observed_at FROM author_follower_history WHERE author_id=? AND observed_at<? ORDER BY observed_at DESC,id DESC LIMIT 1", (author_id, current["observed_at"])).fetchone()
            basis = "sample"
    if previous:
        try:
            seconds = (datetime.fromisoformat(current["observed_at"]) - datetime.fromisoformat(previous["observed_at"])).total_seconds()
        except (ValueError, TypeError):
            return result
        if seconds <= 0:
            return result
        result.update(previous=dict(previous), delta=current["followers"]-previous["followers"], days=round(seconds/86400,1), basis=basis)
    return result


def ingest(store: Store, video: dict, source="manual", source_key="", *, touch_source=True):
    identity = str(video["id"])
    existing = store.one("SELECT * FROM videos WHERE id=?", (identity,))
    # Adding a second source can provide only an ID; keep already-known metadata.
    record = {"id": identity, "title": video.get("title") or (existing or {}).get("title") or f"抖音作品 {identity}", "url": video.get("url") or (existing or {}).get("url") or f"https://www.douyin.com/video/{identity}", "discovered_at": now()}
    allowed = {"author", "author_id", "cover", "published_at", "collected_at", "duration", "media_kind", "followers", "followers_at", "likes", "comments", "favorites", "shares", "media_url", "subtitle_url"}
    record.update({k: video[k] for k in allowed if video.get(k) is not None})
    if video.get("followers") is not None and not video.get("followers_at"):
        record["followers_at"] = now()
    metrics = any(video.get(k) is not None for k in ("likes", "comments", "favorites", "shares"))
    if metrics:
        record["metrics_at"] = now()
    if not existing or not existing["manual_classification"] and existing["classification_method"].startswith("初步分类"):
        record.update(preliminary(record["title"]))
    if existing:
        record.pop("discovered_at")
        update = {k: v for k, v in record.items() if k != "id"}
        with store.connect() as c:
            c.execute("UPDATE videos SET " + ",".join(k + "=?" for k in update) + " WHERE id=?", [*update.values(), identity])
    else:
        with store.connect() as c:
            c.execute("INSERT INTO videos (" + ",".join(record) + ") VALUES (" + ",".join("?" for _ in record) + ")", list(record.values()))
    with store.connect() as c:
        if touch_source:
            c.execute("INSERT INTO sources VALUES (?,?,?,?) ON CONFLICT(video_id,kind,source_key) DO UPDATE SET observed_at=excluded.observed_at", (identity, source, source_key, now()))
        if metrics:
            c.execute("INSERT INTO snapshots (video_id,sampled_at,likes,comments,favorites,shares,followers) VALUES (?,?,?,?,?,?,?)", (identity, now(), *[video.get(k) for k in ["likes", "comments", "favorites", "shares", "followers"]]))
    return not bool(existing)


def unpack(row):
    if not row:
        return None
    for k in ("tools", "purposes", "hooks", "shooting_plan", "verification", "verified_items", "segments", "payload", "content_tracks", "brief", "reference_ids", "reference_analysis"):
        if k in row and isinstance(row[k], str):
            row[k] = json.loads(row[k])
    return row


def order_favorites(store, identities, *, append=False):
    """Store observed list order separately from unknown collection timestamps."""
    incoming = list(dict.fromkeys(identities))
    if not incoming:
        return
    with store.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        valid = {r[0] for r in c.execute("SELECT id FROM videos WHERE id IN (" + ",".join("?" for _ in incoming) + ")", incoming)}
        incoming = [identity for identity in incoming if identity in valid]
        previous = [r[0] for r in c.execute("SELECT video_id FROM favorite_order ORDER BY position")]
        ordered = list(dict.fromkeys(previous + incoming if append else incoming + previous))
        c.execute("DELETE FROM favorite_order")
        c.executemany("INSERT INTO favorite_order VALUES (?,?)", [(identity, index) for index, identity in enumerate(ordered)])


def age_hours(value):
    try:
        return max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(value)).total_seconds() / 3600)
    except (TypeError, ValueError):
        return None


PERSONAL_SCOPE_SQL = "EXISTS(SELECT 1 FROM sources personal WHERE personal.video_id=v.id AND personal.kind IN ('favorites','manual'))"
DISCOVERY_SCOPE_SQL = "EXISTS(SELECT 1 FROM sources discovery WHERE discovery.video_id=v.id AND discovery.kind IN ('creator','search','hotspot')) AND NOT (" + PERSONAL_SCOPE_SQL + ")"
RECOMMENDATION_SCOPE_SQL = "EXISTS(SELECT 1 FROM sources candidate WHERE candidate.video_id=v.id AND candidate.kind IN ('creator','search','hotspot','favorites','manual'))"


def ranked_videos(store: Store, *, query="", source="", category="", state="", ai_only=False, days=0, mode="recommended", tool="", purpose="", include_archived=False, scope="all", track="", collection_group="", collection_unconfirmed=False):
    domain=store.settings().get('creator_domain')
    clauses, args = ["1=1"], []
    if scope not in {"all", "personal", "discovery", "recommendations"}:
        raise TaskError("invalid_input", "素材范围无效")
    if scope == "personal":
        clauses.append(PERSONAL_SCOPE_SQL)
    if scope == "discovery":
        clauses.append(DISCOVERY_SCOPE_SQL)
    if scope == "recommendations":
        clauses.append(RECOMMENDATION_SCOPE_SQL)
        if domain is not None and not domain.get('configured'):return []
        ai_only, mode = not bool(domain), "low"
    if (collection_group or collection_unconfirmed) and scope != "personal":
        raise TaskError("invalid_input", "收藏分类无效")
    if collection_group:
        collection_group = normalize_collection_group(collection_group)
    if collection_unconfirmed:
        clauses.append("v.collection_group=''")
    if query:
        clauses.append("(v.title LIKE ? OR v.author LIKE ? OR v.tools LIKE ? OR v.notes LIKE ? OR EXISTS(SELECT 1 FROM transcripts t WHERE t.video_id=v.id AND t.text LIKE ?))")
        args.extend(["%" + query + "%"] * 5)
    if source:
        clauses.append("EXISTS(SELECT 1 FROM sources s WHERE s.video_id=v.id AND s.kind=?)")
        args.append(source)
    for key, value in [("category", category), ("state", state)]:
        if value:
            clauses.append(f"v.{key}=?")
            args.append(value)
    if not state and not include_archived:
        clauses.append("v.state!='已归档'")
    if ai_only:
        clauses.append("v.is_ai=1")
    for key, value in [("tools", tool), ("purposes", purpose)]:
        if value:
            clauses.append(f"EXISTS(SELECT 1 FROM json_each(v.{key}) WHERE value=?)")
            args.append(value)
    rows = store.rows("SELECT v.* FROM videos v WHERE " + " AND ".join(clauses) + " ORDER BY discovered_at DESC", args)
    snapshots_by_id, sources_by_id = {}, {}
    for snapshot in store.rows("SELECT * FROM snapshots WHERE likes IS NOT NULL ORDER BY id DESC"):
        bucket = snapshots_by_id.setdefault(snapshot["video_id"], [])
        if len(bucket) < 2:
            bucket.append(snapshot)
    for source_row in store.rows("SELECT video_id,kind,source_key,observed_at FROM sources"):
        sources_by_id.setdefault(source_row["video_id"], []).append(source_row)
    settings = store.settings()
    knowledge = {r["video_id"]: r for r in store.rows("SELECT video_id,priority,state FROM knowledge_items")}
    results = []
    for row in rows:
        if scope=='recommendations' and domain:
            from .onboarding import match_domain
            matches=match_domain(row,domain)
            if not matches:continue
            row['domain_matches']=matches
        row["content_tracks"] = tracks_for(row)
        if scope == "personal":
            row["collection_group_manual"] = bool(row["collection_group"])
            row["collection_group"] = collection_group_for(row)
            row["collection_group_label"] = collection_group_label(row["collection_group"])
            if collection_group and row["collection_group"] != collection_group:
                continue
        row["knowledge_priority"] = bool(knowledge.get(row["id"], {}).get("priority"))
        row["knowledge_state"] = knowledge.get(row["id"], {}).get("state", "indexed")
        if track and track not in row["content_tracks"]:
            continue
        age = age_hours(row["published_at"])
        if days and (age is None or age > days * 24):
            continue
        snapshots = snapshots_by_id.get(row["id"], [])
        row["growth_per_hour"] = None
        if len(snapshots) == 2:
            seconds = (datetime.fromisoformat(snapshots[0]["sampled_at"]) - datetime.fromisoformat(snapshots[1]["sampled_at"])).total_seconds()
            if seconds >= 60:
                row["growth_per_hour"] = max(0, snapshots[0]["likes"] - snapshots[1]["likes"]) / (seconds / 3600)
        follower_age = age_hours(row.get("followers_at"))
        row["low_follower_candidate"] = bool(age is not None and age <= 168 and follower_age is not None and follower_age <= 48 and row["followers"] is not None and 0 < row["followers"] <= settings["low_followers"] and row["likes"] is not None and row["likes"] >= settings["low_min_likes"] and row["likes"] / row["followers"] >= settings["low_ratio"])
        if mode == "low" and not row["low_follower_candidate"]:
            continue
        present = [(row[k], w) for k, w in [("likes", 1), ("comments", 2), ("favorites", 3), ("shares", 3)] if row[k] is not None]
        engagement = sum(math.log1p(v) * w for v, w in present) / sum(w for _, w in present) if present else None
        heat = min(100, engagement * 12) if engagement is not None else None
        if heat is not None and row["growth_per_hour"] is not None:
            heat = 0.6 * heat + 0.4 * min(100, math.log1p(row["growth_per_hour"]) * 16)
        parts = [(row["fit"], .4), (row["feasibility"], .25), (heat, .2), (max(0, 100 - age / 168 * 100) if age is not None else None, .15)]
        available = [(v, w) for v, w in parts if v is not None]
        row["score"] = round(sum(v * w for v, w in available) / sum(w for _, w in available)) if available else None
        row["score_preliminary"] = row["fit"] is None or row["feasibility"] is None
        row["heat"] = round(heat) if heat is not None else None
        row["sources"] = sources_by_id.get(row["id"], [])
        row.pop("media_url", None)
        row.pop("subtitle_url", None)
        results.append(unpack(row))
    if (source == "favorites" or scope == "personal") and mode == "recent":
        positions = {r["video_id"]: r["position"] for r in store.rows("SELECT * FROM favorite_order")}
        results.sort(key=lambda r: positions.get(r["id"], math.inf))
    elif mode == "hot":
        results.sort(key=lambda r: r["heat"] if r["heat"] is not None else -1, reverse=True)
    elif mode != "recent":
        results.sort(key=lambda r: r["score"] if r["score"] is not None else -1, reverse=True)
    return results


def detail(store, identity):
    row = store.one("SELECT * FROM videos WHERE id=?", (identity,))
    if not row:
        raise TaskError("not_found", "素材不存在")
    row = unpack(row)
    row.pop("media_url", None)
    row.pop("subtitle_url", None)
    row["sources"] = store.rows("SELECT kind,source_key,observed_at FROM sources WHERE video_id=?", (identity,))
    row["transcript"] = unpack(store.one("SELECT * FROM transcripts WHERE video_id=?", (identity,)))
    key = current_analysis_key(store, row) if row["transcript"] else None
    row["analysis"] = unpack(store.one("SELECT * FROM analyses WHERE cache_key=?", (key,))) if key else None
    row["analysis_stale"] = not row["analysis"] and bool(store.one("SELECT cache_key FROM analyses WHERE video_id=? AND kind='analysis' LIMIT 1", (identity,)))
    row["previous_analysis"] = unpack(store.one("SELECT * FROM analyses WHERE video_id=? AND kind='analysis' ORDER BY created_at DESC LIMIT 1", (identity,))) if row["analysis_stale"] else None
    row["snapshots"] = store.rows("SELECT sampled_at,likes,comments,favorites,shares,followers FROM snapshots WHERE video_id=? ORDER BY id DESC LIMIT 30", (identity,))
    row["follower_history"] = store.rows("SELECT observed_at,followers,source FROM author_follower_history WHERE author_id=? ORDER BY observed_at DESC,id DESC LIMIT 60", (row.get("author_id"),))
    row["recommendation_history"] = store.rows("SELECT shown_at,followers,followers_observed_at FROM recommendation_records WHERE author_id=? ORDER BY id DESC LIMIT 60", (row.get("author_id"),))
    row["content_tracks"] = tracks_for(row)
    if any(source["kind"] in {"favorites", "manual"} for source in row["sources"]):
        row["collection_group_manual"] = bool(row["collection_group"])
        row["collection_group"] = collection_group_for(row)
        row["collection_group_label"] = collection_group_label(row["collection_group"])
    row["knowledge"] = unpack(store.one("SELECT * FROM knowledge_items WHERE video_id=?", (identity,)))
    visual = store.one("SELECT payload FROM analyses WHERE video_id=? AND kind='visual_analysis' ORDER BY created_at DESC LIMIT 1", (identity,))
    row["visual_analysis"] = json.loads(visual["payload"]) if visual else None
    return row


def current_analysis_key(store, video):
    s = store.settings()
    content = encode({"title": video["title"], "author": video["author"], "transcript": video["transcript"]["text"], "profile": s["profile"]})
    return cache_key(video["id"], "analysis", content, s["model"])


def save_transcript(store, identity, text, segments=None, source="手动提供", *, overwrite=True):
    if not store.one("SELECT id FROM videos WHERE id=?", (identity,)):
        raise TaskError("not_found", "素材不存在")
    h = hashlib.sha256(text.encode()).hexdigest()
    conflict = "DO UPDATE SET text=excluded.text,segments=excluded.segments,source=excluded.source,content_hash=excluded.content_hash,updated_at=excluded.updated_at" if overwrite else "DO NOTHING"
    return store.run("INSERT INTO transcripts VALUES (?,?,?,?,?,?) ON CONFLICT(video_id) " + conflict, (identity, text, encode(segments or []), source, h, now()))


def classify(store: Store, identities: list[str]):
    rows = [r for i in identities if (r := store.one("SELECT * FROM videos WHERE id=?", (i,))) and not r["manual_classification"]]
    if not rows:
        return {"classified": 0}
    s = store.settings()
    transcripts = {r["id"]: (store.one("SELECT text FROM transcripts WHERE video_id=?", (r["id"],)) or {}).get("text", "") for r in rows}
    instruction = "输出格式示例：{\"items\":[{\"id\":\"123456789\",\"is_ai\":true,\"category\":\"Agent工作流\",\"tools\":[\"Codex\"],\"purposes\":[\"选题候选\"],\"confidence\":0.8,\"fit\":80,\"feasibility\":70,\"reason\":\"适合小白，可演示实际结果；需准备测试账号\",\"topic_key\":\"Codex搭知识库\"}]}。category只能取给定分类之一，purposes只能取给定用途，fit与feasibility为0到100，confidence为0到1。非AI内容is_ai=false；材料不足时降低置信度。"
    result = DeepSeek(store).json("classification", {"categories": s["categories"], "purposes": s["purposes"], "items": [{"id": r["id"], "title": r["title"][:600], "author": r["author"], "transcript": transcripts[r["id"]][:(12000 if len(rows) == 1 else 1000)]} for r in rows]}, instruction, 3500)
    items = result.get("items")
    if not isinstance(items, list):
        raise TaskError("invalid_response", "分类结果缺少有效items列表")
    count = 0
    expected = {r["id"] for r in rows}
    for item in items:
        if not isinstance(item, dict) or item.get("id") not in expected or item.get("category") not in s["categories"] or not isinstance(item.get("is_ai"), bool):
            continue
        tools, purposes = item.get("tools", []), item.get("purposes", [])
        if not isinstance(tools, list) or not all(isinstance(x, str) for x in tools) or not isinstance(purposes, list):
            continue
        def score(key, ceiling):
            v = item.get(key)
            return min(ceiling, max(0, float(v))) if isinstance(v, (int, float)) and math.isfinite(v) else None
        store.run("UPDATE videos SET is_ai=?,category=?,tools=?,purposes=?,confidence=?,fit=?,feasibility=?,recommendation=?,topic_key=?,classification_method=? WHERE id=? AND manual_classification=0", (int(item["is_ai"]), item["category"], encode(tools[:15]), encode([p for p in purposes if p in s["purposes"]]), score("confidence", 1), score("fit", 100), score("feasibility", 100), str(item.get("reason", ""))[:2000], str(item.get("topic_key") or item["category"])[:200], "AI全文分类" if transcripts[item["id"]] else "AI初步分类·标题与简介", item["id"]))
        count += 1
    if not count:
        raise TaskError("invalid_response", "分类结果没有通过字段校验，原分类已保留")
    return {"classified": count}


def analyze(store, identity, *, force=False):
    video = detail(store, identity)
    transcript = video["transcript"]
    if not transcript or not transcript["text"].strip():
        raise TaskError("transcript_required", "请先提取或粘贴逐字稿，再进行深度拆解")
    model = store.settings()["model"]
    key = current_analysis_key(store, video)
    cached = store.one("SELECT payload FROM analyses WHERE cache_key=?", (key,))
    if cached and not force:
        return {"cached": True, "analysis": json.loads(cached["payload"])}
    instruction = '输出json格式：{"summary":"核心内容","audience":"目标观众","angle":"适合本账号的切入角度","hook":"原开头及其承诺","structure":[{"quote":"原句短摘","role":"开头/承诺/论证/步骤/结尾","reason":"作用及可复用结构"}],"opportunities":["推断，非已证实因果"],"requirements":["演示工具与资源"],"verification":["需要核验的事实或版本"]}。不得输出素材没有提供的完播率、播放量或效果。'
    instruction += '可附 steps 字段，列出原文明确演示的操作步骤；缺少步骤时填空数组，不自行补造。'
    result = DeepSeek(store).json("analysis", {"title": video["title"], "author": video["author"], "transcript": transcript["text"]}, instruction)
    try:
        result = AnalysisOutput.model_validate(result).model_dump()
    except ValueError:
        raise TaskError("invalid_response", "拆解结果字段不完整，未保存为有效分析")
    store.run("INSERT OR REPLACE INTO analyses VALUES (?,?,?,?,?,?,?)", (key, identity, "analysis", model, PROMPT_VERSION, encode(result), now()))
    return {"cached": False, "analysis": result}


def project(store, identity):
    p = unpack(store.one("SELECT * FROM projects WHERE id=?", (identity,)))
    if not p:
        raise TaskError("not_found", "创作项目不存在")
    p["video"] = detail(store, p["video_id"]) if p["video_id"] else None
    p["versions"] = store.rows("SELECT revision,reason,created_at FROM project_versions WHERE project_id=? ORDER BY revision DESC", (identity,))
    p["references"] = [store.one("SELECT id,title,author,url FROM videos WHERE id=?", (video_id,)) for video_id in p["reference_ids"]]
    return p


def create_project(store, video_id, creation_mode="tutorial", title=""):
    v = store.one("SELECT * FROM videos WHERE id=?", (video_id,)) if video_id else None
    if creation_mode not in {"tutorial", "commercial"} or not v and creation_mode != "commercial":
        raise TaskError("not_found", "素材不存在")
    identity = uuid.uuid4().hex
    with store.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        previous = c.execute("SELECT id FROM projects WHERE video_id=? AND creation_mode=? AND stage!='已定稿' ORDER BY updated_at DESC LIMIT 1", (video_id, creation_mode)).fetchone() if video_id else None
        if previous:
            return previous["id"]
        c.execute("INSERT INTO projects (id,video_id,title,creation_mode,reference_ids,created_at,updated_at) VALUES (?,?,?,?,?,?,?)", (identity, video_id or None, title or (v["title"] if v else "新的AIGC商单"), creation_mode, encode([video_id] if video_id and creation_mode == "commercial" else []), now(), now()))
        row = dict(c.execute("SELECT * FROM projects WHERE id=?", (identity,)).fetchone())
        c.execute("INSERT INTO project_versions (project_id,revision,payload,reason,created_at) VALUES (?,?,?,?,?)", (identity, 1, encode(row), "创建项目", now()))
        c.execute("UPDATE videos SET state='已选题' WHERE id=?", (video_id,))
    return identity


def update_project(store, identity, revision, changes, reason="手动编辑", *, job_id=None):
    allowed = {"title", "duration", "selected_hook", "content", "script_kind", "stage", "hooks", "shooting_plan", "verification", "verified_items", "creation_mode", "brief", "reference_ids", "reference_analysis"}
    changes = {k: v for k, v in changes.items() if k in allowed}
    with store.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        if job_id:
            task = c.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if task and task["status"] == "cancelled":
                raise TaskError("cancelled", "任务已取消，生成结果已保留但未应用")
        row = c.execute("SELECT * FROM projects WHERE id=?", (identity,)).fetchone()
        if not row:
            raise TaskError("not_found", "项目不存在")
        if row["revision"] != revision:
            raise TaskError("revision_conflict", "另一台设备或任务已更新此稿。你的输入已保留，请重新载入或复制内容后合并")
        encoded = {k: encode(v) if k in {"hooks", "shooting_plan", "verification", "verified_items", "brief", "reference_ids", "reference_analysis"} else v for k, v in changes.items()}
        encoded.update(revision=revision + 1, updated_at=now())
        c.execute("UPDATE projects SET " + ",".join(k + "=?" for k in encoded) + " WHERE id=?", [*encoded.values(), identity])
        updated = dict(c.execute("SELECT * FROM projects WHERE id=?", (identity,)).fetchone())
        c.execute("INSERT INTO project_versions (project_id,revision,payload,reason,created_at) VALUES (?,?,?,?,?)", (identity, revision + 1, encode(updated), reason, now()))
    return unpack(updated)


def generate_project(store, identity, kind, base_revision, *, job_id=None):
    p = project(store, identity)
    if p["revision"] != base_revision:
        raise TaskError("revision_conflict", "稿件已改变，请用最新版本重新生成")
    commercial = p["creation_mode"] == "commercial"
    transcript = (p["video"] or {}).get("transcript")
    if not transcript and not commercial:
        raise TaskError("transcript_required", "先在素材详情中提取或粘贴逐字稿，再生成开头")
    if commercial and not str(p["brief"].get("product", "")).strip():
        raise TaskError("brief_required", "请先填写商单产品或平台及目标，再生成")
    if commercial and p["reference_ids"] and not p["reference_analysis"]:
        raise TaskError("reference_analysis_required", "先拆解已引用的作品，再把分析用于本次商单")
    source = {"title": p["title"], "duration_seconds": p["duration"], "transcript": transcript["text"] if transcript else "未提供原始口播；不能补造", "selected_hook": p["selected_hook"], "script_kind": p["script_kind"]}
    source["selected_hook_plan"] = next((hook for hook in p["hooks"] if hook.get("text") == p["selected_hook"]), None)
    if commercial:
        source.update(brief=p["brief"], reference_analysis=p["reference_analysis"], creation_mode="AIGC商单；具体受众与表现形式以brief为准")
    if kind == "hooks":
        instruction = '只写3个不同机制的前5秒口播，每个不超过35个汉字。输出json：{"hooks":[{"text":"开头口播","mechanism":"结果先行/痛点/反差","visual":"需要展示的真实画面","promise":"正文需要兑现的承诺"}]}。不要生成正文。'
        if commercial:
            instruction = '为本次商单给3个不同机制的前5秒方案，可以纯视觉、动画或口播，不强制真人。输出json：{"hooks":[{"text":"方案的一句话开头/纯视觉方案可用画面说明","mechanism":"钩子机制","visual":"0—5秒具体画面推进","audio":"建议声音设计，不能冒充参考原声","screen_text":"屏幕文字","promise":"正文需兑现的产品价值"}]}。借鉴参考结构而不是照搬其产品事实，不虚构平台已支持的功能。'
        result = DeepSeek(store).json("hooks", source, instruction, 1600)
        try:
            result = HooksOutput.model_validate(result).model_dump()
        except ValueError:
            raise TaskError("invalid_response", "模型未给出3个有效开头，原稿已保留")
        hooks = result.get("hooks")
        if not isinstance(hooks, list) or len(hooks) != 3 or not all(isinstance(h, dict) and isinstance(h.get("text"), str) and h["text"].strip() for h in hooks):
            raise TaskError("invalid_response", "模型未给出3个有效开头，未覆盖已有稿件")
        changes = {"hooks": hooks, "stage": "选开头"}
    else:
        if not p["selected_hook"].strip():
            raise TaskError("hook_required", "请先选定或写好前5秒开头，再生成正文")
        instruction = '保留用户已确定的开头，为真人口播加真实录屏撰写1—3分钟中文教程。script_kind为大纲则写大纲，否则写完整可口播正文。输出json：{"content":"可编辑Markdown正文，第一段必须是确定的开头","shooting_plan":[{"segment":"对应段落","speech":"口播重点","screen":"实际录屏操作","evidence":"要看到的真实结果"}],"verification":["拍摄前需验证的事实"]}。不能只有纯口播或纯录屏；不要把未经证实的步骤写成亲测成功。'
        if commercial:
            instruction = '以brief为准写完整AIGC商单脚本或大纲及分镜，可纯动画或混合，不强制真人口播或录屏。保留确定的开头方案，卖点自然进入创意。输出json：{"content":"含时间段的可编辑商单脚本，首段保留确定的开头","shooting_plan":[{"segment":"段落","time":"时间段","speech":"旁白或无旁白","screen":"屏幕文字/操作","visual":"画面构图与动作","audio":"建议声音设计","tool_point":"本镜头体现的卖点","asset_method":"真实录屏/生成/动画及素材需求","evidence":"需实测的产品能力或镜头结果"}],"verification":["产品事实、必提禁用词与素材缺口"]}。无法验证的功能标待核实，不伪造亲测、甲方认可或已生成的成片。'
        result = DeepSeek(store).json("script", source, instruction, 4200)
        try:
            result = ScriptOutput.model_validate(result).model_dump()
        except ValueError:
            raise TaskError("invalid_response", "模型返回的正文或拍摄清单格式不完整，原稿已保留")
        if not isinstance(result.get("content"), str) or not result["content"].strip() or not isinstance(result.get("shooting_plan"), list) or not isinstance(result.get("verification", []), list):
            raise TaskError("invalid_response", "脚本缺少正文或录屏清单，未覆盖已有稿件")
        content = result["content"]
        if not content.lstrip().startswith(p["selected_hook"]):
            content = p["selected_hook"] + "\n\n" + content
        changes = {"content": content, "shooting_plan": result["shooting_plan"], "verification": result.get("verification", []), "verified_items": [], "stage": "待确认脚本"}
    # Persist the paid result before optimistic commit; a conflict must never discard it.
    key = cache_key(p["video_id"], kind, encode(source), store.settings()["model"])
    if p["video_id"]:
        store.run("INSERT OR REPLACE INTO analyses VALUES (?,?,?,?,?,?,?)", (key, p["video_id"], kind, store.settings()["model"], PROMPT_VERSION, encode(result), now()))
    try:
        updated = update_project(store, identity, base_revision, changes, "AI生成开头" if kind == "hooks" else "AI生成正文", job_id=job_id)
    except TaskError as e:
        if e.code in {"revision_conflict", "cancelled"}:
            return {"conflict": e.code == "revision_conflict", "cancelled": e.code == "cancelled", "message": "稿件已改变或任务已取消；生成结果已保存在此任务中，未覆盖新稿", "generated": changes, "project_id": identity}
        raise
    return {"project_id": identity, "revision": updated["revision"], "generated": changes}


def backup(store: Store):
    folder = store.root / "backups"
    folder.mkdir(exist_ok=True)
    name = datetime.now(timezone.utc).strftime("workbench-%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4] + ".sqlite3"
    dest = sqlite3.connect(folder / name)
    try:
        with store.connect() as src:
            src.backup(dest)
    finally:
        dest.close()
    return {"name": name, "size": (folder / name).stat().st_size}


def restore(store: Store, name: str):
    if Path(name).name != name or not name.startswith("workbench-") or not name.endswith(".sqlite3"):
        raise TaskError("invalid_backup", "备份文件名无效")
    source = store.root / "backups" / name
    if not source.is_file():
        raise TaskError("not_found", "备份不存在")
    if store.one("SELECT id FROM jobs WHERE status IN ('running','queued')"):
        raise TaskError("jobs_active", "请先等待或取消所有任务，再恢复备份")
    c = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    try:
        if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or c.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise TaskError("invalid_backup", "备份完整性或版本检查未通过")
        required = {"videos", "settings", "auth", "projects", "expenses", "jobs"}
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not required <= tables:
            raise TaskError("invalid_backup", "这不是有效的工作台备份")
        # Charges and credentials are runtime state: restoring content cannot undo spending.
        current_expenses = store.rows("SELECT * FROM expenses")
        current_auth = store.rows("SELECT * FROM auth")
        current_secrets = store.rows("SELECT * FROM secrets")
        backup(store)
        with store.connect() as target:
            c.backup(target)
        Store(store.root)  # Apply supported additive migrations to older v1 snapshots.
        with store.connect() as target:
            for expense in current_expenses:
                previous = target.execute("SELECT COALESCE(actual,reserved) amount FROM expenses WHERE id=?", (expense["id"],)).fetchone()
                current_amount = expense["actual"] if expense["actual"] is not None else expense["reserved"]
                if previous and previous["amount"] > current_amount:
                    continue
                target.execute("INSERT OR REPLACE INTO expenses (" + ",".join(expense) + ") VALUES (" + ",".join("?" for _ in expense) + ")", list(expense.values()))
            target.execute("DELETE FROM auth")
            target.execute("DELETE FROM secrets")
            for row in current_auth:
                target.execute("INSERT INTO auth VALUES (?,?)", (row["id"], row["password_hash"]))
            for row in current_secrets:
                target.execute("INSERT INTO secrets VALUES (?,?)", (row["name"], row["value"]))
        store.run("DELETE FROM sessions")
        store.run("DELETE FROM browser_bridge")
        store.run("DELETE FROM browser_pairing")
        store.set("data_connection_state", {"status": "unverified", "updated_at": now()})
        store.run("UPDATE browser_requests SET state='cancelled' WHERE state IN ('pending','claimed')")
        store.run("UPDATE jobs SET status='cancelled',message='从备份恢复后取消旧任务' WHERE status IN ('queued','running')")
        return {"restored": name, "login_required": True}
    finally:
        c.close()
