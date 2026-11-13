from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .db import Store, encode, now
from .providers import TaskError
from .services import ingest, order_favorites

EXTENSION_ID = re.compile(r"^[a-p]{32}$")
ORIGIN = re.compile(r"^chrome-extension://([a-p]{32})$")


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class PairBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=20, max_length=128)
    extension_id: str = Field(pattern=r"^[a-p]{32}$")


class EdgeVideo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^\d{6,30}$")
    title: str = Field(min_length=1, max_length=4000)
    author: str = Field(default="", max_length=200)
    cover: str | None = Field(default=None, max_length=3000)
    media_kind: str = Field(default="video", pattern=r"^(video|image)$")


class ReaderState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visibility: Literal['visible', 'hidden', 'prerender'] | None = None
    rounds: int = Field(ge=0, le=100)
    scroll_top: int = Field(ge=0, le=100000000)
    scroll_height: int = Field(ge=0, le=100000000)
    viewport_height: int = Field(ge=0, le=100000)


class ResultBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    nonce: str = Field(min_length=20, max_length=128)
    outcome: str = Field(pattern=r"^(ok|login_required|verification_required|page_unavailable|no_progress|browser_closed)$")
    items: list[EdgeVideo] = Field(default_factory=list, max_length=300)
    head_ids: list[str] = Field(default_factory=list, max_length=40)
    reached_checkpoint: bool = False
    exhausted: bool = False
    truncated: bool = False
    continuation: bool = False
    reader_revision: str | None = Field(default=None, max_length=40)
    stop_reason: Literal['batch_limit','checkpoint','end','page_stalled','time_limit','scan_limit'] | None = None
    reader_state: ReaderState | None = None


