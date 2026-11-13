from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import math
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, unquote

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .db import Store, data_directory, encode, now
from .edge_bridge import EdgeBridge, PairBody, ResultBody, ORIGIN
from .jobs import JobEngine
from .providers import DeepSeek, TaskError, parse_douyin_url, SHANGHAI
from .security import create_session, get_secret, password_hash, read_session, save_secret, verify_password, SecretStorageError
from . import services
from .phone_access import PhoneAccess


class PasswordBody(BaseModel):
    password: str = Field(min_length=8, max_length=256)
    setup_token: str = ""


class LinkBody(BaseModel):
    url: str = Field(min_length=5, max_length=3000)
    title: str = Field(default="", max_length=4000)


class RecommendationBody(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)
    refresh_profiles: bool = True


class JobBody(BaseModel):
    kind: str
    payload: dict = Field(default_factory=dict)


class TranscriptBody(BaseModel):
    text: str = Field(min_length=1, max_length=100000)


class ProjectBody(BaseModel):
    video_id: str | None = None
    creation_mode: str = "tutorial"
    title: str = Field(default="", max_length=2000)


class ProjectEdit(BaseModel):
    revision: int = Field(ge=1)
    changes: dict


class SecretBody(BaseModel):
    value: str = Field(max_length=512)


def create_app(directory: Path | None = None, *, run_jobs=True, phone_port=8765):
    store = Store(directory or data_directory())
    engine = JobEngine(store)
    phone = PhoneAccess(store.root, phone_port)
    bootstrap_file = store.root / "initial-setup-code.txt"
    setup_token = bootstrap_file.read_text(encoding="utf-8").strip() if bootstrap_file.exists() else secrets.token_urlsafe(24)
    if not store.one("SELECT id FROM auth"):
        bootstrap_file.write_text(setup_token, encoding="utf-8")

    @asynccontextmanager
    async def lifespan(app):
        if run_jobs:
            engine.start()
        yield
        phone.stop()
        engine.close()

    app = FastAPI(title="AI 创作台", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.engine = store, engine
    app.state.phone = phone
    login_attempts: dict[str, list[float]] = {}

    @app.exception_handler(TaskError)
    async def task_error(request, exc):
        status = 409 if exc.code == "revision_conflict" else 404 if exc.code == "not_found" else 400
        return JSONResponse({"code": exc.code, "message": exc.message}, status_code=status)

    @app.exception_handler(SecretStorageError)
    async def credential_error(request, exc):
        return JSONResponse({"code": "credential_storage_unavailable", "message": str(exc)}, status_code=503)

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        host = request.url.hostname or ""
        settings = store.settings()
        remote = urlparse(settings["remote_access"].get("url", "")).hostname
        public_phone = phone.accepts(host)
        request.state.public_phone = public_phone
        if host not in {"localhost", "127.0.0.1", "[::1]", "::1", "testserver", remote} and not public_phone:
            return JSONResponse({"message": "此主机名未获准，请使用本机地址或已配置的私有地址"}, status_code=403)
        path = request.url.path
        if public_phone and (path.startswith(("/api/edge/", "/api/secrets/", "/api/backups", "/api/connections/", "/api/system")) or path == "/api/auth/setup" or path == "/api/settings" and request.method != "GET" or path.startswith("/api/phone/") and path != "/api/phone/status"):
            return JSONResponse({"message": "这项设置请在电脑工作台操作"}, status_code=403)
        bridge_path = request.url.path.startswith("/api/edge/bridge/")
        bridge_origin = request.headers.get("origin", "") if bridge_path else ""
        if bridge_path:
            if host not in {"localhost", "127.0.0.1", "testserver"}:
                return JSONResponse({"message": "Edge连接接口仅供电脑本机使用"}, status_code=403)
            match = ORIGIN.fullmatch(bridge_origin) if bridge_origin else None
            registered = store.one("SELECT extension_id FROM browser_bridge WHERE id=1")
            origin_ok = bool(match and (request.url.path.endswith("/pair") or registered and registered["extension_id"] == match[1]))
            if bridge_origin and not origin_ok:
                return JSONResponse({"message": "未获准的浏览器扩展来源"}, status_code=403)
            if request.method == "OPTIONS":
                if not origin_ok:
                    return Response(status_code=403)
                return Response(status_code=204, headers={"Access-Control-Allow-Origin": bridge_origin, "Access-Control-Allow-Methods": "GET,POST,OPTIONS", "Access-Control-Allow-Headers": "Authorization,Content-Type", "Vary": "Origin"})
            length = request.headers.get("content-length", "")
            if length.isdigit() and int(length) > 3_000_000:
                return Response(status_code=413)
        if not bridge_path and request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin:
                o = urlparse(origin)
                if o.netloc != request.headers.get("host") or o.scheme not in {"http", "https"}:
                    return JSONResponse({"message": "拒绝跨站请求"}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"message": "拒绝跨站请求"}, status_code=403)
        response = await call_next(request)
        if bridge_origin:
            response.headers["Access-Control-Allow-Origin"] = bridge_origin
            response.headers["Vary"] = "Origin"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' https: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; font-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def require_auth(request: Request):
        session = read_session(store, request.cookies.get("aw_session", ""))
        if not session:
            from fastapi import HTTPException
            raise HTTPException(401, "请先登录工作台")
        if request.method not in {"GET", "HEAD"} and not secrets.compare_digest(request.headers.get("x-csrf-token", ""), session["csrf"]):
            from fastapi import HTTPException
            raise HTTPException(403, "会话校验失败，请重新登录")
        if request.state.public_phone:
            phone.last_visit = now()
        return session

    def auth_response(request):
        token, csrf = create_session(store)
        response = JSONResponse({"authenticated": True, "csrf": csrf})
        response.set_cookie("aw_session", token, httponly=True, samesite="strict", secure=request.url.scheme == "https" or request.state.public_phone, max_age=14 * 86400, path="/")
        return response

    @app.get("/api/health")
    def health():
        return {"status": "ok", "app": "AIWorkbench", "version": "1.0.0", "instance_id":hashlib.sha256(str(store.root.resolve()).encode()).hexdigest()[:16]}

    @app.get('/api/discovery', dependencies=[Depends(require_auth)])
    def discovery_feed(mode: str='focus', limit: int=10, offset: int=0, exclude_ids: str='', q: str='', track: str='', sort: str='saved', hours: int=72, hot_min_likes: int | None=None):
        from .discovery import feed
        excluded=exclude_ids.split(',') if exclude_ids else []
        if len(excluded)>200:raise TaskError('invalid_input','每次换批最多排除200条')
        return feed(store,mode=mode,limit=max(1,min(limit,60)),offset=max(0,offset),exclude_ids=excluded,query=q[:200],track=track[:100],sort=sort,hours=hours,hot_min_likes=hot_min_likes)

    @app.post('/api/discovery/{identity}/feedback', dependencies=[Depends(require_auth)])
    def discovery_feedback(identity: str, body: dict):
        from .discovery import feedback
        if set(body)-{'action','reason'}:raise TaskError('invalid_input','反馈字段无效')
        return feedback(store,identity,body.get('action'),body.get('reason',''))

    @app.post('/api/discovery/{identity}/follow-author', dependencies=[Depends(require_auth)])
    def discovery_follow(identity: str):
        from .discovery import follow_author
        return follow_author(store,identity)

    @app.get("/api/auth/status")
    def auth_status(request: Request):
        session = read_session(store, request.cookies.get("aw_session", ""))
        return {"configured": bool(store.one("SELECT id FROM auth")), "authenticated": bool(session), "csrf": session["csrf"] if session else None, "public_access": request.state.public_phone}

    @app.post("/api/auth/setup")
    def auth_setup(body: PasswordBody, request: Request):
        if store.one("SELECT id FROM auth"):
            raise TaskError("already_configured", "已完成初始设置，请登录")
        if not secrets.compare_digest(body.setup_token, setup_token):
            raise TaskError("invalid_setup", "初始设置码不正确，请从电脑上的初始设置码文件获取")
        with store.connect() as c:
            try:
                c.execute("INSERT INTO auth VALUES (1,?)", (password_hash(body.password),))
            except sqlite3.IntegrityError:
                raise TaskError("already_configured", "初始设置已完成")
        if bootstrap_file.exists():
            bootstrap_file.unlink()
        return auth_response(request)

    @app.post("/api/auth/login")
    def login(body: PasswordBody, request: Request):
        ip = "public-phone" if request.state.public_phone else request.client.host if request.client else "unknown"
        attempts = login_attempts.setdefault(ip, [])
        attempts[:] = [t for t in attempts if t > time.time() - 300]
        if len(attempts) >= 10:
            raise TaskError("rate_limited", "尝试过于频繁，请5分钟后重试")
        attempts.append(time.time())
        auth = store.one("SELECT password_hash FROM auth WHERE id=1")
        if not auth or not verify_password(body.password, auth["password_hash"]):
            raise TaskError("invalid_password", "密码不正确")
        attempts.clear()
        if request.state.public_phone:
            phone.last_visit = now()
        return auth_response(request)

    @app.get("/api/phone/status", dependencies=[Depends(require_auth)])
    def phone_status(request: Request):
        if request.state.public_phone:
            return {"state": "ready", "message": "已通过扫码入口连接电脑", "url": ""}
        return phone.status()

    @app.post("/api/phone/start", dependencies=[Depends(require_auth)])
    def phone_start(request: Request):
        if request.url.hostname not in {"localhost", "127.0.0.1", "::1", "testserver"}:
            raise TaskError("local_only", "请在电脑工作台开启扫码连接")
        if not store.one("SELECT id FROM auth"):
            raise TaskError("password_required", "请先设置工作台密码")
        return phone.start()

    @app.post("/api/phone/stop", dependencies=[Depends(require_auth)])
    def phone_stop(request: Request):
        if request.url.hostname not in {"localhost", "127.0.0.1", "::1", "testserver"}:
            raise TaskError("local_only", "请在电脑工作台关闭扫码连接")
        return phone.stop()

    @app.post("/api/auth/logout", dependencies=[Depends(require_auth)])
    def logout(request: Request):
        token_hash = hashlib.sha256(request.cookies["aw_session"].encode()).hexdigest()
        store.run("DELETE FROM sessions WHERE token_hash=?", (token_hash,))
        r = JSONResponse({"ok": True})
        r.delete_cookie("aw_session")
        return r

    @app.get("/api/overview", dependencies=[Depends(require_auth)])
    def overview():
        counts = store.one("SELECT COUNT(*) AS materials, SUM(CASE WHEN state='备选' THEN 1 ELSE 0 END) AS shortlisted, SUM(CASE WHEN is_ai=1 THEN 1 ELSE 0 END) AS ai FROM videos")
        counts["favorites"] = store.one("SELECT COUNT(DISTINCT video_id) n FROM sources WHERE kind='favorites'")["n"]
        counts["projects"] = store.one("SELECT COUNT(*) n FROM projects")["n"]
        counts["creators"] = store.one("SELECT COUNT(*) n FROM creators WHERE enabled=1")["n"]
        counts["unclassified"] = store.one("SELECT COUNT(*) n FROM videos WHERE classification_method LIKE '初步分类%' AND manual_classification=0")["n"]
        counts["personal_materials"] = store.one("SELECT COUNT(*) n FROM videos v WHERE " + services.PERSONAL_SCOPE_SQL)["n"]
        counts["discovery_materials"] = store.one("SELECT COUNT(*) n FROM videos v WHERE " + services.DISCOVERY_SCOPE_SQL)["n"]
        counts["personal_unclassified"] = store.one("SELECT COUNT(*) n FROM videos v WHERE classification_method LIKE '初步分类%' AND manual_classification=0 AND " + services.PERSONAL_SCOPE_SQL)["n"]
        counts["personal_shortlisted"] = store.one("SELECT COUNT(*) n FROM videos v WHERE state='备选' AND " + services.PERSONAL_SCOPE_SQL)["n"]
        counts["personal_projects"] = store.one("SELECT COUNT(*) n FROM projects p JOIN videos v ON v.id=p.video_id WHERE " + services.PERSONAL_SCOPE_SQL)["n"]
        counts["discovery_projects"] = store.one("SELECT COUNT(*) n FROM projects p JOIN videos v ON v.id=p.video_id WHERE " + services.DISCOVERY_SCOPE_SQL)["n"]
        settings = store.settings()
        month = datetime.now(SHANGHAI).strftime("%Y-%m")
        expenses = store.rows("SELECT provider,COUNT(*) calls,SUM(COALESCE(actual,reserved)) used,SUM(CASE WHEN status IN ('reserved','unknown') THEN COALESCE(actual,reserved) ELSE 0 END) pending FROM expenses WHERE month=? AND status!='released' GROUP BY provider", (month,))
        return {"counts": counts, "last_sync": settings["last_sync"], "collection_state": settings["collection_state"], "expenses": expenses, "budget": settings["budgets"], "automation": settings["automation"], "configured": {name: bool(get_secret(store, name)) for name in ["deepseek", "tikhub"]}, "pricing_confirmed": settings["pricing"]["confirmed"], "jobs": store.rows("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 8"), "server_time": now()}

    @app.get("/api/videos", dependencies=[Depends(require_auth)])
    def videos(q: str = "", source: str = "", category: str = "", state: str = "", ai_only: bool = False, days: int = 0, mode: str = "recommended", tool: str = "", purpose: str = "", offset: int = 0, limit: int = 60, scope: str = "", track: str = "", collection_group: str = "", collection_unconfirmed: bool = False):
        scope = scope or ("personal" if source == "favorites" else "recommendations")
        rows = services.ranked_videos(store, query=q[:200], source=source, category=category, state=state, ai_only=ai_only, days=max(0, days), mode=mode, tool=tool, purpose=purpose, scope=scope, track=track, collection_group=collection_group, collection_unconfirmed=collection_unconfirmed)
        return {"items": rows[max(0, offset):max(0, offset) + min(200, max(1, limit))], "total": len(rows)}

    @app.post("/api/recommendations/record", dependencies=[Depends(require_auth)])
    def record_recommendations(body: RecommendationBody):
        eligible = {row['id'] for row in store.rows("SELECT v.id FROM videos v WHERE " + services.RECOMMENDATION_SCOPE_SQL)}
        identities = [identity for identity in body.ids if identity in eligible]
        records = services.record_recommendations(store, identities)
        settings = store.settings()
        due = services.recommended_authors_due(store, identities)
        job_id = None
        if body.refresh_profiles and due and get_secret(store, "tikhub") and settings["pricing"]["confirmed"] and settings["data_pricing_confirmed"]:
            job_id = engine.enqueue("author_profiles_refresh", {"author_ids": due}, "recommended-author-profiles")
        return {"records": records, "profile_refresh_job": job_id}

    @app.post("/api/recommendations/record-local", dependencies=[Depends(require_auth)])
    def record_local_recommendations(body: RecommendationBody):
        return record_recommendations(body.model_copy(update={"refresh_profiles":False}))

    @app.get("/api/videos/{identity}", dependencies=[Depends(require_auth)])
    def video(identity: str):
        return services.detail(store, identity)

    @app.get('/api/videos/{identity}/cover', dependencies=[Depends(require_auth)])
    def video_cover(identity: str):
        from .covers import cached_cover
        path,mime=cached_cover(store,identity)
        return FileResponse(path,media_type=mime,headers={'Cache-Control':'private,no-cache'})

    @app.get("/api/videos/{identity}/frames/{index}", dependencies=[Depends(require_auth)])
    def frame(identity: str, index: int):
        if not identity.isdigit() or not 0 <= index < 12:
            raise TaskError("not_found", "采样画面不存在")
        path = store.root / "cache" / "visual" / identity / f"{index}.jpg"
        if not path.is_file(): raise TaskError("not_found", "采样画面不存在或已清理，请重新拆解")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private,no-cache"})

    @app.post("/api/import", dependencies=[Depends(require_auth)])
    def import_link(body: LinkBody):
        kind, identity, url = parse_douyin_url(body.url)
        if kind != "video":
            raise TaskError("invalid_url", "这是作者主页，请在「我的 → 对标博主」添加")
        services.ingest(store, {"id": identity, "url": url, "title": body.title or f"待补全作品 {identity}"}, "manual", "link")
        job_id = engine.enqueue("video_refresh", {"video_id": identity}, "video:" + identity) if get_secret(store, "tikhub") else None
        return {"video_id": identity, "job_id": job_id, "message": "已加入素材库" if job_id else "已保存链接；配置TikHub后可补全详情，也可手动编辑标题与逐字稿"}

    @app.get("/api/collection-groups", dependencies=[Depends(require_auth)])
    def collection_groups():
        options = dict(services.COLLECTION_GROUP_LABELS)
        settings=store.settings()
        if settings.get('creator_domain'):
            options={services.normalize_collection_group(label):label for label in settings['categories'] if len(label)<=40}
            options.setdefault('review','待确认')
            options.setdefault('other','其他')
        for row in store.rows("SELECT DISTINCT v.collection_group FROM videos v WHERE v.collection_group!='' AND " + services.PERSONAL_SCOPE_SQL + " ORDER BY v.collection_group"):
            group = row["collection_group"]
            options.setdefault(group, services.collection_group_label(group))
        return {"groups": options}

    @app.patch("/api/videos/{identity}", dependencies=[Depends(require_auth)])
    def edit_video(identity: str, body: dict):
        video = services.detail(store, identity)
        settings = store.settings()
        allowed = {"category", "is_ai", "tools", "purposes", "state", "notes", "title", "content_tracks", "collection_group"}
        if set(body) - allowed:
            raise TaskError("invalid_input", "包含不支持的素材字段")
        changes = dict(body)
        if "category" in body and body["category"] not in settings["categories"] or "state" in body and body["state"] not in settings["states"]:
            raise TaskError("invalid_input", "分类或状态无效")
        for k in ("tools", "purposes", "content_tracks"):
            if k in body:
                if not isinstance(body[k], list) or len(body[k]) > 30 or not all(isinstance(v, str) and len(v) <= 100 for v in body[k]):
                    raise TaskError("invalid_input", "标签格式不正确")
                changes[k] = encode(body[k])
        if "purposes" in body and any(v not in settings["purposes"] for v in body["purposes"]):
            raise TaskError("invalid_input", "用途标签无效")
        if "content_tracks" in body and (not body["content_tracks"] or any(v not in {"AI教程", "AIGC", "其他"} for v in body["content_tracks"])):
            raise TaskError("invalid_input", "内容方向可多选AI教程、AIGC或其他")
        if "is_ai" in body and not isinstance(body["is_ai"], bool):
            raise TaskError("invalid_input", "AI分类必须为布尔值")
        if "collection_group" in body:
            if "collection_group_manual" not in video:
                raise TaskError("invalid_input", "收藏分类无效")
            changes["collection_group"] = services.normalize_collection_group(body["collection_group"])
        for k in ("title", "notes"):
            if k in body and (not isinstance(body[k], str) or len(body[k]) > 20000):
                raise TaskError("invalid_input", "文本字段过长或无效")
        if set(body) & {"category", "is_ai", "tools", "purposes", "content_tracks"}:
            changes.update(manual_classification=1, classification_method="人工确认")
        if changes:
            with store.connect() as connection:
                previous = connection.execute('SELECT state FROM videos WHERE id=?',(identity,)).fetchone()
                connection.execute("UPDATE videos SET " + ",".join(k + "=?" for k in changes) + " WHERE id=?", [*changes.values(), identity])
                if changes.get('state')=='备选' and previous['state']!='备选':
                    connection.execute('INSERT INTO candidate_saves(video_id,saved_at) VALUES (?,?) ON CONFLICT(video_id) DO UPDATE SET saved_at=excluded.saved_at',(identity,now()))
        return services.detail(store, identity)

    @app.post("/api/videos/batch", dependencies=[Depends(require_auth)])
    def batch_video(body: dict):
        ids = body.get("ids")
        changes = body.get("changes")
        if not isinstance(ids, list) or not 1 <= len(ids) <= 200 or not all(isinstance(i, str) for i in ids) or not isinstance(changes, dict):
            raise TaskError("invalid_input", "批量操作格式无效，单次最多200条")
        if set(changes) == {"collection_group"}:
            group = services.normalize_collection_group(changes["collection_group"])
            ids = list(dict.fromkeys(ids))
            with store.connect() as connection:
                for identity in ids:
                    if not connection.execute("SELECT 1 FROM videos v WHERE v.id=? AND " + services.PERSONAL_SCOPE_SQL, (identity,)).fetchone():
                        raise TaskError("invalid_input", "仅可整理个人收藏；列表已变化，请刷新后重试")
                connection.executemany("UPDATE videos SET collection_group=? WHERE id=?", [(group, identity) for identity in ids])
            return {"updated": len(ids)}
        for identity in ids:
            edit_video(identity, changes)
        return {"updated": len(ids)}

    @app.put("/api/videos/{identity}/transcript", dependencies=[Depends(require_auth)])
    def put_transcript(identity: str, body: TranscriptBody):
        services.save_transcript(store, identity, body.text)
        if get_secret(store, "deepseek") and store.settings()["pricing"]["confirmed"]:
            engine.enqueue("classify", {"ids": [identity]}, "classify:" + identity)
        return {"saved": True}

    @app.get("/api/creators", dependencies=[Depends(require_auth)])
    def creators():
        return store.rows("SELECT * FROM creators ORDER BY enabled DESC,name")

    @app.post("/api/creators", dependencies=[Depends(require_auth)])
    def add_creator(body: LinkBody):
        kind, identity, url = parse_douyin_url(body.url)
        if kind != "creator":
            raise TaskError("invalid_url", "请使用博主主页分享链接")
        count = store.one("SELECT COUNT(*) n FROM creators WHERE enabled=1")["n"]
        if count >= 100 and not store.one("SELECT id FROM creators WHERE id=?", (identity,)):
            raise TaskError("limit", "当前版本最多监控100个账号，建议先保持10—30个")
        store.run("INSERT INTO creators (id,name,url) VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,enabled=1", (identity, body.title or "待更新博主", url))
        return {"id": identity}

    @app.patch("/api/creators/{identity}", dependencies=[Depends(require_auth)])
    def edit_creator(identity: str, body: dict):
        if not isinstance(body.get("enabled"), bool):
            raise TaskError("invalid_input", "enabled必须为布尔值")
        store.run("UPDATE creators SET enabled=? WHERE id=?", (int(body["enabled"]), identity))
        return {"ok": True}

    @app.get("/api/projects", dependencies=[Depends(require_auth)])
    def projects():
        return [services.unpack(r) for r in store.rows("SELECT * FROM projects ORDER BY updated_at DESC")]

    @app.post("/api/projects", dependencies=[Depends(require_auth)])
    def make_project(body: ProjectBody):
        identity = services.create_project(store, body.video_id, body.creation_mode, body.title)
        if body.video_id:
            prioritize(body.video_id)
        return {"id": identity}

    def prioritize(identity):
        return None  # Legacy callers may read drafts, but never start knowledge work.

    @app.get("/api/projects/{identity}", dependencies=[Depends(require_auth)])
    def get_project(identity: str):
        return services.project(store, identity)

    @app.patch("/api/projects/{identity}", dependencies=[Depends(require_auth)])
    def save_project(identity: str, body: ProjectEdit):
        allowed = {"title", "duration", "selected_hook", "content", "script_kind", "stage", "verified_items", "creation_mode", "brief", "reference_ids"}
        c = body.changes
        if set(c) - allowed:
            raise TaskError("invalid_input", "包含不允许手动修改的字段")
        if "duration" in c and (not isinstance(c["duration"], int) or not 60 <= c["duration"] <= 180):
            raise TaskError("invalid_input", "时长应在60—180秒之间")
        if "stage" in c and c["stage"] not in {"选开头", "写正文", "待确认脚本", "已定稿"}:
            raise TaskError("invalid_input", "项目状态无效")
        if "script_kind" in c and c["script_kind"] not in {"完整脚本", "口播大纲"}:
            raise TaskError("invalid_input", "脚本类型无效")
        current = services.project(store, identity)
        if "creation_mode" in c and c["creation_mode"] not in {"tutorial", "commercial"}:
            raise TaskError("invalid_input", "创作模式无效")
        if "brief" in c:
            from .commercial import BRIEF_FIELDS
            if not isinstance(c["brief"], dict) or set(c["brief"]) - BRIEF_FIELDS or any(not isinstance(v, str) or len(v)>2000 for v in c["brief"].values()):
                raise TaskError("invalid_input", "商单brief字段无效或过长")
        if "reference_ids" in c and (not isinstance(c["reference_ids"], list) or len(c["reference_ids"])>4 or not all(isinstance(v, str) and store.one("SELECT id FROM videos WHERE id=?", (v,)) for v in c["reference_ids"])):
            raise TaskError("invalid_input", "请引用素材库中最多4条作品")
        if "verified_items" in c and (not isinstance(c["verified_items"], list) or not all(isinstance(item, str) and item in services.project(store, identity)["verification"] for item in c["verified_items"])):
            raise TaskError("invalid_input", "核验勾选必须对应本稿已有的待核实事项")
        if any(k not in {"duration", "verified_items", "brief", "reference_ids"} and (not isinstance(v, str) or len(v) > 100000) for k, v in c.items()):
            raise TaskError("invalid_input", "项目字段格式不正确")
        if c.get("stage") == "已定稿":
            current = services.project(store, identity)
            if not c.get("content", current["content"]).strip() or not c.get("selected_hook", current["selected_hook"]).strip():
                raise TaskError("invalid_input", "请先确定前5秒开头和正文，再确认脚本定稿")
        if any(k in c and c[k] != current.get(k) for k in {"brief", "reference_ids", "creation_mode"}):
            c["reference_analysis"] = {}
        result = services.update_project(store, identity, body.revision, c)
        for video_id in set(c.get("reference_ids", [])) - set(current["reference_ids"]):
            prioritize(video_id)
        if c.get("stage") == "已定稿" and result["video_id"]:
            store.run("UPDATE videos SET state='已用过' WHERE id=?", (result["video_id"],))
        return result

    @app.post("/api/projects/{identity}/restore", dependencies=[Depends(require_auth)])
    def restore_version(identity: str, body: dict):
        old = store.one("SELECT payload FROM project_versions WHERE project_id=? AND revision=?", (identity, body.get("version")))
        if not old:
            raise TaskError("not_found", "该历史版本不存在")
        previous = services.unpack(json.loads(old["payload"]))
        for key, default in {"creation_mode": "tutorial", "brief": {}, "reference_ids": [], "reference_analysis": {}}.items():
            previous.setdefault(key, default)
        result = services.update_project(store, identity, body.get("revision"), previous, "从历史版本恢复")
        return result

    @app.get("/api/projects/{identity}/export", dependencies=[Depends(require_auth)])
    def export_project(identity: str, format: str = "markdown"):
        p = services.project(store, identity)
        if format == "json":
            return Response(encode(p), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="project-{identity}.json"'})
        text = f"# {p['title']}\n\n来源：{p['video']['url'] if p['video'] else ''}\n\n时长：{p['duration']}秒；版本：v{p['revision']}\n\n## 前5秒\n\n{p['selected_hook']}\n\n## {p['script_kind']}\n\n{p['content']}\n\n## 录屏与拍摄清单\n\n"
        for shot in p["shooting_plan"]:
            if isinstance(shot, dict):
                text += "\n" + "\n".join(f"- {k}：{v}" for k, v in shot.items()) + "\n"
        text += "\n## 待核实\n\n" + "\n".join("- " + str(v) for v in p["verification"])
        return Response(text, media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="script-{identity}.md"'})

    @app.get("/api/settings", dependencies=[Depends(require_auth)])
    def settings(request: Request):
        s = store.settings()
        for k in ("favorites_checkpoint", "backfill_cursor"):
            s.pop(k, None)
        s["credentials"] = {name: bool(get_secret(store, name)) for name in ["deepseek", "tikhub"]}
        s["data_directory"] = str(store.root)
        s["edge_bridge"] = EdgeBridge(store).status()
        s["edge_extension_directory"] = str(Path(__file__).resolve().parents[2] / "edge-extension")
        if request.state.public_phone:
            for field in ("data_directory", "edge_extension_directory", "knowledge"):
                s.pop(field, None)
            s["remote_access"] = {"url": "", "allowed_device": ""}
        return s

    @app.get("/api/connections/edge", dependencies=[Depends(require_auth)])
    def edge_status():
        return EdgeBridge(store).status()

    @app.post("/api/connections/edge/pairing", dependencies=[Depends(require_auth)])
    def edge_pairing():
        return EdgeBridge(store).pairing()

    @app.delete("/api/connections/edge", dependencies=[Depends(require_auth)])
    def edge_revoke():
        with engine.restore_lock:
            return EdgeBridge(store).revoke()

    @app.get("/api/connections/edge/extension.zip", dependencies=[Depends(require_auth)])
    def edge_package():
        import io
        import zipfile
        folder = Path(__file__).resolve().parents[2] / "edge-extension"
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for name in ("manifest.json", "worker.js", "reader.js", "relay.js", "popup.html", "popup.js", "popup.css", "browser-compat.js", "README.md"):
                p = folder / name
                if not p.is_file():
                    raise TaskError("configuration_required", "本地Edge连接组件尚未准备完成")
                z.writestr("edge-extension/" + name, p.read_bytes())
        return Response(out.getvalue(), media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="ai-workbench-edge.zip"'})

    @app.post("/api/edge/bridge/pair")
    def bridge_pair(body: PairBody, request: Request):
        return EdgeBridge(store).pair(body.code, body.extension_id, request.headers.get("origin", ""))

    def require_bridge(request: Request):
        EdgeBridge(store).authorize(request.headers.get("authorization", ""), request.headers.get("origin", ""))

    @app.get("/api/edge/bridge/work", dependencies=[Depends(require_bridge)])
    def bridge_work():
        with engine.restore_lock:
            return EdgeBridge(store).work()

    @app.post("/api/edge/bridge/sync", dependencies=[Depends(require_bridge)])
    def bridge_sync(body: dict):
        if body:
            raise TaskError("invalid_input", "扩展只能请求收藏同步")
        return {"job_id": EdgeBridge(store).manual_guard() or engine.enqueue("favorites_sync", {}, "favorites_sync:")}

    @app.post("/api/edge/bridge/result", dependencies=[Depends(require_bridge)])
    def bridge_result(body: ResultBody):
        with engine.restore_lock:
            return EdgeBridge(store).complete(body)

    @app.patch("/api/settings", dependencies=[Depends(require_auth)])
    def set_settings(body: dict):
        allowed = {"profile", "keywords", "categories", "creator_domain", "traffic_tags", "model", "budgets", "pricing", "data_pricing_confirmed", "usd_cny", "low_followers", "low_min_likes", "low_ratio", "hotspots", "automation", "whisper_model", "whisper_device", "transcription_download_approved", "remote_access"}
        if 'creator_domain' in body:
            from .onboarding import validate_domain
            body['creator_domain']=validate_domain(body['creator_domain'])
        if 'traffic_tags' in body:
            from .onboarding import validate_tags
            body['traffic_tags']=validate_tags(body['traffic_tags'])
        if set(body) - allowed:
            raise TaskError("invalid_input", "设置包含未知字段")
        original = store.settings()
        for key, value in body.items():
            if key in {"profile", "model"} and (not isinstance(value, str) or not value.strip() or len(value) > 12000):
                raise TaskError("invalid_input", "账号定位或模型名称无效")
            if key in {"keywords", "categories"} and (not isinstance(value, list) or not 1 <= len(value) <= 30 or not all(isinstance(v, str) and 0 < len(v) <= 100 for v in value)):
                raise TaskError("invalid_input", "关键词或分类需为1—30项文本")
            if key in {"budgets", "pricing", "automation", "remote_access", "hotspots"}:
                if not isinstance(value, dict) or set(value) != set(original[key]):
                    raise TaskError("invalid_input", "设置字段不完整")
            if key=='hotspots' and (not isinstance(value['enabled'],bool) or type(value['min_likes']) is not int or not 1<=value['min_likes']<=10000000):
                raise TaskError('invalid_input','热点推荐设置无效')
            if key in {"budgets", "pricing"}:
                for k, v in value.items():
                    if k == "confirmed":
                        if not isinstance(v, bool):
                            raise TaskError("invalid_input", "价格确认状态无效")
                    elif isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) or v <= 0 or v > 100000:
                        raise TaskError("invalid_input", "价格与预算必须是有效正数")
            if key in {"usd_cny", "low_followers", "low_min_likes", "low_ratio"} and (isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0):
                raise TaskError("invalid_input", "筛选阈值或汇率无效")
            if key == "automation" and (not isinstance(value["enabled"], bool) or value["backfill_enabled"] is not False or value["favorites_minutes"] not in {240, 480, 720, 1440} or value["creator_minutes"] not in {240, 480, 720, 1440}):
                raise TaskError("invalid_input", "自动任务频率无效")
            if key == "whisper_model" and value not in {"tiny", "base", "small", "medium", "large-v3", "turbo"} or key == "whisper_device" and value not in {"auto", "cpu", "cuda"}:
                raise TaskError("invalid_input", "转写模型或设备无效")
            if key in {"transcription_download_approved", "data_pricing_confirmed"} and not isinstance(value, bool):
                raise TaskError("invalid_input", "模型下载选项无效")
            if key == "remote_access":
                url = value.get("url", "")
                if not isinstance(url, str) or not isinstance(value.get("allowed_device"), str):
                    raise TaskError("invalid_input", "私有访问设置无效")
                if url and (urlparse(url).scheme != "https" or not (urlparse(url).hostname or "").endswith(".ts.net") or urlparse(url).port not in {None, 443} or urlparse(url).username):
                    raise TaskError("invalid_input", "私有地址必须是HTTPS的Tailscale .ts.net地址")
        for key, value in body.items():
            store.set(key, value)
        return {"saved": True}

    @app.put("/api/secrets/{name}", dependencies=[Depends(require_auth)])
    def put_secret(name: str, body: SecretBody):
        if name not in {"deepseek", "tikhub"}:
            raise TaskError("invalid_input", "不支持的服务")
        if body.value and (len(body.value) < 12 or "\n" in body.value):
            raise TaskError("invalid_input", "API密钥格式无效")
        save_secret(store, name, body.value.strip())
        if name == "tikhub":
            store.set("data_connection_state", {"status": "unverified" if body.value else "unconfigured", "updated_at": now()})
        return {"configured": bool(body.value)}

    @app.post("/api/connections/deepseek/test", dependencies=[Depends(require_auth)])
    def test_deepseek():
        return {"models": DeepSeek(store).models()}

    @app.get("/api/jobs", dependencies=[Depends(require_auth)])
    def jobs():
        rows = [services.unpack(r) for r in store.rows("SELECT * FROM jobs ORDER BY CASE WHEN status='running' THEN 0 WHEN status='queued' THEN 1 ELSE 2 END,created_at DESC LIMIT 100")]
        for row in rows:
            if row['result']:
                row['result'] = json.loads(row['result'])
        return rows

    @app.post("/api/jobs", dependencies=[Depends(require_auth)])
    def queue_job(body: JobBody):
        from .discovery import RETIRED_JOBS
        if body.kind in RETIRED_JOBS:raise TaskError('feature_retired','工作台已聚焦素材与对标发现，此制作/知识库功能已移除；已有资料保留')
        allowed = {"favorites_login", "favorites_status", "favorites_probe", "favorites_sync", "favorites_backfill", "video_refresh", "creators_sync", "author_profiles_refresh", "search", "hotspot_sync", "classify", "transcribe", "analyze", "hooks", "script", "backup", "knowledge_sync", "knowledge_deep", "commercial_analyze", "visual_analyze", "brief_extract"}
        if body.kind not in allowed:
            raise TaskError("invalid_job", "不支持该任务")
        p = body.payload
        expected = {"video_refresh": {"video_id"}, "transcribe": {"video_id"}, "analyze": {"video_id"}, "hooks": {"project_id", "revision"}, "script": {"project_id", "revision"}, "classify": {"ids"}, "author_profiles_refresh": {"author_ids"}, "knowledge_deep": {"video_id"}, "commercial_analyze": {"project_id", "revision"}, "visual_analyze": {"video_id"}}.get(body.kind, set())
        optional = {"foreground"} if body.kind in {"favorites_probe", "favorites_sync", "favorites_backfill"} else {"force"} if body.kind in {"author_profiles_refresh", "analyze", "knowledge_deep"} else {"creator_ids"} if body.kind == "creators_sync" else {"keywords"} if body.kind == "search" else set()
        if body.kind == 'brief_extract':
            expected, optional = {'project_id', 'revision', 'text'}, {'source_name'}
        if body.kind == 'hotspot_sync':optional={'retry_from'}
        if not expected <= set(p) or set(p) - expected - optional or any(k in p and not isinstance(p[k], bool) for k in optional & {"foreground", "force"}):
            raise TaskError("invalid_input", "任务参数不匹配")
        for key, limit in [("creator_ids", 100), ("keywords", 20)]:
            if key in p and (not isinstance(p[key], list) or not 1 <= len(p[key]) <= limit or not all(isinstance(value, str) and 0 < len(value) <= 300 for value in p[key])):
                raise TaskError("invalid_input", "重试对象列表无效")
        if body.kind == "author_profiles_refresh" and (not isinstance(p["author_ids"], list) or not 1 <= len(p["author_ids"]) <= 20 or not all(isinstance(a, str) and 0 < len(a) <= 300 for a in p["author_ids"])):
            raise TaskError("invalid_input", "每批最多补全20位作者资料")
        if "video_id" in p:
            services.detail(store, p["video_id"])
        if 'retry_from' in p:
            previous=store.one("SELECT id,status FROM jobs WHERE id=? AND kind='hotspot_sync'",(p['retry_from'],)) if isinstance(p['retry_from'],str) else None
            if not previous or previous['status'] not in {'failed','attention','cancelled'}:
                raise TaskError('invalid_input','只能补跑已结束热点任务的失败查询')
        if "project_id" in p:
            project = services.project(store, p["project_id"])
            if p["revision"] != project["revision"]:
                raise TaskError("revision_conflict", "稿件版本已更新，请先刷新")
        if body.kind == 'brief_extract':
            from .brief_import import validate_text, import_key
            p['text'] = validate_text(p['text'])
            if not isinstance(p.get('source_name', ''), str) or len(p.get('source_name', '')) > 200:
                raise TaskError('invalid_input', 'Brief来源名称过长')
            key = import_key(p['project_id'], p['text'], store.settings()['model'])
            cached = store.one("SELECT id FROM jobs WHERE dedup_key=? AND status='succeeded' ORDER BY created_at DESC LIMIT 1", (key,))
            if cached:
                return {'job_id': cached['id'], 'cached': True}
            return {'job_id': engine.enqueue(body.kind, p, key)}
        if body.kind == "classify" and (not isinstance(p["ids"], list) or not 1 <= len(p["ids"]) <= 20 or not all(isinstance(i, str) for i in p["ids"])):
            raise TaskError("invalid_input", "每批选择1—20条素材进行AI分类")
        if body.kind in {"favorites_sync", "favorites_probe", "favorites_backfill"}:
            active = EdgeBridge(store).manual_guard()
            if active:
                return {"job_id": active, "reused": True}
        if body.kind=='hotspot_sync' and not p:
            latest=store.one("SELECT id,status,created_at,updated_at,result FROM jobs WHERE kind='hotspot_sync' ORDER BY created_at DESC LIMIT 1")
            if latest and latest['status'] in {'succeeded','attention'} and services.age_hours(latest['updated_at']) is not None and services.age_hours(latest['updated_at'])<1/6 and latest['result'] and json.loads(latest['result']).get('completed_queries',0)>0:
                return {'job_id':latest['id'],'cached':True}
        if body.kind=='search' and not p:
            latest=store.one("SELECT id,status,updated_at,result FROM jobs WHERE kind='search' ORDER BY created_at DESC LIMIT 1")
            if latest and latest['status']=='succeeded' and services.age_hours(latest['updated_at']) is not None and services.age_hours(latest['updated_at'])<1/6 and latest['result']:
                result=json.loads(latest['result'])
                expected=list(dict.fromkeys(word.strip().casefold() for word in store.settings()['keywords'] if word.strip()))[:20]
                searched=[word.casefold() for word in result.get('searched_keywords',[])]
                if searched==expected and result.get('completed_keywords',0)>0:
                    return {'job_id':latest['id'],'cached':True}
        identity = p.get("video_id") or p.get("project_id") or ""
        job_id=engine.enqueue(body.kind, p, f"{body.kind}:{identity}")
        existing=store.one('SELECT status FROM jobs WHERE id=?',(job_id,))
        return {"job_id":job_id, "cached":bool(existing and existing['status']=='succeeded')}

    @app.post("/api/brief/read-file", dependencies=[Depends(require_auth)])
    async def read_brief_file(request: Request):
        from .brief_import import MAX_FILE_BYTES, read_file
        from starlette.concurrency import run_in_threadpool
        size = request.headers.get('content-length', '')
        if size.isdigit() and int(size) > MAX_FILE_BYTES:
            raise TaskError('invalid_input', 'Brief文件不能超过5MB')
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > MAX_FILE_BYTES:
                raise TaskError('invalid_input', 'Brief文件不能超过5MB')
            data.extend(chunk)
        filename = unquote(request.headers.get('x-file-name', ''))
        return await run_in_threadpool(read_file, bytes(data), filename)

    @app.get("/api/jobs/{identity}", dependencies=[Depends(require_auth)])
    def get_job(identity: str):
        row = services.unpack(store.one("SELECT * FROM jobs WHERE id=?", (identity,)))
        if not row:
            raise TaskError("not_found", "任务不存在")
        if row["result"]:
            row["result"] = json.loads(row["result"])
        return row

    @app.post("/api/jobs/{identity}/retry", dependencies=[Depends(require_auth)])
    def retry_job(identity: str):
        row = store.one("SELECT * FROM jobs WHERE id=?", (identity,))
        if not row or row["status"] not in {"failed", "attention", "cancelled"}:
            raise TaskError("invalid_input", "只有失败、待处理或取消的任务可以重试")
        payload = json.loads(row["payload"])
        result = json.loads(row["result"]) if row["result"] else {}
        if row["kind"] == "creators_sync" and result.get("failed_creators"):
            payload["creator_ids"] = [creator['id'] for creator in result['failed_creators']]
        if row["kind"] == "search" and result.get("failed_keywords"):
            payload["keywords"] = [item['keyword'] for item in result['failed_keywords']]
        if row['kind']=='hotspot_sync' and result.get('terms') and result.get('queries'):
            payload['retry_from']=row['id']
        if row["kind"] == "author_profiles_refresh":
            payload["force"] = True
        return queue_job(JobBody(kind=row["kind"], payload=payload))

    @app.post("/api/jobs/{identity}/cancel", dependencies=[Depends(require_auth)])
    def cancel_job(identity: str):
        return engine.cancel(identity)

    @app.get("/api/events", dependencies=[Depends(require_auth)])
    async def events(request: Request):
        async def stream():
            for _ in range(300):
                if await request.is_disconnected():
                    break
                rows = store.rows("SELECT id,status,progress,message,updated_at FROM jobs ORDER BY updated_at DESC LIMIT 15")
                yield "data: " + encode(rows) + "\n\n"
                await asyncio.sleep(2)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})

    @app.get("/api/backups", dependencies=[Depends(require_auth)])
    def backups():
        folder = store.root / "backups"
        return [{"name": p.name, "size": p.stat().st_size} for p in sorted(folder.glob("workbench-*.sqlite3"), reverse=True)][:100]

    @app.get("/api/backups/{name}", dependencies=[Depends(require_auth)])
    def download_backup(name: str):
        if Path(name).name != name or not name.startswith("workbench-") or not name.endswith(".sqlite3"):
            raise TaskError("invalid_input", "备份名无效")
        path = store.root / "backups" / name
        if not path.exists():
            raise TaskError("not_found", "备份不存在")
        return FileResponse(path, filename=name, media_type="application/octet-stream")

    @app.post("/api/backups/restore", dependencies=[Depends(require_auth)])
    def restore_backup(body: dict):
        if body.get("confirm") != "恢复备份":
            raise TaskError("confirmation_required", "请输入「恢复备份」，当前数据库会先自动备份")
        with engine.restore_lock:
            return services.restore(store, body.get("name", ""))

    @app.get("/api/export", dependencies=[Depends(require_auth)])
    def export_library():
        data = {"schema_version": 1, "exported_at": now(), "videos": services.ranked_videos(store, state="", days=0, mode="recent", include_archived=True), "projects": [services.project(store, p["id"]) for p in store.rows("SELECT id FROM projects")]}
        data["author_follower_history"] = store.rows("SELECT * FROM author_follower_history ORDER BY id")
        data["recommendation_records"] = store.rows("SELECT * FROM recommendation_records ORDER BY id")
        for v in data["videos"]:
            v["transcript"] = services.detail(store, v["id"])["transcript"]
        return Response(encode(data), media_type="application/json", headers={"Content-Disposition": 'attachment; filename="ai-workbench-library.json"'})

    @app.get("/api/system", dependencies=[Depends(require_auth)])
    def system():
        import importlib.util
        tailscale = shutil.which("tailscale")
        if not tailscale:
            candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Tailscale/tailscale.exe"
            tailscale = str(candidate) if candidate.is_file() else None
        result = {"ffmpeg": bool(shutil.which("ffmpeg")), "whisper": bool(importlib.util.find_spec("faster_whisper")), "tailscale_installed": bool(tailscale), "tailscale_status": "not_installed" if not tailscale else "installed", "remote_url": store.settings()["remote_access"].get("url", ""), "data_directory": str(store.root)}
        if tailscale:
            try:
                r = subprocess.run([tailscale, "status", "--json"], capture_output=True, timeout=5, creationflags=0x08000000 if os.name == "nt" else 0)
                s = json.loads(r.stdout)
                result["tailscale_status"] = s.get("BackendState", "unknown")
                result["computer_name"] = s.get("Self", {}).get("DNSName", "").rstrip(".")
                result["devices"] = [{"name": p.get("HostName"), "ips": p.get("TailscaleIPs"), "online": p.get("Online")} for p in (s.get("Peer") or {}).values()]
            except Exception:
                result["tailscale_status"] = "not_connected"
        return result

    frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if frontend.exists():
        app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")

        @app.get("/{path:path}")
        def frontend_file(path: str):
            if path.startswith("api/"):
                return JSONResponse({"message": "接口不存在"}, status_code=404)
            candidate = (frontend / path).resolve()
            if frontend.resolve() in candidate.parents and candidate.is_file():
                return FileResponse(candidate, headers={"Cache-Control": "no-cache"} if candidate.name in {"index.html", "sw.js", "manifest.webmanifest"} else None)
            return FileResponse(frontend / "index.html", headers={"Cache-Control": "no-cache"})
    return app


app = create_app()
