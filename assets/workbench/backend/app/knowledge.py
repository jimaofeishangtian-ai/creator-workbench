"""Personal material knowledge, owned Obsidian region and weekly local exports."""
from __future__ import annotations

import hashlib
import csv
import io
import json
import os
import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from .db import encode, now
from .providers import DeepSeek, SHANGHAI, TaskError
from .task_context import check_cancelled

FOLDER = "08-自媒体知识库"
MANUAL = "\n<!-- ai-workbench:manual -->\n"
TRACKS = ["AI教程", "AIGC", "其他"]


def infer_tracks(title, is_ai=None):
    text = title.lower()
    visual_ai = any(w in text for w in ["aigc", "ai短片", "文生视频", "图生视频", "即梦", "可灵", "海螺", "vidu", "seedance", "sora", "flova", "liblib", "数字人"])
    ai_hint = bool(is_ai or visual_ai or re.search(r"(?<![a-z])ai(?![a-z])", text) or any(w in text for w in ['agent','codex','claude','workbuddy','人工智能','大模型']))
    aigc = visual_ai or ai_hint and any(w in text for w in ['商单','广告','画布','运镜','镜头语言'])
    tutorial = any(w in text for w in ["教程", "教学", "实操", "怎么", "如何", "工作流", "知识库", "agent", "codex", "claude", "token", "剪辑", "记忆"])
    result = (["AI教程"] if tutorial and ai_hint or is_ai and not aigc else []) + (["AIGC"] if aigc else [])
    return result or ["其他"]


def tracks_for(row):
    value = row.get("content_tracks")
    if isinstance(value, str):
        value = json.loads(value)
    return value or infer_tracks(row["title"], row.get("is_ai"))


def mark_priority(store, identity, enabled=True):
    if not store.one("SELECT id FROM videos WHERE id=?", (identity,)):
        raise TaskError("not_found", "素材不存在")
    store.run("INSERT INTO knowledge_items(video_id,priority,state,updated_at) VALUES (?,?,?,?) ON CONFLICT(video_id) DO UPDATE SET priority=excluded.priority,state=CASE WHEN excluded.priority=1 AND knowledge_items.state!='ready' THEN 'pending' ELSE knowledge_items.state END,updated_at=excluded.updated_at", (identity, int(enabled), "pending" if enabled else "indexed", now()))


def knowledge_current(item, transcript):
    if not item or item["state"] != "ready":
        return False
    payload = item.get("payload") or {}
    if isinstance(payload, str):
        payload = json.loads(payload)
    if "transcript_hash" in payload:
        return payload["transcript_hash"] == (transcript or {}).get("content_hash")
    return not transcript or transcript["updated_at"] <= item["updated_at"]


def reuse_analysis(row, visual):
    analysis = (row.get("analysis") or {}).get("payload", {})
    if not analysis and not visual:
        return None
    visual = visual or {}
    return {
        "summary": analysis.get("summary") or visual.get("summary"),
        "takeaways": [*analysis.get("opportunities", []), *visual.get("reuse", [])],
        "steps": analysis.get("steps", []),
        "opening_reference": analysis.get("hook") or "；".join(
            f"{shot['second']}秒：{shot['visual']}" for shot in visual.get("opening", [])
            if isinstance(shot, dict) and "second" in shot and shot.get("visual")
        ),
        "applications": [analysis["angle"]] if analysis.get("angle") else [],
        "verification": list(dict.fromkeys([*analysis.get("verification", []), *visual.get("verification", [])])),
        "reused_analysis": True,
    }


