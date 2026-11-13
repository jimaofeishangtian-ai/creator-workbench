from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

from .db import Store, now
from .providers import TaskError, normalize_video
from .services import ingest


@contextmanager
def profile_lock(root: Path):
    path = root / "browser-profile.lock"
    handle = path.open("a+b")
    try:
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise TaskError("profile_busy", "抖音会话正在被另一个任务使用，请稍后重试")
        yield
    finally:
        handle.close()


class FavoritesCollector:
    def __init__(self, store: Store, progress):
        self.store, self.progress = store, progress

    def state(self, status, message):
        previous = self.store.settings()["collection_state"]
        self.store.set("collection_state", {"status": status, "message": message, "updated_at": now(), "authenticated_once": status == "authenticated" or previous.get("authenticated_once", False)})

    def run(self, mode="sync"):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise TaskError("dependency_required", "缺少浏览器采集依赖，请运行安装脚本")
        with profile_lock(self.store.root), sync_playwright() as p:
            context = None
            for channel in ["msedge", "chrome", None]:
                try:
                    kwargs = {"user_data_dir": str(self.store.root / "douyin-profile"), "headless": mode != "login", "viewport": {"width": 1280, "height": 850}, "locale": "zh-CN"}
                    if channel:
                        kwargs["channel"] = channel
                    context = p.chromium.launch_persistent_context(**kwargs)
                    break
                except Exception:
                    continue
            if context is None:
                raise TaskError("browser_required", "未找到可用的Edge、Chrome或Chromium浏览器")
            try:
                page = context.pages[0] if context.pages else context.new_page()
                self.progress(5, "正在连接独立抖音会话")
                page.goto("https://www.douyin.com/user/self?showTab=favorite_collection", wait_until="domcontentloaded", timeout=45000)

                def fetch(cursor=0, count=20):
                    return page.evaluate("""async ({cursor,count})=>{
                      const params=new URLSearchParams({device_platform:'webapp',aid:'6383',channel:'channel_pc_web',cookie_enabled:String(navigator.cookieEnabled),browser_language:navigator.language,browser_platform:navigator.platform,browser_name:'Chrome'});
                      const r=await fetch('/aweme/v1/web/aweme/listcollection/?'+params,{method:'POST',credentials:'include',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({cursor:String(cursor),count:String(count)}).toString()});
                      if(!r.ok)return {http_status:r.status};
                      try{return await r.json()}catch{return {invalid:true}}
                    }""", {"cursor": cursor, "count": count})

                def authenticated():
                    return any(c["name"] in ("sessionid", "sessionid_ss", "sid_guard") and c["value"] for c in context.cookies("https://www.douyin.com"))

                if mode == "login":
                    self.state("waiting_login", "请在电脑弹出的抖音官方窗口扫码，等待验证完成")
                    self.progress(10, "等待你在电脑上的独立抖音窗口扫码登录，最长15分钟")
                    until = time.monotonic() + 900
                    login_seen = False
                    last_problem = ""
                    while time.monotonic() < until:
                        if authenticated():
                            login_seen = True
                            result = fetch(0, 1)
                            if result.get("status_code") == 0 and isinstance(result.get("aweme_list"), list):
                                self.state("authenticated", "登录与收藏接口已验证")
                                return {"authenticated": True}
                            last_problem = f"HTTP {result['http_status']}" if result.get("http_status") else f"状态码 {result['status_code']}" if result.get("status_code") is not None else "响应不是有效收藏列表"
                            self.state("validating", "已检测到登录，收藏接口尚未验证通过：" + last_problem)
                            self.progress(15, "已登录，正在验证收藏读取：" + last_problem)
                        else:
                            self.progress(10, "等待你在独立抖音窗口扫码并确认登录，最长15分钟")
                        page.wait_for_timeout(2000)
                    if login_seen:
                        raise TaskError("collection_failed", "已检测到抖音登录，但收藏读取验证未通过：" + last_problem)
                    raise TaskError("login_required", "独立采集会话尚未完成登录，请在电脑新弹出的抖音窗口扫码并确认")
                if not authenticated():
                    had_login = self.store.settings()["collection_state"].get("authenticated_once", False)
                    message = "抖音登录已失效，请在「我的」重新扫码" if had_login else "独立采集会话尚未完成登录，请在「我的」连接抖音"
                    raise TaskError("login_required", message)
                if mode == "status":
                    result = fetch(0, 1)
                    if result.get("status_code") != 0 or not isinstance(result.get("aweme_list"), list):
                        raise TaskError("login_required", "登录态未通过收藏读取验证，请重新扫码")
                    self.state("authenticated", "登录与收藏接口已验证")
                    return {"authenticated": True}
                settings = self.store.settings()
                backfill = mode == "backfill"
                pending = settings.get("favorites_pending") or {}
                cursor = int(settings["backfill_cursor"]) if backfill else int(pending.get("cursor", 0))
                limit = 100 if backfill else (300 if not settings["favorites_checkpoint"] else 100)
                stop_ids = set(pending.get("stop_ids") or settings["favorites_checkpoint"]) if not backfill else set()
                head_ids, seen_cursors = list(pending.get("head", [])) if not backfill else [], set()
                scanned = added = 0
                more = True
                while scanned < limit and more:
                    self.progress(10 + int(80 * scanned / limit), f"正在读取收藏：已扫描{scanned}条，新增{added}条")
                    result = fetch(cursor, min(20, limit - scanned))
                    if result.get("status_code") != 0 or not isinstance(result.get("aweme_list"), list):
                        raise TaskError("collection_failed", "收藏接口未返回有效列表；可能需要重新登录或平台接口已变化")
                    items = result["aweme_list"]
                    found_checkpoint = False
                    for raw in items:
                        vid = str(raw.get("aweme_id") or "")
                        if vid in stop_ids:
                            found_checkpoint = True
                        if len(head_ids) < 40:
                            head_ids.append(vid)
                        try:
                            data = normalize_video(raw)
                            # Do not invent collection timestamps from create_time or observed_at.
                            data["collected_at"] = None
                            added += int(ingest(self.store, data, "favorites", "my-favorites"))
                        except TaskError as e:
                            raise TaskError("invalid_response", "收藏中出现无法识别的记录，已停止推进游标，避免误报无新增或漏采") from e
                        scanned += 1
                    more = bool(result.get("has_more")) and bool(items)
                    next_cursor = int(result.get("cursor") or 0)
                    if more and (next_cursor == cursor or next_cursor in seen_cursors):
                        raise TaskError("pagination_stalled", "收藏分页游标未推进，已停止以避免重复采集")
                    seen_cursors.add(cursor)
                    cursor = next_cursor
                    if not backfill and found_checkpoint:
                        more = False
                    if more:
                        page.wait_for_timeout(500)
                if backfill or not settings["favorites_checkpoint"]:
                    self.store.set("backfill_cursor", cursor)
                    self.store.set("backfill_has_more", more)
                if not backfill and head_ids:
                    if more and settings["favorites_checkpoint"]:
                        # Do not advance the watermark until all unseen pages have been scanned.
                        self.store.set("favorites_pending", {"cursor": cursor, "head": head_ids, "stop_ids": list(stop_ids)})
                    else:
                        self.store.set("favorites_checkpoint", head_ids)
                        self.store.set("favorites_pending", {})
                self.state("authenticated", f"收藏同步成功：扫描{scanned}条，新增{added}条；收藏时间未知")
                last = self.store.settings()["last_sync"]
                last["favorites"] = now()
                self.store.set("last_sync", last)
                return {"scanned": scanned, "added": added, "has_more": more, "collection_time_available": False}
            except TaskError as e:
                self.state(e.code, e.message)
                raise
            except Exception:
                self.state("collection_failed", "浏览器或收藏接口异常，请检查电脑上的抖音会话")
                raise TaskError("collection_failed", "浏览器或收藏接口异常；未将失败误报为空收藏")
            finally:
                context.close()
