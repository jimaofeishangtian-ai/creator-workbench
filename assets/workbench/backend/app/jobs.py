from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from .edge_bridge import EdgeBridge
from .db import Store, encode, now
from .providers import TaskError, TikHub, extract_videos, normalize_video
from .security import get_secret
from .services import analyze, backup, classify, generate_project, ingest, record_author_followers, author_profile
from .transcription import transcribe
from .task_context import cancellation_scope, check_cancelled

TZ = timezone(timedelta(hours=8))
log = logging.getLogger("workbench")


class JobEngine:
    def __init__(self, store: Store):
        self.store = store
        self.stop = threading.Event()
        self.worker = None
        self.scheduler = None
        self.restore_lock = threading.RLock()

    def start(self):
        from .discovery import RETIRED_JOBS
        for kind in RETIRED_JOBS:
            self.store.run("UPDATE jobs SET status='cancelled',error_code='feature_retired',message='功能已退出工作台，历史资料保留',updated_at=? WHERE kind=? AND status IN ('queued','running')",(now(),kind))
        self.store.run("UPDATE jobs SET status='queued',progress=0,message='进程重启，准备恢复任务' WHERE status='running' AND kind IN ('favorites_probe','favorites_sync','favorites_backfill','backup')")
        self.store.run("UPDATE jobs SET status='attention',error_code='interrupted',message='任务被中断，结果与费用可能已发生，请检查后重试' WHERE status='running'")
        self.worker = threading.Thread(target=self.loop, daemon=True, name="workbench-jobs")
        self.scheduler = threading.Thread(target=self.schedule_loop, daemon=True, name="workbench-schedule")
        self.worker.start()
        self.scheduler.start()

    def close(self):
        self.stop.set()

    def enqueue(self, kind, payload=None, dedup_key=None):
        import sqlite3
        from .discovery import RETIRED_JOBS
        if kind in RETIRED_JOBS:raise TaskError('feature_retired','该功能已退出工作台，历史资料保留')
        check_cancelled()
        if kind=='hotspot_sync':
            latest=self.store.one("SELECT id,status,created_at FROM jobs WHERE kind='hotspot_sync' ORDER BY created_at DESC LIMIT 1")
            if latest and latest['status'] in {'queued','running'}:return latest['id']
            if latest and (datetime.now(timezone.utc)-datetime.fromisoformat(latest['created_at'])).total_seconds()<600:
                raise TaskError('rate_limited','热点推荐每10分钟最多更新一轮，避免重复付费；请先查看已有结果')
        if kind in {'video_refresh', 'creators_sync'}:
            recent = self.store.one('SELECT id,payload,status,updated_at,result FROM jobs WHERE kind=? ORDER BY created_at DESC LIMIT 1', (kind,))
            if recent and recent['status']=='succeeded' and json.loads(recent['payload'])==(payload or {}):
                from .services import age_hours
                age=age_hours(recent['updated_at'])
                same_creators = kind!='creators_sync' or json.loads(recent['result'] or '{}').get('creator_ids_seen')==sorted(r['id'] for r in self.store.rows('SELECT id FROM creators WHERE enabled=1'))
                if age is not None and 0<=age<1/6 and same_creators:return recent['id']
        dedup_key = self.job_key(kind, dedup_key)
        if kind=='video_refresh':dedup_key='video:'+str((payload or {}).get('video_id',''))
        identity = uuid.uuid4().hex
        try:
            self.store.run("INSERT INTO jobs (id,kind,payload,dedup_key,created_at,updated_at) VALUES (?,?,?,?,?,?)", (identity, kind, encode(payload or {}), dedup_key, now(), now()))
        except sqlite3.IntegrityError:
            row = self.store.one("SELECT id FROM jobs WHERE dedup_key=? AND status IN ('queued','running')", (dedup_key,))
            if row:
                return row["id"]
            raise
        return identity

    @staticmethod
    def job_key(kind, fallback):
        if kind in {"favorites_probe", "favorites_sync", "favorites_backfill"}:
            return "favorites-browser"
        if kind in {"creators_sync", "search", "hotspot_sync", "author_profiles_refresh", "knowledge_sync"}:
            return kind
        return fallback

    def check_job(self, identity):
        row = self.store.one("SELECT status FROM jobs WHERE id=?", (identity,)) if identity else None
        if self.stop.is_set() or row and row["status"] == "cancelled":
            raise TaskError("cancelled", "任务已取消，不再发起后续请求；已产生的费用保留")

    def cancel(self, identity):
        # Never take restore_lock here: the worker holds it during network calls.
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT status,kind FROM jobs WHERE id=?", (identity,)).fetchone()
            if not row:
                raise TaskError("not_found", "任务不存在")
            if row["status"] not in {"running", "queued"}:
                return {"cancelled": False, "status": row["status"], "message": "任务已结束，无需取消"}
            browser_claimed = c.execute("SELECT job_id FROM browser_requests WHERE job_id=? AND state='claimed'", (identity,)).fetchone()
            message = "已取消；当前收藏页面可能需本轮结束（最多约100秒），后续批次不再启动" if browser_claimed else "已取消；在途请求收尾后停止，后续请求不再发起，已产生费用保留"
            c.execute("UPDATE jobs SET status='cancelled',message=?,error_code='cancelled',updated_at=? WHERE id=?", (message, now(), identity))
            c.execute("UPDATE browser_requests SET state='cancelled' WHERE job_id=? AND state IN ('pending','claimed')", (identity,))
        return {"cancelled": True, "status": "cancelled", "message": message}

    def progress(self, identity, value, message):
        row = self.store.one("SELECT status FROM jobs WHERE id=?", (identity,))
        if self.stop.is_set() or row and row["status"] == "cancelled":
            raise TaskError("cancelled", "任务已取消，已产生的费用会保留")
        self.store.run("UPDATE jobs SET progress=?,message=?,updated_at=? WHERE id=? AND status!='cancelled'", (value, message, now(), identity))

    def loop(self):
        while not self.stop.is_set():
            with self.restore_lock:
                with self.store.connect() as c:
                    c.execute("BEGIN IMMEDIATE")
                    row = c.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
                    if row:
                        job = dict(row)
                        c.execute("UPDATE jobs SET status='running',attempts=attempts+1,updated_at=? WHERE id=?", (now(), job["id"]))
                if row:
                    try:
                        result = self.execute(job["kind"], json.loads(job["payload"]), lambda v, m: self.progress(job["id"], v, m), job_id=job["id"])
                        self.check_job(job["id"])
                        if result and result.get("_await_browser"):
                            self.store.run("UPDATE jobs SET progress=5,message='等待现有Edge返回收藏，通常在一分钟内开始',updated_at=? WHERE id=? AND status='running'", (now(), job["id"]))
                            continue
                        self.store.run("UPDATE jobs SET result=? WHERE id=?", (encode(result or {}), job["id"]))
                        partial = bool((result or {}).get("failed_keywords") or (result or {}).get("failed_creators"))
                        if job['kind']=='hotspot_sync':partial=bool(result.get('issues') or any(q['status']=='failed' for q in result.get('queries',[])))
                        message = f"搜索部分完成：{result['completed_keywords']}个关键词成功，{len(result['failed_keywords'])}个需要处理" if job["kind"] == 'search' and partial else f"博主同步部分完成：{result['completed_creators']}个成功，{len(result['failed_creators'])}个需要处理" if job["kind"] == 'creators_sync' and partial else "已完成"
                        if job["kind"] == "search":
                            message += f"；请求{result['request_attempts']}次，其中自动重试{result['retry_attempts']}次"
                        if job['kind']=='hotspot_sync':message=f"热点推荐：{result['completed_queries']}/{len(result['queries'])}个查询完成，{result['observed_videos']}条样本；数据请求{result['request_attempts']}次"
                        self.store.run("UPDATE jobs SET status=?,progress=100,message=?,error_code=?,result=?,updated_at=? WHERE id=? AND status!='cancelled'", ('attention' if partial else 'succeeded', message, 'partial_failure' if partial else None, encode(result or {}), now(), job["id"]))
                    except TaskError as e:
                        status = "cancelled" if e.code == "cancelled" else "attention" if e.code in {"configuration_required", "pricing_required", "login_required", "model_download_required", "budget_exceeded", "balance_required", "transcript_required", "hook_required", "revision_conflict", "credential_error", "brief_required", "reference_required", "reference_analysis_required"} else "failed"
                        self.store.run("UPDATE jobs SET status=?,message=?,error_code=?,updated_at=? WHERE id=? AND status!='cancelled'", (status, e.message, e.code, now(), job["id"]))
                    except Exception as e:
                        # Exception strings can include signed media URLs or credentials: never persist them.
                        log.error("job %s failed: %s", job["id"], type(e).__name__)
                        self.store.run("UPDATE jobs SET status='failed',message='任务异常，未误报为无新增；可检查配置后重试',error_code='internal_error',updated_at=? WHERE id=? AND status!='cancelled'", (now(), job["id"]))
            self.stop.wait(.5 if row else 1)

    def execute(self, kind, payload, progress, *, job_id=None):
        with cancellation_scope(lambda: self.check_job(job_id), {'job_id':job_id,'job_kind':kind}):
            self.check_job(job_id)
            return self._execute(kind, payload, progress, job_id=job_id)

    def _execute(self, kind, payload, progress, *, job_id=None):
        from .discovery import RETIRED_JOBS
        if kind in RETIRED_JOBS:
            raise TaskError('feature_retired','已移除知识库/制作任务，保留已有资料，不再执行后续请求')
        if kind=='hotspot_sync':
            from .hotspots import refresh_hotspots
            previous = self.store.one("SELECT result FROM jobs WHERE id=? AND kind='hotspot_sync'", (payload.get('retry_from',''),)) if payload.get('retry_from') else None
            return refresh_hotspots(self.store,progress,previous=json.loads(previous['result']) if previous and previous['result'] else None,job_id=job_id)
        if kind == "knowledge_sync":
            from .knowledge import sync_vault
            return sync_vault(self.store, progress, period=payload.get("period"))
        if kind == "knowledge_deep":
            from .knowledge import deep_read, knowledge_current, mark_priority
            cached = self.store.one("SELECT * FROM knowledge_items WHERE video_id=?", (payload["video_id"],))
            transcript = self.store.one("SELECT content_hash,updated_at FROM transcripts WHERE video_id=?", (payload["video_id"],))
            if knowledge_current(cached, transcript) and not payload.get("force") and cached["priority"]:
                return {"cached": True, "video_id": payload["video_id"]}
            if knowledge_current(cached, transcript) and not payload.get("force"):
                mark_priority(self.store, payload["video_id"])
                result = {"cached": True, "video_id": payload["video_id"]}
            else:
                result = deep_read(self.store, payload["video_id"], progress, force=payload.get("force", False))
            if self.store.settings()["knowledge"]["enabled"]:
                self.enqueue("knowledge_sync", dedup_key="knowledge_sync")
            return result
        if kind == "commercial_analyze":
            from .commercial import analyze_references
            return analyze_references(self.store, payload["project_id"], payload["revision"], progress, job_id)
        if kind == "visual_analyze":
            from .visual import analyze_visual
            return analyze_visual(self.store, payload["video_id"], progress)
        if kind == "brief_extract":
            from .brief_import import extract
            return extract(self.store, payload["project_id"], payload["text"], payload.get("source_name", "粘贴的Brief"), progress)
        if kind == "author_profiles_refresh":
            identities = list(dict.fromkeys(payload["author_ids"]))[:20]
            completed, failures = 0, []
            for index, author_id in enumerate(identities):
                progress(int(index / max(1, len(identities)) * 90), "正在补全推荐作者的粉丝资料")
                attempted = self.store.one("SELECT attempted_at FROM author_profile_refreshes WHERE author_id=?", (author_id,))
                if not payload.get("force") and attempted and (datetime.now(timezone.utc) - datetime.fromisoformat(attempted["attempted_at"])).total_seconds() < 86400:
                    continue
                try:
                    profile = author_profile(self.store, TikHub(self.store), author_id, force=payload.get('force',False))
                    if profile["followers"] is None:
                        raise TaskError("missing_followers", "作者资料未提供粉丝数，保留未知")
                    record_author_followers(self.store, author_id, profile["name"], profile["followers"], profile['sampled_at'])
                    completed += 1
                except TaskError as error:
                    self.store.run("UPDATE author_profile_refreshes SET error_code=? WHERE author_id=?", (error.code, author_id))
                    if error.code in {"configuration_required", "pricing_required", "credential_error", "balance_required", "budget_exceeded", "rate_limited", "cancelled"}:
                        raise
                    failures.append({"id": author_id, "code": error.code, "message": error.message})
            return {"completed_creators": completed, "failed_creators": failures}
        if kind in {"favorites_login", "favorites_status", "favorites_probe", "favorites_sync", "favorites_backfill"}:
            mode = {"favorites_login": "login", "favorites_status": "status", "favorites_probe": "probe", "favorites_sync": "sync", "favorites_backfill": "backfill"}[kind]
            return EdgeBridge(self.store).request(mode, job_id)
        if kind == "video_refresh":
            progress(10, "正在读取作品公开数据")
            data = TikHub(self.store).detail(payload["video_id"])
            ingest(self.store, data, touch_source=False)
            if data.get("author_id"):
                progress(60, "正在验证作者粉丝数")
                p = author_profile(self.store, TikHub(self.store), data["author_id"])
                record_author_followers(self.store, data["author_id"], p["name"], p["followers"], p['sampled_at'])
            self.classify_pending()
            return {"video_id": data["id"]}
        if kind == "creators_sync":
            creators = self.store.rows("SELECT * FROM creators WHERE enabled=1")
            if payload.get('creator_ids'):
                requested = set(payload['creator_ids'])
                creators = [creator for creator in creators if creator['id'] in requested]
            if not creators:
                raise TaskError("configuration_required", "请先添加你要监控的对标博主")
            api, stats, failures = TikHub(self.store), {'added':0,'scanned':0}, []
            for index, creator in enumerate(creators):
                progress(int(index / max(1, len(creators)) * 90), f"正在检查 {creator['name']}")
                try:
                    self.sync_creator(api, creator, stats)
                except TaskError as e:
                    if e.code in {'configuration_required','pricing_required','credential_error','balance_required','budget_exceeded','rate_limited','cancelled'}:
                        raise
                    failures.append({'id':creator['id'],'name':creator['name'],'code':e.code,'message':e.message})
            self.classify_pending()
            last = self.store.settings()["last_sync"]
            if len(failures) < len(creators):
                last["creators"] = now()
                self.store.set("last_sync", last)
            return {"creators": len(creators), "creator_ids_seen":sorted(c['id'] for c in creators), "completed_creators":len(creators)-len(failures), **stats, "failed_creators":failures}
        if kind == "search":
            keywords, seen = [], set()
            for value in payload.get("keywords") or self.store.settings()["keywords"]:
                keyword = value.strip()
                if keyword and keyword.casefold() not in seen:
                    seen.add(keyword.casefold())
                    keywords.append(keyword)
            keywords = keywords[:20]
            added, failures, observed_ids = 0, [], set()
            request_attempts, retry_attempts = 0, 0
            for index, keyword in enumerate(keywords):
                progress(int(index / max(1, len(keywords)) * 90), f"正在发现：{keyword}")
                response = None
                for attempt in range(2):
                    check_cancelled()
                    try:
                        request_attempts += 1
                        response = TikHub(self.store).search(keyword)
                        break
                    except TaskError as e:
                        if e.code in {"configuration_required", "pricing_required", "credential_error", "balance_required", "budget_exceeded", "rate_limited", "cancelled"}:
                            raise
                        # Do not repeat rejected HTTP 400 requests or requests with
                        # unknown billing after a timeout. Only short-lived server
                        # errors qualify; cap extra paid calls across the whole run.
                        if attempt == 0 and retry_attempts < 2 and e.code == "provider_error" and e.http_status in {500, 502, 503, 504}:
                            retry_attempts += 1
                            progress(int(index / max(1, len(keywords)) * 90), f"{keyword}遇到服务端临时故障，稍候重试一次（本轮最多2次）")
                            self.stop.wait(2)
                            continue
                        failures.append({"keyword":keyword,"code":e.code,"message":e.message,"http_status":e.http_status,"attempts":attempt + 1})
                        log.warning("keyword search failed: keyword=%r code=%s http_status=%s attempts=%s", keyword, e.code, e.http_status, attempt + 1)
                        break
                if response is None:
                    continue
                data = response.get("data") or {}
                items = extract_videos(response)
                if not items and isinstance(data, dict) and (data.get("business_data") or data.get("data")):
                    raise TaskError("invalid_response", "搜索接口返回结构发生变化，请检查；未误报没有热点")
                for raw in items:
                    try:
                        video = normalize_video(raw)
                        added += int(ingest(self.store, video, "search", keyword))
                        observed_ids.add(video['id'])
                    except TaskError as e:
                        if e.code != "invalid_response":
                            raise
            progress(75, "正在核实高互动候选的作者粉丝数")
            candidates = self.store.rows("SELECT author_id,MAX(likes) peak FROM videos WHERE id IN (SELECT value FROM json_each(?)) AND author_id IS NOT NULL AND likes>=? AND published_at>=? GROUP BY author_id ORDER BY peak DESC LIMIT 20", (encode(sorted(observed_ids)), self.store.settings()["low_min_likes"], (datetime.now(timezone.utc)-timedelta(days=7)).isoformat()))
            profile_failures=[]
            for candidate in candidates:
                try:
                    author_profile(self.store, TikHub(self.store), candidate['author_id'])
                except TaskError as error:
                    if error.code in {'cancelled','configuration_required','pricing_required','credential_error','balance_required','budget_exceeded','rate_limited'}:raise
                    profile_failures.append({'id':candidate['author_id'],'code':error.code,'message':error.message})
            self.classify_pending()
            last = self.store.settings()["last_sync"]
            if len(failures)<len(keywords):
                last["search"] = now()
                self.store.set("last_sync", last)
            return {"keywords": len(keywords), "searched_keywords":keywords, "completed_keywords": len(keywords) - len(failures), "added": added, "failed_keywords": failures, "failed_profiles":profile_failures, "request_attempts": request_attempts, "retry_attempts": retry_attempts}
        if kind == "classify":
            progress(15, "正在按账号定位分类与判断可执行性")
            return classify(self.store, payload["ids"][:20])
        if kind == "transcribe":
            result = transcribe(self.store, payload["video_id"], progress)
            if get_secret(self.store, "deepseek") and self.store.settings()["pricing"]["confirmed"]:
                self.enqueue("classify", {"ids": [payload["video_id"]]}, "classify:" + payload["video_id"])
            return result
        if kind == "analyze":
            if not self.store.one("SELECT video_id FROM transcripts WHERE video_id=?", (payload["video_id"],)):
                transcribe(self.store, payload["video_id"], lambda v, m: progress(int(v * .5), m))
            progress(55, "正在深度拆解原文结构与可复用方法，可随时取消")
            return analyze(self.store, payload["video_id"], force=payload.get("force", False))
        if kind in {"hooks", "script"}:
            project = self.store.one("SELECT * FROM projects WHERE id=?", (payload["project_id"],))
            revision = payload["revision"]
            if not project:
                raise TaskError("not_found", "项目不存在")
            if project["revision"] != revision:
                raise TaskError("revision_conflict", "稿件已改变，请使用最新版本；本次未开始分析")
            if project["creation_mode"] == "commercial":
                if not json.loads(project["brief"]).get("product", "").strip():
                    raise TaskError("brief_required", "请先在「要求与参考」填入产品或甲方Brief")
                if kind == "script" and not project["selected_hook"].strip():
                    raise TaskError("hook_required", "请先选定或写好开头，再生成正文")
                if json.loads(project["reference_ids"]) and not json.loads(project["reference_analysis"]):
                    from .commercial import analyze_references
                    progress(1, "先拆解已选参考，再生成稿件；可随时取消")
                    result = analyze_references(self.store, payload["project_id"], revision, lambda v, m: progress(int(v * .5), m), job_id)
                    if result.get("conflict"):
                        return {**result, "message": "参考拆解已保留在本任务结果中；稿件有新修改，未继续生成"}
                    self.check_job(job_id)
                    revision = result["revision"]
            elif kind == "hooks" and not self.store.one("SELECT video_id FROM transcripts WHERE video_id=?", (project["video_id"],)):
                transcribe(self.store, project["video_id"], lambda v, m: progress(int(v * .5), m))
            progress(55, "正在深度思考并生成3个开头，可随时取消" if kind == "hooks" else "正在深度思考并生成正文与录屏清单，可随时取消")
            return generate_project(self.store, payload["project_id"], kind, revision, job_id=job_id)
        if kind == "backup":
            return backup(self.store)
        raise TaskError("invalid_job", "不支持的任务类型")

    def sync_creator(self, api, creator, stats):
        from .services import age_hours
        age=age_hours(creator.get('updated_at'))
        profile = creator if creator.get('followers') is not None and age is not None and 0<=age<24 else author_profile(self.store, api, creator['id'])
        profile_at = profile.get('sampled_at') or creator.get('updated_at')
        cursor = 0
        for page in range(3):
            check_cancelled()
            response = api.posts(creator["id"], cursor)
            data = response.get("data") or {}
            items = extract_videos(response)
            if not items and data.get("has_more"):
                raise TaskError("invalid_response", "作者列表解析失败，未当作没有新作品")
            ages = []
            for raw in items:
                v = normalize_video(raw)
                v["followers"] = profile.get("followers")
                v["followers_at"] = profile_at
                v["author_id"] = v.get("author_id") or creator["id"]
                stats['added'] += int(ingest(self.store, v, "creator", creator["id"]))
                stats['scanned'] += 1
                if not raw.get("is_top") and not raw.get("is_pinned"):
                    ages.append(bool(v["published_at"] and datetime.fromisoformat(v["published_at"]) < datetime.now(timezone.utc) - timedelta(days=7)))
            next_cursor = data.get("max_cursor")
            if bool(ages) and all(ages) or not data.get("has_more") or not items:
                break
            if next_cursor is None or next_cursor == cursor:
                raise TaskError("pagination_stalled", "作者作品分页异常")
            cursor = next_cursor

    def classify_pending(self):
        check_cancelled()
        if get_secret(self.store, "deepseek") and self.store.settings()["pricing"]["confirmed"]:
            ids = [r["id"] for r in self.store.rows("SELECT id FROM videos WHERE manual_classification=0 AND classification_method LIKE '初步分类%' ORDER BY discovered_at DESC LIMIT 20")]
            if ids:
                self.enqueue("classify", {"ids": ids}, "auto-classify")

    def schedule_loop(self):
        while not self.stop.wait(15):
            try:
                self.schedule_tick()
            except Exception as e:
                log.error("scheduler error: %s", type(e).__name__)

    def schedule_tick(self, current=None):
        EdgeBridge(self.store).expire()
        settings = self.store.settings()
        current = current or datetime.now(TZ)
        if settings["automation"]["enabled"]:
            minute = current.hour * 60 + current.minute
            if get_secret(self.store, "tikhub") and settings["pricing"]["confirmed"] and settings["data_pricing_confirmed"]:
                if 8 <= current.hour < 24 and self.store.one("SELECT id FROM creators WHERE enabled=1 LIMIT 1") and self.interval_due("creators_sync", current, settings['automation']['creator_minutes'], settings['last_sync'].get('creators')):
                    self.once(f"creators:{current:%Y-%m-%d}:{minute // settings['automation']['creator_minutes']}", "creators_sync")
                if (current.hour, current.minute) >= (8, 30):
                    # Keep the old morning slot key so upgrading/restarting does
                    # not replay a search already completed earlier today.
                    if not self.daily_search_covered(current, settings['keywords']):
                        self.once(f"search:{current:%Y-%m-%d}:8", "search")
                if settings['hotspots']['enabled'] and (current.hour,current.minute)>=(8,40) and self.interval_due('hotspot_sync',current,10):
                    last_hot=settings['last_sync'].get('hotspots')
                    if not last_hot or datetime.fromisoformat(last_hot).astimezone(TZ).date()!=current.date():
                        self.once(f"hotspots:{current:%Y-%m-%d}",'hotspot_sync')
            collection = settings["collection_state"]
            if collection.get("status") == "authenticated" or collection.get("authenticated_once") and collection.get("status") not in {"login_required", "waiting_login", "verification_required", "pagination_stalled", "no_progress"}:
                interval = settings['automation']['favorites_minutes']
                if self.interval_due("favorites_sync", current, interval, settings['last_sync'].get('favorites'), settings.get('favorites_last_access_at')):
                    self.once(f"favorites:{current:%Y-%m-%d}:{minute // interval}", "favorites_sync")
        if self.store.one("SELECT id FROM auth"):
            self.once(f"backup:{current:%Y-%m-%d}", "backup")

    def daily_search_covered(self, current, keywords):
        expected={w.strip().casefold() for w in keywords if w.strip()}
        for row in self.store.rows("SELECT result,updated_at FROM jobs WHERE kind='search' AND status='succeeded' ORDER BY updated_at DESC LIMIT 20"):
            if datetime.fromisoformat(row['updated_at']).astimezone(TZ).date()!=current.date():continue
            result=json.loads(row['result'] or '{}')
            if expected and not result.get('failed_keywords') and {w.casefold() for w in result.get('searched_keywords',[])}==expected:return True
        return False

    def interval_due(self, kind, current, minutes, *anchors):
        latest = self.store.one("SELECT created_at FROM jobs WHERE kind=? ORDER BY created_at DESC LIMIT 1", (kind,))
        stamps = [*anchors, latest['created_at'] if latest else None]
        valid = []
        for stamp in stamps:
            try:
                value = datetime.fromisoformat(stamp)
                valid.append(value if value.tzinfo else value.replace(tzinfo=timezone.utc))
            except (TypeError, ValueError):
                pass
        return not valid or current - max(valid) >= timedelta(minutes=minutes)

    def once(self, slot, kind, payload=None):
        from .discovery import RETIRED_JOBS
        if kind in RETIRED_JOBS:raise TaskError('feature_retired','该功能已退出工作台，历史资料保留')
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            if c.execute("SELECT 1 FROM schedule_runs WHERE slot=?", (slot,)).fetchone():
                return
            key = self.job_key(kind, kind)
            if c.execute("SELECT 1 FROM jobs WHERE dedup_key=? AND status IN ('queued','running')",(key,)).fetchone():
                return
            c.execute("INSERT INTO schedule_runs VALUES (?,?)", (slot, now()))
            c.execute("INSERT INTO jobs (id,kind,payload,dedup_key,created_at,updated_at) SELECT ?,?,?,?,?,? WHERE NOT EXISTS(SELECT 1 FROM jobs WHERE dedup_key=? AND status IN ('running','queued'))", (uuid.uuid4().hex, kind, encode(payload or {}), key, now(), now(), key))