def deep_read(store, identity, progress, *, force=False):
    from .services import detail
    from .transcription import transcribe
    mark_priority(store, identity)
    try:
        row = detail(store, identity)
        tracks = tracks_for(row)
        progress(5, "准备重点素材，只处理人工标记或项目引用的内容")
        if not row["transcript"] and row.get("media_kind") != "image" and (force or not row["visual_analysis"]):
            try:
                transcribe(store, identity, lambda v, m: progress(int(v * .35), m))
            except TaskError as error:
                if error.code not in {"no_speech", "audio_error"} or "AIGC" not in tracks:
                    raise
            row = detail(store, identity)
        visual = row["visual_analysis"]
        if "AIGC" in tracks and row.get("media_kind") != "image" and not visual and (force or not row["analysis"]):
            from .visual import analyze_visual
            visual = analyze_visual(store, identity, lambda v, m: progress(35 + int(v * .3), m))
        text = (row["transcript"] or {}).get("text", "")
        if not text and not visual:
            raise TaskError("transcript_required", "需要真实正文或可取得的画面；图文请先粘贴正文")
        progress(75, "提炼可复用知识，区分原作者说法与待验证事项")
        source = {"title": row["title"], "author": row["author"], "transcript": text[:20000], "visual": visual, "tracks": tracks}
        needs_transcript_analysis = bool(text and "AI教程" in tracks and not row["analysis"])
        result = None if force or needs_transcript_analysis else reuse_analysis(row, visual)
        if result is None:
            result = DeepSeek(store).json("knowledge", source, '输出json：{"summary":"基于原文/采样画面的摘要","takeaways":["可复用方法或创意"],"steps":["实际操作或制作步骤"],"opening_reference":"可借鉴的开头结构","applications":["适用的教程或商单场景"],"verification":["仍须核验的说法"]}。没有口播时不能编造逐字稿；抽帧不代表完整看过视频，画面之外的声音与连续运动不能断言。', 3200)
        if not isinstance(result.get("summary"), str) or not result["summary"].strip():
            raise TaskError("invalid_response", "深读缺少有效摘要，原记录保留")
        for key in ["takeaways", "steps", "applications", "verification"]:
            if not isinstance(result.get(key, []), list) or not all(isinstance(s, str) for s in result.get(key, [])):
                raise TaskError("invalid_response", "深读结构不完整，原记录保留")
        result["visual"] = visual
        result["transcript_hash"] = (row["transcript"] or {}).get("content_hash")
        old = store.one("SELECT payload FROM knowledge_items WHERE video_id=?", (identity,))
        result["version"] = json.loads(old["payload"] or "{}").get("version", 0) + 1
        current_text = store.one("SELECT text FROM transcripts WHERE video_id=?", (identity,))
        if (current_text or {}).get("text", "") != text:
            raise TaskError("revision_conflict", "深读期间原文发生修改，未将旧结果覆盖为最新知识卡，请重新深读")
        check_cancelled()
        store.run("UPDATE knowledge_items SET state='ready',payload=?,error=NULL,updated_at=? WHERE video_id=?", (encode(result), now(), identity))
        return {"video_id": identity, "summary": result["summary"]}
    except TaskError as error:
        store.run("UPDATE knowledge_items SET state='attention',error=?,updated_at=? WHERE video_id=?", (error.message, now(), identity))
        raise


def clean(value):
    text = str(value or "")
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}", "[密钥已移除]", text)
    text = re.sub(r"(?im)(Authorization\s*[:：]\s*|Bearer\s+)[^\s\n]+", "[授权信息已移除]", text)
    text = re.sub(r"data:(?:image|video|audio)/[^;]+;base64,[A-Za-z0-9+/=]+", "[媒体数据已移除]", text)
    return text


def frontmatter(data):
    return "---\n" + "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in data.items()) + "\n---\n\n"


def destination(store):
    config = store.settings()["knowledge"]
    vault = Path(config.get("vault_path", "")).expanduser()
    if not config.get("enabled") or not vault.is_absolute() or not (vault / ".obsidian").is_dir():
        raise TaskError("configuration_required", "请先在知识库设置中选择现有Obsidian库并启用")
    vault = vault.resolve()
    root = vault / FOLDER
    if root.resolve().parent != vault or root.is_symlink():
        raise TaskError("invalid_path", "知识区目录不允许跳转到库外")
    return root


