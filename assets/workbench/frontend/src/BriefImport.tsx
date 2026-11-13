import { useEffect, useRef, useState } from 'react';
import { FileUp, Sparkles, LoaderCircle } from 'lucide-react';
import { api } from './api';

type Row = Record<string, any>;
export const briefFields = [
  ['product', '产品 / 画布平台'], ['audience', '目标观众'], ['goal', '本次推广目标'],
  ['selling_points', '核心卖点'], ['must_include', '必提内容 / 必带词'],
  ['forbidden', '禁用词 / 禁止表达'], ['style', '创意与风格偏好'],
  ['deliverables', '交付规格与其他要求'],
];

export default function BriefImport({ project, brief, apply, busy }: {
  project: Row; brief: Row; apply: (brief: Row) => Promise<boolean>; busy: boolean;
}) {
  const [text, setText] = useState('');
  const [source, setSource] = useState('粘贴的Brief');
  const [warnings, setWarnings] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [uploading, setUploading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [jobId, setJobId] = useState('');
  const [job, setJob] = useState<Row | null>(null);
  const [preview, setPreview] = useState<Row | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const fileInput = useRef<HTMLInputElement>(null);
  const currentBrief = useRef(brief);
  currentBrief.current = brief;
  const expected = useRef<Row>({});
  const activeJob = useRef('');
  const textRef = useRef('');
  const storageKey = 'brief-input-' + project.id;
  const running = !!jobId && (!job || ['queued', 'running'].includes(job.status));

  function updateText(value: string, name = '粘贴的Brief') {
    setText(value); textRef.current = value; setSource(name);
    sessionStorage.setItem(storageKey, JSON.stringify({ text: value, source: name }));
    setPreview(null); setError(''); setMessage('');
  }
  function showPreview(result: Row) {
    setPreview(result);
    expected.current = { ...currentBrief.current };
    setSelected(briefFields.map(([key]) => key).filter(key => result.fields?.[key] && !currentBrief.current[key]?.trim()));
  }
  useEffect(() => {
    let live = true;
    let saved: Row = {};
    try { saved = JSON.parse(sessionStorage.getItem(storageKey) || '{}'); } catch { /* ignore old draft */ }
    textRef.current = saved.text || '';
    setText(saved.text || ''); setSource(saved.source || '粘贴的Brief');
    api('/jobs').then(async (jobs: Row[]) => {
      const latest = jobs.find(j => j.kind === 'brief_extract' && j.payload?.project_id === project.id);
      if (!live || !latest || activeJob.current || textRef.current && textRef.current.trim() !== latest.payload.text) return;
      if (!textRef.current) {
        setText(latest.payload.text); textRef.current = latest.payload.text;
        setSource(latest.payload.source_name || '粘贴的Brief');
      }
      activeJob.current = latest.id;
      setJobId(latest.id); setJob(latest);
    }).catch(() => {});
    return () => { live = false; };
  }, [project.id, storageKey]);
  useEffect(() => {
    if (!jobId) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const next = await api('/jobs/' + jobId);
        if (!live) return;
        setJob(next);
        if (next.status === 'succeeded') showPreview(next.result);
        else if (['failed', 'attention', 'cancelled'].includes(next.status)) setError(next.message || '提取未完成，原稿未改动');
        else timer = setTimeout(poll, 1500);
      } catch (e) {
        if (live) { setError((e as Error).message); timer = setTimeout(poll, 4000); }
      }
    }
    poll();
    return () => { live = false; clearTimeout(timer); };
  }, [jobId]);

  async function readFile(file?: File) {
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) { setError('文件不能超过5MB，请精简Brief后上传'); return; }
    setUploading(true); setError('');
    try {
      const result = await api('/brief/read-file', 'POST', file);
      updateText(result.text, result.source_name);
      setWarnings(result.warnings || []);
    } catch (e) { setError((e as Error).message); }
    finally { setUploading(false); if (fileInput.current) fileInput.current.value = ''; }
  }
  async function extract() {
    setSubmitting(true); setError(''); setMessage(''); setPreview(null);
    try {
      const result = await api('/jobs', 'POST', { kind: 'brief_extract', payload: {
        project_id: project.id, revision: project.revision, text, source_name: source,
      } });
      activeJob.current = result.job_id; setJob(null);
      if (jobId === result.job_id) {
        const existing = await api('/jobs/' + result.job_id);
        setJob(existing);
        if (existing.status === 'succeeded') showPreview(existing.result);
      } else setJobId(result.job_id);
    } catch (e) { setError((e as Error).message); }
    finally { setSubmitting(false); }
  }
  async function fill() {
    if (!preview) return;
    const changed = selected.filter(key => (currentBrief.current[key] || '') !== (expected.current[key] || ''));
    if (changed.length) {
      setSelected(selected.filter(key => !changed.includes(key)));
      setError('提取后你又修改了部分字段，已取消这些字段的替换。需要替换时请重新勾选。');
      return;
    }
    const next = { ...currentBrief.current };
    selected.forEach(key => { next[key] = preview.fields[key]; });
    setSubmitting(true); setError('');
    try {
      if (await apply(next)) {
        setMessage('已填入并保存' + selected.length + '项。Brief未写的要求可以留空，之后补充。');
        setPreview(null); setSelected([]);
      } else setError('暂未保存成功，提取结果仍保留，请处理编辑室中的提示。');
    } finally { setSubmitting(false); }
  }

  return <div className="brief-import">
    <div className="inline-heading"><h4>把甲方 Brief 交给 AI 整理</h4><span>粘贴 / 上传 → 核对 → 填入</span></div>
    <p>不用逐项抄写。提取原文中的产品、卖点、必提与禁用表达、交付要求；没写的留空待确认。</p>
    <label className="brief-source-label">甲方Brief原文
      <textarea aria-label="甲方Brief原文" rows={5} maxLength={30000} value={text} disabled={running || uploading}
        placeholder="把甲方发来的Brief直接粘贴在这里，也可以上传Word、PDF或文本文件。在线文档请复制正文。"
        onChange={e => { updateText(e.target.value); setWarnings([]); }} />
    </label>
    <div className="brief-import-actions">
      <input ref={fileInput} type="file" accept=".docx,.pdf,.txt,.md" aria-label="上传甲方Brief文件" disabled={running || uploading || submitting || busy}
        onChange={e => readFile(e.target.files?.[0])} />
      <button type="button" className="button" disabled={running || uploading || submitting || busy} onClick={() => fileInput.current?.click()}><FileUp size={15}/>{uploading ? '读取文件中…' : '上传Brief'}</button>
      <button type="button" className="button primary" disabled={running || uploading || submitting || busy || text.trim().length < 5} onClick={extract}><Sparkles size={15}/>{submitting ? '处理中…' : '自动整理Brief'}</button>
      <small>{text.length}/30000字 · {source}</small>
    </div>
    <small>支持 DOCX、文字型PDF、TXT、MD，最大5MB。读取文件在本机完成；点击自动整理后，正文交给已配置的DeepSeek分析，计入模型预算。</small>
    {!!warnings.length && <ul className="brief-import-notes">{warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
    {running && <div className="brief-import-status" role="status"><LoaderCircle size={16} className="spin"/><span>{job?.message || '正在排队整理Brief…'}</span><button type="button" className="button" onClick={async () => { try { await api('/jobs/' + jobId + '/cancel', 'POST', {}); } catch (e) { setError((e as Error).message); } }}>取消提取</button></div>}
    {error && <p className="brief-import-error" role="alert">{error}</p>}
    {message && <p className="brief-import-success" role="status">{message}</p>}
    {preview && <section className="brief-preview">
      <h4>核对提取结果</h4><p>默认只勾选空白字段。已有内容需要你勾选才替换；未勾选的内容全部保留。</p>
      <div className="brief-import-actions"><button type="button" className="button" disabled={submitting || busy} onClick={() => { expected.current = { ...currentBrief.current }; setSelected(briefFields.map(([key]) => key).filter(key => preview.fields[key] && !currentBrief.current[key]?.trim())); }}>只补空白</button><button type="button" className="button" disabled={submitting || busy} onClick={() => { expected.current = { ...currentBrief.current }; setSelected(briefFields.map(([key]) => key).filter(key => preview.fields[key])); }}>采用Brief中全部已提取要求</button></div>
      <div className="brief-preview-grid">{briefFields.map(([key, label]) => <div className="brief-preview-field" key={key}>
        <label><input type="checkbox" checked={selected.includes(key)} disabled={!preview.fields[key] || submitting || busy}
          onChange={e => { expected.current[key] = currentBrief.current[key] || ''; setSelected(old => e.target.checked ? [...old, key] : old.filter(k => k !== key)); }}/><strong>{label}</strong>{brief[key]?.trim() && <small>已有内容，默认保留</small>}</label>
        <p>{preview.fields[key] || 'Brief未写，待确认'}</p>
        {!!preview.evidence[key]?.length && <details><summary>查看原文依据</summary>{preview.evidence[key].map((q: string, i: number) => <blockquote key={i}>{q}</blockquote>)}</details>}
      </div>)}</div>
      {!!preview.warnings?.length && <ul className="brief-import-notes">{preview.warnings.map((w: string, i: number) => <li key={i}>{w}</li>)}</ul>}
      {!!preview.questions?.length && <div><strong>需要你确认</strong><ul>{preview.questions.map((q: string, i: number) => <li key={i}>{q}</li>)}</ul></div>}
      <div className="brief-import-actions"><button type="button" className="button primary" disabled={!selected.length || submitting || busy} onClick={fill}>填入并保存{selected.length}项</button><button type="button" className="button" onClick={() => { setPreview(null); setMessage('已收起提取结果，当前表单未改动。'); }}>暂不填入</button></div>
    </section>}
  </div>;
}
