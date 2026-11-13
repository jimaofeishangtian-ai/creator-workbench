from .db import now
from .providers import DeepSeek, TaskError
from .services import project, update_project
from .visual import analyze_visual


BRIEF_FIELDS = {"product", "audience", "goal", "selling_points", "must_include", "forbidden", "style", "deliverables"}


def analyze_references(store, identity, revision, progress, job_id=None):
    p = project(store, identity)
    if p["revision"] != revision:
        raise TaskError("revision_conflict", "商单已更新，请使用最新版本")
    if p["creation_mode"] != "commercial":
        raise TaskError("invalid_input", "请先切换为AIGC商单模式")
    references = []
    for index, video_id in enumerate(p["reference_ids"]):
        visual = analyze_visual(store, video_id, lambda v, m: progress(int((index+v/100)/max(1,len(p["reference_ids"]))*80), m))
        row = store.one("SELECT title,author,url FROM videos WHERE id=?", (video_id,))
        transcript = store.one("SELECT text FROM transcripts WHERE video_id=?", (video_id,))
        references.append({"id": video_id, **row, "transcript": (transcript or {}).get("text", "")[:2500], "visual": visual})
    if not references:
        raise TaskError("reference_required", "先从已有素材中选择1—4条参考作品")
    progress(85, "把画面与开头结构匹配本次brief，保留每条来源")
    result = DeepSeek(store).json("commercial_analysis", {"brief": p["brief"], "references": references}, '输出json：{"summary":"本次商单可借鉴的创意与边界","opening_reference":["来源ID、前5秒可见结构、如何适配本次产品"],"structure":["中段与结尾的可复用镜头结构及来源ID"],"angles":["本次可选创意方向"],"verification":["产品事实、声音或未采样画面需核验"]}。参考画面是稀疏采样，不能声称知道连续运动和精确剪辑点，不编造参考原声。', 3800)
    if not isinstance(result.get("summary"), str) or not isinstance(result.get("opening_reference"), list):
        raise TaskError("invalid_response", "商单拆解字段不完整，原稿保留")
    result["sources"] = [{"id": r["id"], "title": r["title"], "url": r["url"], "sample_seconds": r["visual"]["sample_seconds"]} for r in references]
    result["generated_at"] = now()
    try:
        updated = update_project(store, identity, revision, {"reference_analysis": result}, "商单参考画面拆解", job_id=job_id)
    except TaskError as error:
        if error.code in {"revision_conflict", "cancelled"}:
            return {"project_id": identity, "conflict": True, "generated": {"reference_analysis": result}, "message": "分析完成但稿件已改变，结果保留在任务中"}
        raise
    return {"project_id": identity, "revision": updated["revision"]}