def write_owned(store, root, relative, generated):
    target = root / relative
    if not target.resolve().is_relative_to(root.resolve()) or any(p.is_symlink() for p in [target, *target.parents] if p != root.parent):
        raise TaskError("invalid_path", "导出路径超出已确定的知识区")
    generated = clean(generated)
    digest = hashlib.sha256(generated.encode()).hexdigest()
    key = str(target.resolve())
    previous = store.one("SELECT generated_hash FROM knowledge_exports WHERE path=?", (key,))
    tail = "\n## 我的笔记\n\n"
    yaml_file = target.suffix in {".base", ".csv"}
    if target.exists():
        content = target.read_text(encoding="utf-8")
        if (not yaml_file and MANUAL not in content) or not previous:
            return "conflict"
        old_generated, tail = (content, "") if yaml_file else content.split(MANUAL, 1)
        if hashlib.sha256(old_generated.encode()).hexdigest() != previous["generated_hash"]:
            return "conflict"
        if digest == previous["generated_hash"]:
            return "unchanged"
    target.parent.mkdir(parents=True, exist_ok=True)
    check_cancelled()
    descriptor, temporary = tempfile.mkstemp(prefix=".workbench-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(generated if yaml_file else generated + MANUAL + tail)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    store.run("INSERT INTO knowledge_exports VALUES (?,?,?) ON CONFLICT(path) DO UPDATE SET generated_hash=excluded.generated_hash,updated_at=excluded.updated_at", (key, digest, now()))
    return "written"


def source_note(row, item):
    payload = json.loads(item.get("payload") or "{}")
    tracks = tracks_for(row)
    meta = {"id": "SRC-VIDEO-" + row["id"], "title": clean(row["title"][:240]), "platform": "抖音", "video_id": row["id"], "author": clean(row["author"]), "source_url": row["url"], "themes": tracks, "category": row["category"], "tools": json.loads(row["tools"]), "purposes": json.loads(row["purposes"]), "priority": bool(item.get("priority")), "reading_state": item.get("state", "indexed"), "published_at": row["published_at"], "collected_at": row["collected_at"], "first_seen": row["discovered_at"], "tags": ["自媒体/收藏素材", *["自媒体/" + t for t in tracks]]}
    meta["origins"] = row.get("source_kinds", [])
    meta["personal_collection"] = any(kind in {"favorites", "manual"} for kind in row.get("source_kinds", []))
    text = frontmatter(meta) + f"# {clean(row['title'][:240])}\n\n[打开原作品]({row['url']})\n\n"
    text += f"- 内容：{'、'.join(tracks)} / {row['category']}\n- 发布时间：{row['published_at'] or '未知'}\n- 收藏时间：{row['collected_at'] or '未知；不能用发布时间替代'}\n- 首次入库：{row['discovered_at']}\n- 点赞：{row['likes'] if row['likes'] is not None else '未知'}；粉丝：{row['followers'] if row['followers'] is not None else '未知'}\n\n"
    text += "## 摘要\n\n" + clean(payload.get("summary") or "初步索引，仅依据标题、简介与现有分类；尚未全文深读。") + "\n\n"
    if payload:
        text += f"## 知识卡\n\n[[{FOLDER}/02-可复用知识/CAS-DY-{row['id']}|查看提炼与验证事项]]\n\n"
    if row.get("transcript"):
        text += "## 原始逐字稿\n\n" + clean(row["transcript"]) + "\n\n"
    if item.get("error"):
        text += "## 待处理\n\n" + clean(item["error"]) + "\n"
    return text


def knowledge_note(row, item):
    p = json.loads(item["payload"])
    relative = f"{FOLDER}/01-收藏素材/抖音/DY-{row['id']}"
    metadata = {"id": "CAS-DY-" + row["id"], "type": "CAS", "title": clean(row["title"][:200]), "source_documents": [f"[[{relative}]]"], "source_authors": [clean(row["author"])], "themes": tracks_for(row), "keywords": json.loads(row["tools"]), "status": "待验证", "canonical": True, "version": p.get("version", 1), "created_at": row["discovered_at"], "updated_at": item["updated_at"], "relationships": [], "case_subject": clean(row["title"][:200]), "case_summary": clean(p["summary"]), "case_process": "依据原文及明确标注的采样画面整理", "case_result": "作者演示或主张，未经用户亲测不视为已验证", "tags": ["自媒体/知识卡"]}
    result = frontmatter(metadata) + f"# {clean(row['title'][:200])}\n\n来源：[[{relative}|原始素材]]\n\n" + clean(p["summary"]) + "\n\n"
    for key, title in [("takeaways", "可复用方法"), ("steps", "操作或制作步骤"), ("applications", "适用场景"), ("verification", "待验证")]:
        result += f"## {title}\n\n" + "\n".join("- " + clean(s) for s in p.get(key, [])) + "\n\n"
    result += "## 开头参考\n\n" + clean(p.get("opening_reference", "")) + "\n"
    return result


def sync_vault(store, progress, *, period=None):
    from .services import PERSONAL_SCOPE_SQL
    root = destination(store)
    rows = store.rows("SELECT v.*,t.text transcript FROM videos v LEFT JOIN transcripts t ON t.video_id=v.id WHERE " + PERSONAL_SCOPE_SQL + " OR EXISTS(SELECT 1 FROM knowledge_items k WHERE k.video_id=v.id AND k.priority=1) ORDER BY v.discovered_at DESC")
    items = {r["video_id"]: r for r in store.rows("SELECT * FROM knowledge_items")}
    origins = {}
    for origin in store.rows("SELECT video_id,kind FROM sources"):
        origins.setdefault(origin["video_id"], []).append(origin["kind"])
    written, unchanged, conflicts = 0, 0, []
    def emit(relative, text):
        nonlocal written, unchanged
        outcome = write_owned(store, root, relative, text)
        if outcome == "conflict": conflicts.append(relative)
        elif outcome == "written": written += 1
        else: unchanged += 1
    for index, row in enumerate(rows):
        check_cancelled()
        row["source_kinds"] = list(dict.fromkeys(origins.get(row["id"], [])))
        item = items.get(row["id"], {})
        emit(f"01-收藏素材/抖音/DY-{row['id']}.md", source_note(row, item))
        if item.get("state") == "ready" and json.loads(item.get("payload") or "{}").get("summary"):
            emit(f"02-可复用知识/CAS-DY-{row['id']}.md", knowledge_note(row, item))
        progress(int((index + 1) / max(1, len(rows)) * 85), f"整理本机已收录素材 {index+1}/{len(rows)}；不访问抖音")
    registry = io.StringIO(newline="")
    writer = csv.writer(registry, lineterminator="\n")
    writer.writerow(["source_id", "platform", "video_id", "title", "author", "url", "first_seen", "note"])
    for row in rows:
        writer.writerow(["SRC-VIDEO-"+row["id"], "抖音", row["id"], clean(row["title"]), clean(row["author"]), row["url"], row["discovered_at"], f"01-收藏素材/抖音/DY-{row['id']}.md"])
    emit("00-入口与分类/来源注册表.csv", registry.getvalue())
    from .services import project
    for project_row in store.rows("SELECT id FROM projects ORDER BY updated_at DESC"):
        p = project(store, project_row["id"])
        refs = list(dict.fromkeys(([p["video_id"]] if p["video_id"] else []) + p["reference_ids"]))
        text = frontmatter({"title": clean(p["title"][:200]), "project_id": p["id"], "creation_mode": p["creation_mode"], "stage": p["stage"], "revision": p["revision"], "tags": ["自媒体/创作项目"]}) + f"# {clean(p['title'][:200])}\n\n工作台稿件v{p['revision']} · {p['stage']}\n\n"
        text += "## 参考素材\n\n" + "\n".join(f"- [[{FOLDER}/01-收藏素材/抖音/DY-{v}]]" for v in refs) + "\n\n"
        if p["brief"]:
            text += "## 商单brief\n\n" + "\n".join(f"- {k}：{clean(v)}" for k, v in p["brief"].items()) + "\n\n"
        text += "## 确定的开头\n\n" + clean(p["selected_hook"]) + "\n\n## 脚本\n\n" + clean(p["content"]) + "\n\n## 分镜与拍摄清单\n\n"
        for shot in p["shooting_plan"]:
            text += "\n" + "\n".join(f"- {k}：{clean(v)}" for k,v in shot.items() if v) + "\n"
        text += "\n## 待核实\n\n" + "\n".join("- " + clean(v) for v in p["verification"])
        emit(f"03-教程与商单参考/PROJECT-{p['id']}.md", text)
    stamp = datetime.now(SHANGHAI)
    period = period or stamp.strftime("%G-W%V")
    if not re.fullmatch(r"\d{4}-W\d{2}", period):
        raise TaskError("invalid_input", "周整理标识无效")
    year, week = period.split("-W")
    start = datetime.fromisocalendar(int(year), int(week), 1).replace(tzinfo=SHANGHAI)
    end = start + timedelta(days=7)
    additions = [r for r in rows if start <= datetime.fromisoformat(r["discovered_at"]).astimezone(SHANGHAI) < end]
    report = {"period": period, "generated_at": now(), "indexed": len(rows), "ready": sum(r.get("state") == "ready" for r in items.values()), "written": written, "unchanged": unchanged, "conflicts": conflicts, "new_items": [{"id": r["id"], "title": clean(r["title"][:180]), "tracks": tracks_for(r)} for r in additions], "time_basis": "按首次入库时间归周；收藏时间未知的记录不冒充本周收藏"}
    report["highlights"] = [{"id": row["id"], "title": clean(row["title"][:120]), "summary": clean(json.loads(items[row["id"]]["payload"]).get("summary", ""))[:600], "applications": json.loads(items[row["id"]]["payload"]).get("applications", [])[:3]} for row in rows if items.get(row["id"], {}).get("state") == "ready"][:30]
    report_md = frontmatter({"title": period + "自媒体收藏整理", "period": period, "tags": ["自媒体/每周整理"]}) + f"# {period} 收藏整理\n\n本机已收录{len(rows)}条，重点深读完成{report['ready']}条。\n\n{report['time_basis']}。首次索引只代表已同步内容，不代表抖音全部历史收藏。\n\n## 本周首次入库\n\n"
    for row in additions:
        report_md += f"- [[{FOLDER}/01-收藏素材/抖音/DY-{row['id']}|{clean(row['title'][:120]).replace('|',' / ').replace(']','）')}]]\n"
    report_md += "\n## 重点知识与可用方向\n\n"
    for entry in report["highlights"]:
        report_md += f"### [[{FOLDER}/02-可复用知识/CAS-DY-{entry['id']}|{entry['title'].replace('|',' / ').replace(']','）')}]]\n\n{entry['summary']}\n\n" + "\n".join("- 可用于："+clean(s) for s in entry["applications"]) + "\n\n"
    if conflicts:
        report_md += "\n## 人工改动保留\n\n以下生成区发生人工修改，已跳过覆盖：\n" + "\n".join("- " + p for p in conflicts)
    emit(f"04-每周整理/{period}.md", report_md)
    base = 'filters:\n  and:\n    - \'file.inFolder("' + FOLDER + '/01-收藏素材")\'\n    - \'file.ext == "md"\'\nviews:\n  - type: table\n    name: 全部素材\n    order: [file.name, note.title, note.themes, note.category, note.tools, note.priority, note.reading_state, note.author, note.source_url]\n  - type: table\n    name: 我的收藏\n    filters: \'personal_collection == true\'\n    order: [file.name, note.title, note.themes, note.category, note.source_url]\n  - type: table\n    name: 重点深读\n    filters: \'priority == true\'\n    order: [file.name, note.title, note.themes, note.reading_state, note.source_url]\n'
    base = base.replace('views:\n', 'formulas:\n  display_title: \'file.asLink(note.title)\'\nproperties:\n  formula.display_title:\n    displayName: 作品\n  note.themes:\n    displayName: 内容方向\n  note.category:\n    displayName: 主题\n  note.tools:\n    displayName: 工具\n  note.priority:\n    displayName: 重点\n  note.reading_state:\n    displayName: 深读状态\n  note.author:\n    displayName: 作者\n  note.source_url:\n    displayName: 原链接\nviews:\n').replace('file.name, note.title', 'formula.display_title').replace('    order:', '    sort:\n      - property: note.first_seen\n        direction: DESC\n    order:')
    # Bases is YAML, so protect it as a whole file via the same owned-region mechanism.
    emit("00-入口与分类/收藏索引.base", base)
    home = frontmatter({"title": "自媒体知识库", "tags": ["自媒体/入口"]}) + f"# 自媒体知识库\n\n已索引 **{len(rows)}** 条本机收藏与重点素材；深读完成 **{report['ready']}** 条。\n\n![[{FOLDER}/00-入口与分类/收藏索引.base]]\n\n## 最近整理\n\n[[{FOLDER}/04-每周整理/{period}|查看本周整理]]\n\n## 使用方式\n\n- 在工作台收藏列表点「标为重点」；选题或商单项目引用也会触发深读。\n- 自动生成区上方请在工作台修改；下方「我的笔记」保留你的手写内容。生成区被手改时会停止覆盖并提示。\n- 资料来自作者说法及模型提炼，未经亲测不当作已验证结论。\n- 每周日21:00（北京时间）整理，电脑下次上线补跑；视频留在工作台缓存，不批量复制进笔记库。\n"
    emit("自媒体知识库.md", home)
    report["written"], report["unchanged"], report["conflicts"] = written, unchanged, conflicts
    store.run("INSERT INTO knowledge_reports VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at", (period, encode(report), now()))
    store.set("knowledge_last_sync", {"at": now(), "period": period, "indexed": len(rows), "conflicts": len(conflicts)})
    return report


def latest_due(config, current):
    if not config.get("enabled") or not config.get("enabled_at"):
        return None
    current = current.astimezone(SHANGHAI)
    due = current.replace(hour=21, minute=0, second=0, microsecond=0) - timedelta(days=(current.weekday()-6) % 7)
    if due > current:
        due -= timedelta(days=7)
    if due < datetime.fromisoformat(config["enabled_at"]).astimezone(SHANGHAI):
        return None
    return due.strftime("%G-W%V")


def status(store):
    config = store.settings()["knowledge"]
    reports = [dict(id=r["id"], **json.loads(r["payload"])) for r in store.rows("SELECT * FROM knowledge_reports ORDER BY updated_at DESC LIMIT 8")]
    items = store.rows("SELECT k.video_id,k.priority,k.state,k.error,k.updated_at,v.title,v.content_tracks,v.is_ai FROM knowledge_items k JOIN videos v ON v.id=k.video_id WHERE k.priority=1 ORDER BY k.updated_at DESC LIMIT 100")
    return {"config": config, "last_sync": store.settings().get("knowledge_last_sync"), "reports": reports, "items": [{**r, "content_tracks": tracks_for(r)} for r in items], "counts": {r["state"]: r["n"] for r in store.rows("SELECT state,COUNT(*) n FROM knowledge_items WHERE priority=1 GROUP BY state")}}