class EdgeBridge:
    def __init__(self, store: Store):
        self.store = store

    def status(self):
        row = self.store.one("SELECT extension_id,created_at,last_seen FROM browser_bridge WHERE id=1")
        last = self.store.settings().get("favorites_last_access_at")
        remaining = 0
        if last:
            try:
                remaining = max(0, int((datetime.fromisoformat(last) + timedelta(minutes=10) - datetime.now(timezone.utc)).total_seconds()))
            except (TypeError, ValueError):
                pass
        return {"paired": bool(row), **(row or {}), "access_cooldown_seconds": remaining, "last_access_at": last}

    def manual_guard(self):
        active = self.store.one("SELECT id FROM jobs WHERE kind IN ('favorites_sync','favorites_probe','favorites_backfill') AND status IN ('running','queued') ORDER BY created_at LIMIT 1")
        if active:
            return active['id']
        remaining = self.status()["access_cooldown_seconds"]
        if remaining:
            raise TaskError("collection_cooldown", f"收藏读取保护间隔中，请约{(remaining + 59) // 60}分钟后再手动同步；可以继续浏览已有素材")
        return None

    def pairing(self):
        code = secrets.token_urlsafe(24)
        self.store.run("INSERT OR REPLACE INTO browser_pairing VALUES (1,?,?,0)", (digest(code), time.time() + 600))
        return {"code": code, "expires_in": 600}

    def pair(self, code, extension_id, origin=""):
        if not EXTENSION_ID.fullmatch(extension_id) or origin and origin != "chrome-extension://" + extension_id:
            raise TaskError("invalid_extension", "扩展来源不匹配")
        token = secrets.token_urlsafe(36)
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM browser_pairing WHERE id=1").fetchone()
            valid = bool(row and row["expires"] > time.time() and row["attempts"] < 8 and hmac.compare_digest(row["code_hash"], digest(code)))
            if valid:
                c.execute("DELETE FROM browser_pairing")
                c.execute("INSERT OR REPLACE INTO browser_bridge VALUES (1,?,?,?,?)", (digest(token), extension_id, now(), now()))
            elif row:
                c.execute("UPDATE browser_pairing SET attempts=attempts+1 WHERE id=1")
        if not valid:
            raise TaskError("invalid_pairing", "配对码不正确、已使用或已过期，请在工作台重新生成")
        return {"token": token, "paired": True}

    def authorize(self, authorization, origin=""):
        row = self.store.one("SELECT * FROM browser_bridge WHERE id=1")
        supplied = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
        if not row or not supplied or not hmac.compare_digest(row["token_hash"], digest(supplied)):
            raise TaskError("bridge_unauthorized", "浏览器连接已失效，请在工作台重新配对本地扩展")
        if origin and origin != "chrome-extension://" + row["extension_id"]:
            raise TaskError("bridge_unauthorized", "请求不来自已配对的浏览器扩展")
        return row

    def revoke(self):
        with self.store.connect() as c:
            c.execute("DELETE FROM browser_bridge")
            c.execute("DELETE FROM browser_pairing")
            c.execute("UPDATE jobs SET status='cancelled',message='浏览器连接已解除',updated_at=? WHERE id IN (SELECT job_id FROM browser_requests WHERE state IN ('pending','claimed')) AND status='running'", (now(),))
            c.execute("UPDATE browser_requests SET state='cancelled' WHERE state IN ('pending','claimed')")
        return {"paired": False}

    def request(self, mode, job_id):
        if mode == "login":
            raise TaskError("configuration_required", "当前复用已经登录的浏览器。请连接本地浏览器扩展，无需另开浏览器扫码。")
        state = self.status()
        if not state["paired"]:
            raise TaskError("configuration_required", "收藏页面已可读取。请先在「我的 → 连接现有浏览器」配对本地连接扩展，无需重新登录抖音。")
        if mode == "status":
            return state
        if not job_id:
            raise TaskError("invalid_job", "缺少收藏任务编号")
        self.store.run("INSERT INTO browser_requests (job_id,mode,created_at) VALUES (?,?,?) ON CONFLICT(job_id) DO UPDATE SET state='pending',lease_until=0,nonce=NULL,created_at=excluded.created_at", (job_id, mode, time.time()))
        return {"_await_browser": True}

    def expire(self):
        with self.store.connect() as c:
            expired = c.execute("SELECT job_id FROM browser_requests WHERE state IN ('pending','claimed') AND created_at<?", (time.time() - 900,)).fetchall()
            for row in expired:
                c.execute("UPDATE browser_requests SET state='expired' WHERE job_id=?", (row["job_id"],))
                c.execute("UPDATE jobs SET status='attention',error_code='browser_offline',message='未收到浏览器返回，请确认电脑上的采集浏览器和本地连接扩展正在运行',updated_at=? WHERE id=? AND status='running'", (now(), row["job_id"]))

    def work(self):
        self.expire()
        self.store.run("UPDATE browser_bridge SET last_seen=? WHERE id=1", (now(),))
        stamp = time.time()
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT r.*,j.payload FROM browser_requests r JOIN jobs j ON j.id=r.job_id WHERE j.status='running' AND (r.state='pending' OR (r.state='claimed' AND r.lease_until<?)) ORDER BY r.created_at LIMIT 1", (stamp,)).fetchone()
            if not row:
                return {"request": None}
            nonce = secrets.token_urlsafe(24)
            c.execute("UPDATE browser_requests SET state='claimed',lease_until=?,nonce=? WHERE job_id=?", (stamp + 300, nonce, row["job_id"]))
            previous_job = c.execute("SELECT value FROM settings WHERE key='favorites_last_access_job'").fetchone()
            if not previous_job or json.loads(previous_job['value']) != row['job_id']:
                c.execute("INSERT INTO settings VALUES ('favorites_last_access_at',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (encode(now()),))
                c.execute("INSERT INTO settings VALUES ('favorites_last_access_job',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (encode(row['job_id']),))
        settings = self.store.settings()
        mode = row["mode"]
        known = [r["video_id"] for r in self.store.rows("SELECT DISTINCT video_id FROM sources WHERE kind='favorites' LIMIT 20000")]
        return {"request": {"job_id": row["job_id"], "nonce": nonce, "mode": mode, "foreground": json.loads(row["payload"]).get("foreground") is True, "limit": 20 if mode == "probe" else 100 if mode == "backfill" or settings["favorites_checkpoint"] else 300, "stop_ids": settings["favorites_checkpoint"] if mode == "sync" else [], "known_ids": known}}

    def complete(self, result: ResultBody):
        row = self.store.one("SELECT r.*,j.status job_status,j.result job_result FROM browser_requests r JOIN jobs j ON j.id=r.job_id WHERE r.job_id=?", (result.job_id,))
        if not row or row["job_status"] != "running" or row["state"] != "claimed" or not row["nonce"] or not hmac.compare_digest(row["nonce"], result.nonce):
            raise TaskError("stale_request", "任务已取消、完成或重新领取，本次结果未写入")
        if result.outcome != "ok":
            messages = {"login_required": "当前浏览器页面需要登录，请先在平时使用的抖音页面确认登录；工作台不会另开登录会话", "verification_required": "抖音页面出现验证，请在现有浏览器中本人处理；自动采集已暂停", "page_unavailable": "没有可读取的浏览器抖音页面，请先打开平时使用的抖音", "no_progress": "收藏页未返回可确认的作品，未把异常当作空收藏", "browser_closed": "浏览器收藏页面已关闭，本次未完成"}
            message = messages[result.outcome]
            self.store.run("UPDATE jobs SET status='attention',message=?,error_code=?,updated_at=? WHERE id=? AND status='running'", (message, result.outcome, now(), result.job_id))
            self.store.run("UPDATE browser_requests SET state='failed' WHERE job_id=?", (result.job_id,))
            previous = self.store.settings()["collection_state"]
            self.store.set("collection_state", {"status": result.outcome, "transport": "existing_edge", "message": message, "updated_at": now(), "authenticated_once": previous.get("authenticated_once", False)})
            return {"saved": False}
        if any(not re.fullmatch(r"\d{6,30}", x) for x in result.head_ids):
            raise TaskError("invalid_response", "收藏水位包含无效作品编号")
        if result.continuation:
            valid = row['mode']=='backfill' and not result.items and result.truncated and not result.exhausted and not result.reached_checkpoint and result.stop_reason in {'scan_limit','time_limit'} and result.reader_state and result.reader_state.rounds>=2 and result.reader_state.scroll_top>0 and bool(result.head_ids)
            if valid:
                known={r['video_id'] for r in self.store.rows("SELECT DISTINCT video_id FROM sources WHERE kind='favorites' AND video_id IN ("+','.join('?' for _ in result.head_ids)+')',result.head_ids)}
                valid=set(result.head_ids)<=known
            if not valid:
                raise TaskError('invalid_response','历史续接缺少已入库作品与翻页进度，未把空结果记为成功')
            passes=json.loads(row['job_result'] or '{}').get('scan_passes',0)+1
            if passes>20:
                self.store.run("UPDATE browser_requests SET state='failed' WHERE job_id=?",(result.job_id,))
                self.store.run("UPDATE jobs SET status='attention',error_code='backfill_limit',message='本轮历史扫描已到上限，可稍后继续；没有误报为空收藏',updated_at=? WHERE id=?",(now(),result.job_id))
                return {'saved':False}
            self.store.run("UPDATE browser_requests SET state='pending',lease_until=0,nonce=NULL WHERE job_id=?",(result.job_id,))
            self.store.run("UPDATE jobs SET progress=10,message='正在越过已入库的历史收藏，翻页位置已保留，等待继续读取',result=?,updated_at=? WHERE id=? AND status='running'",(encode({'scan_passes':passes,'continuation':True}),now(),result.job_id))
            return {'saved':True,'scanned':len(set(result.head_ids)),'added':0,'continuation':True}
        if not result.items and not result.exhausted and not result.reached_checkpoint:
            raise TaskError("invalid_response", "未确认空收藏或已到旧水位，本次不能记为成功")
        added = 0
        seen = set()
        for item in result.items:
            if item.id in seen:
                continue
            seen.add(item.id)
            data = item.model_dump()
            data["url"] = f"https://www.douyin.com/{'note' if item.media_kind == 'image' else 'video'}/{item.id}"
            host = (urlparse(item.cover or "").hostname or "").lower()
            if item.cover and (urlparse(item.cover).scheme != "https" or not any(host == d or host.endswith("." + d) for d in ("douyinpic.com", "byteimg.com", "douyincdn.com", "ibytedtos.com", "pstatp.com"))):
                data["cover"] = None
            added += int(ingest(self.store, data, "favorites", "existing-edge"))
            self.store.run("UPDATE videos SET notes=? WHERE id=? AND notes=''", ("由已登录浏览器收藏页面读取；收藏时间、发布时间及未明确标注的互动数据保持未知。", item.id))
        if row["mode"] == "sync" and result.head_ids and not result.truncated:
            self.store.set("favorites_checkpoint", result.head_ids)
        if row["mode"] == "backfill":
            self.store.set("backfill_has_more", not result.exhausted)
        order_favorites(self.store, ([*result.head_ids] if row["mode"] != "backfill" else []) + [item.id for item in result.items], append=row["mode"] == "backfill")
        summary = {"scanned": len(seen), "added": added, "truncated": result.truncated, "has_more": not result.exhausted, "collection_time_available": False, "probe": row["mode"] == "probe", "reader_revision": result.reader_revision, "stop_reason": result.stop_reason, "reader_state": result.reader_state.model_dump() if result.reader_state else None}
        stalled = result.truncated and result.stop_reason == 'page_stalled'
        action = "读取验证完成" if row["mode"] == "probe" else "历史回填本批完成" if row["mode"] == "backfill" and not stalled else "部分入库" if result.truncated else "同步完成"
        suffix = "；分页未继续加载，请使用手动前台同步" if stalled else "；仍有更早收藏可继续整理" if row["mode"] == "backfill" and not result.exhausted else "；本次尚有积压，水位未推进" if result.truncated else ""
        message = f"现有浏览器收藏{action}：读取{len(seen)}条，新增{added}条" + suffix
        self.store.run("UPDATE browser_requests SET state='completed' WHERE job_id=?", (result.job_id,))
        self.store.run("UPDATE jobs SET status=?,progress=100,message=?,result=?,error_code=?,updated_at=? WHERE id=? AND status='running'", ('attention' if stalled else 'succeeded', message, encode(summary), 'pagination_stalled' if stalled else None, now(), result.job_id))
        self.store.set("collection_state", {"status": "pagination_stalled" if stalled else "authenticated", "transport": "existing_edge", "message": message, "updated_at": now(), "authenticated_once": True})
        last = self.store.settings()["last_sync"]
        last["favorites"] = now()
        self.store.set("last_sync", last)
        self.store.run("UPDATE browser_bridge SET last_seen=? WHERE id=1", (now(),))
        return {"saved": True, **summary}
