import { useEffect, useRef, useState } from "react";
import { Check, LoaderCircle, Plus, Search } from "lucide-react";
import { api, ApiError } from "./api";

type Row = Record<string, any>;

export default function CommercialReferencePicker({
  video,
  openProject,
  createProject,
  added,
  back,
}: {
  video: Row;
  openProject: (id: string) => void;
  createProject: () => Promise<void>;
  added: () => void;
  back: () => void;
}) {
  const [projects, setProjects] = useState<Row[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<Row | null>(null);
  const saving = useRef(false);
  const mounted = useRef(true);

  async function load() {
    setLoading(true);
    try {
      const rows = await api<Row[]>("/projects");
      if (mounted.current)
        setProjects(rows.filter(project => project.creation_mode === "commercial" && project.stage !== "已定稿"));
    } catch (e) {
      if (mounted.current) setError((e as Error).message);
    } finally {
      if (mounted.current) setLoading(false);
    }
  }
  useEffect(() => {
    mounted.current = true;
    load();
    return () => { mounted.current = false; };
  }, []);

  async function addReference(project: Row) {
    if (saving.current) return;
    saving.current = true;
    setBusy(true);
    setError("");
    try {
      // Closed editors can retain local edits. Let the user save these first.
      if (sessionStorage.getItem("draft-" + project.id)) {
        openProject(project.id);
        return;
      }
      await api("/projects/" + project.id, "PATCH", {
        revision: project.revision,
        changes: { reference_ids: [...project.reference_ids, video.id] },
      });
      added();
      if (mounted.current) setSelected(project);
    } catch (e) {
      if (mounted.current) {
        setError((e as ApiError).code === "revision_conflict"
          ? "这条商单刚有更新，请核对最新参考数量后重新选择。"
          : (e as Error).message);
        await load();
      }
    } finally {
      saving.current = false;
      if (mounted.current) setBusy(false);
    }
  }

  async function create() {
    if (saving.current) return;
    saving.current = true;
    setBusy(true);
    setError("");
    try {
      await createProject();
    } catch (e) {
      if (mounted.current) setError((e as Error).message);
    } finally {
      saving.current = false;
      if (mounted.current) setBusy(false);
    }
  }

  if (selected) return <div className="modal-content commercial-reference-picker">
    <div className="reference-added" role="status">
      <Check size={22} />
      <div><h3>已加入「{selected.title || "未命名商单"}」</h3><p>{video.title}</p></div>
    </div>
    <div className="card-buttons">
      <button className="button primary" onClick={() => openProject(selected.id)}>继续写这条商单</button>
      <button className="button" onClick={back}>返回素材</button>
    </div>
  </div>;

  const visible = projects.filter(project =>
    [project.title, project.brief?.product, project.brief?.goal].some(value => value?.toLowerCase().includes(query.trim().toLowerCase())));

  return <div className="modal-content commercial-reference-picker">
    <p className="reference-source">要加入的素材 <strong>{video.title}</strong></p>
    <p className="hint">选择正在做的商单，每条最多4个参考。引用后按知识库设置安排重点深读，费用计入预算。</p>
    <label className="search-field"><Search size={17} /><input
      aria-label="搜索已有商单"
      placeholder="搜索商单标题或产品…"
      value={query}
      onChange={event => setQuery(event.target.value)}
    /></label>
    {error && <p className="hint" role="alert">{error}</p>}
    {loading ? <div className="loading-block"><LoaderCircle className="spin" size={22} /></div> : <div className="commercial-reference-list">
      {visible.map(project => {
        const included = project.reference_ids.includes(video.id);
        const full = project.reference_ids.length >= 4;
        const hasDraft = !!sessionStorage.getItem("draft-" + project.id);
        return <button
          key={project.id}
          className="commercial-reference-option"
          disabled={busy || included || (full && !hasDraft)}
          onClick={() => addReference(project)}
        >
          <span><strong>{project.title || "未命名商单"}</strong><small>{project.brief?.product || "产品待补充"} · {project.reference_ids.length}/4 条参考{hasDraft ? " · 先打开保存草稿" : ""}</small></span>
          <span>{included ? "已加入" : hasDraft ? "打开草稿" : full ? "参考已满" : busy ? "处理中…" : "加入"}</span>
        </button>;
      })}
      {!visible.length && <p className="hint">{query.trim() ? "没有找到这条商单，换个词或清空搜索。" : "还没有进行中的商单，可以带这条素材新建。"}</p>}
    </div>}
    <div className="card-buttons">
      <button className="button" onClick={back} disabled={busy}>返回素材</button>
      <button className="button" onClick={() => { setError(""); load(); }} disabled={busy || loading}>刷新商单列表</button>
      <button className="button" onClick={create} disabled={busy}><Plus size={15} />新建商单</button>
    </div>
  </div>;
}
