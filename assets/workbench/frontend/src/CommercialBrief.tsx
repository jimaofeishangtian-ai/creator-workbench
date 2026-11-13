import { useEffect, useState } from 'react';
import { api } from './api';
import BriefImport, { briefFields } from './BriefImport';
type Row=Record<string,any>;
export default function CommercialBrief({draft,project,change,generate,applyBrief,busy}:{draft:Row;project:Row;change:(c:Row)=>void;generate:(kind:string)=>void;applyBrief:(brief:Row)=>Promise<boolean>;busy:boolean}) {
  const [query,setQuery]=useState('');
  const [options,setOptions]=useState<Row[]>([]);
  useEffect(()=>{
    if(draft.creation_mode!=='commercial')return;
    let active=true;
    const timer=setTimeout(()=>{api('/videos?scope=all&mode=recent&limit=30&q='+encodeURIComponent(query)).then(r=>{if(active)setOptions(r.items)}).catch(()=>{})},250);
    return ()=>{active=false;clearTimeout(timer)};
  },[query,draft.creation_mode]);
  const refs:string[]=draft.reference_ids||[];
  const brief=draft.brief||{};
  const filled = briefFields.filter(([key])=>brief[key]?.trim()).length;
  return <section className="commercial-brief">
    <label>创作场景<select aria-label="创作场景" value={draft.creation_mode||'tutorial'} onChange={e=>change({creation_mode:e.target.value,reference_ids:e.target.value==='commercial'&&!refs.length&&project.video_id?[project.video_id]:refs})}><option value="tutorial">AI教程 · 涨粉</option><option value="commercial">AIGC商单 · 创意与推广</option></select></label>
    {draft.creation_mode==='commercial'&&<>
      <h3>甲方要求</h3>
      <details className="brief-update" open={!filled}><summary>{filled ? '更新甲方Brief（粘贴 / 上传）' : '粘贴或上传Brief，自动整理要求'}</summary><BriefImport key={project.id} project={project} brief={brief} apply={applyBrief} busy={busy}/></details>
      <details className="brief-manual"><summary>查看或手动调整商单要求（已填{briefFields.filter(([key])=>brief[key]?.trim()).length}/8项）</summary>
      <div className="brief-grid">{briefFields.map(([key,label])=><label key={key}>{label}<textarea aria-label={label} rows={key==='selling_points'?3:2} maxLength={2000} value={brief[key]||''} onChange={e=>change({brief:{...brief,[key]:e.target.value}})}/></label>)}</div></details>
      <h4>参考作品（最多4条）</h4>
      <div className="selected-references">{refs.map(id=><div key={id}><span>{[...(project.references||[]),...options].find(v=>v.id===id)?.title?.slice(0,100)||id}</span><button className="button" onClick={()=>change({reference_ids:refs.filter(v=>v!==id)})}>移除引用</button></div>)}</div>
      <details open={!refs.length}><summary>{refs.length ? '添加其他参考作品' : '选择参考作品（可选，最多4条）'}</summary><input aria-label="搜索商单参考素材" placeholder="搜索已收录作品的标题或作者…" value={query} onChange={e=>setQuery(e.target.value)}/><div className="reference-picker">{options.filter(v=>!refs.includes(v.id)).map(v=><button type="button" key={v.id} disabled={refs.length>=4} onClick={()=>change({reference_ids:[...refs,v.id]})}>{v.author} · {v.title.slice(0,100)}</button>)}</div></details>
      <button className="button primary" disabled={busy||!refs.length} onClick={()=>generate('commercial_analyze')}>拆解参考画面与前5秒</button>
      {project.reference_analysis?.summary&&<details className="commercial-analysis"><summary>参考拆解已完成 · 展开查看</summary><p>{project.reference_analysis.summary}</p>{[['opening_reference','开头结构'],['structure','镜头结构'],['angles','本次创意方向'],['verification','待核实']].map(([key,label])=><div key={key}><h4>{label}</h4><ul>{(project.reference_analysis[key]||[]).map((s:string,i:number)=><li key={i}>{s}</li>)}</ul></div>)}<p className="hint">已按每条参考采样画面分析，未提供的声音和连续运动仍需回原视频核对。修改brief或参考后请重新拆解。</p></details>}
    </>}
  </section>
}
