import { useEffect, useState } from 'react';
import { api, fmtFullDate } from './api';
type Row = Record<string, any>;

export function KnowledgePanel({ refresh, queue, notify }: { refresh: number; queue: (kind:string,payload?:Row)=>void; notify:(text:string,error?:boolean)=>void }) {
  const [data,setData]=useState<Row|null>(null);
  const [reading,setReading]=useState<Row|null>(null);
  const [path,setPath]=useState('');
  const load=()=>api('/knowledge').then(r=>{setData(r);setPath(r.config.vault_path||'')}).catch(e=>notify(e.message,true));
  useEffect(()=>{load()},[refresh]);
  async function configure(enabled:boolean) {
    try {await api('/knowledge/config','PATCH',{enabled,vault_path:path});await load();notify(enabled?'已启用每周日21:00整理':'已暂停每周整理，已有笔记保留')} catch(e){notify((e as Error).message,true)}
  }
  if(!data)return <p>正在读取知识库状态…</p>;
  const stateName:Row={indexed:'已索引',pending:'待深读',ready:'已深读',attention:'需处理'};
  return <div className="knowledge-panel">
    <h3>个人收藏知识库</h3>
    <p>全部建立索引，人工标重点或项目引用时深读。整理只读取本机资料，不额外同步抖音。</p>
    <details className="knowledge-config"><summary>整理设置 · {data.config.enabled ? '每周日21:00自动整理' : '自动整理已暂停'}</summary><label>现有Obsidian库<input aria-label="Obsidian库目录" value={path} onChange={e=>setPath(e.target.value)} /></label>
      <p>知识区：08-自媒体知识库 · 每周日21:00（北京时间） · 电脑下次上线补跑</p>
      <div className="card-buttons"><button className="button" onClick={()=>configure(!data.config.enabled)}>{data.config.enabled?'暂停每周整理':'启用每周整理'}</button><button className="button primary" disabled={!data.config.enabled} onClick={()=>queue('knowledge_sync')}>立即整理到Obsidian</button></div>
    </details>
    <p>最近整理：{fmtFullDate(data.last_sync?.at)} · 已索引 {data.last_sync?.indexed||0} 条 · 重点深读完成 {data.counts.ready||0} 条</p>
    <p className="hint">请在笔记的“我的笔记”区写内容。自动生成区被手改时会跳过覆盖，并在周报中列出。手机在此查看周报；当前未配置iPhone的Obsidian文件同步。</p>
    <h4>每周整理</h4>
    {!data.reports.length&&<p>首次整理后会显示结果。</p>}
    {data.reports.map((report:Row)=><details className="knowledge-report" key={report.id}><summary>{report.period} · 索引{report.indexed}条 · 深读{report.ready}条</summary><p>{report.time_basis}</p><p>本轮写入{report.written}个文件，未变化{report.unchanged}个，保留人工改动{report.conflicts.length}个。</p>
      {!!report.conflicts.length&&<p>需处理：{report.conflicts.join('、')}</p>}
      {!!report.highlights?.length&&<details><summary>重点知识与可用方向</summary>{report.highlights.map((h:Row)=><section key={h.id}><h4>{h.title}</h4><p>{h.summary}</p><ul>{h.applications.map((s:string,i:number)=><li key={i}>{s}</li>)}</ul></section>)}</details>}
      <details><summary>本周首次入库 {report.new_items.length} 条</summary><ul>{report.new_items.map((v:Row)=><li key={v.id}><a href={'https://www.douyin.com/video/'+v.id} target="_blank" rel="noreferrer">{v.title}</a><small> · {v.tracks.join(' / ')}</small></li>)}</ul></details></details>)}
    <h4>重点素材</h4>
    <div className="knowledge-items">{data.items.map((item:Row)=><section key={item.video_id}><strong>{item.title.slice(0,110)}</strong><p>{item.content_tracks.join(' / ')} · {stateName[item.state]||item.state}</p>{item.error&&<p className="hint">{item.error}</p>}<div className="card-buttons">
      {item.state==='ready'&&<button className="button" onClick={()=>api('/knowledge/'+item.video_id+'/read').then(setReading).catch(e=>notify(e.message,true))}>查看知识卡</button>}
      <button className="button" onClick={()=>queue('knowledge_deep',{video_id:item.video_id,force:item.state==='ready'})}>{item.state==='ready'?'重新深读':'继续深读'}</button>
    </div></section>)}</div>
    {reading&&<section className="knowledge-reading"><div className="inline-heading"><h4>提炼知识卡</h4><button className="button" onClick={()=>setReading(null)}>收起</button></div><p>{reading.payload.summary}</p>{[['takeaways','可复用方法'],['steps','操作/制作步骤'],['applications','适用场景'],['verification','待验证']].map(([key,label])=><div key={key}><h4>{label}</h4><ul>{(reading.payload[key]||[]).map((s:string,i:number)=><li key={i}>{s}</li>)}</ul></div>)}<p>开头参考：{reading.payload.opening_reference}</p></section>}
  </div>
}

export function VisualEvidence({identity,data}:{identity:string;data:Row|null}) {
  if(!data)return <p className="hint">拆解后显示真实采样画面与分析。抽帧不能替代对连续运动、精确剪切和原声音轨的检查。</p>;
  return <section className="visual-evidence"><p>{data.summary}</p><p className="hint">{data.coverage}</p><div className="frame-grid">{(data.sample_seconds||[]).map((second:number,i:number)=><figure key={i}><img loading="lazy" src={`/api/videos/${identity}/frames/${i}`} alt={`${second}秒采样画面`}/><figcaption>{second}秒</figcaption></figure>)}</div>
    <h4>前5秒</h4>{(data.opening||[]).map((v:Row,i:number)=><p key={i}><strong>{v.second}秒：</strong>{v.visual} · {v.role}</p>)}
    <h4>画面结构</h4>{(data.shot_structure||[]).map((v:Row,i:number)=><p key={i}><strong>{v.range}：</strong>{v.visual} · {v.purpose}</p>)}
    <ul>{(data.verification||[]).map((s:string,i:number)=><li key={i}>{s}</li>)}</ul>
  </section>
}
