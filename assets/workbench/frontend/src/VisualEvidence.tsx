import { useState } from "react";
import { api, fmtDate } from "./api";
type Row = Record<string, any>;
export function VisualEvidence({identity,data}:{identity:string;data:Row|null}) {
  if(!data)return <p className="hint">拆解后显示真实采样画面与分析。抽帧不能替代对连续运动、精确剪切和原声音轨的检查。</p>;
  return <section className="visual-evidence"><p>{data.summary}</p><p className="hint">{data.coverage}</p><div className="frame-grid">{(data.sample_seconds||[]).map((second:number,i:number)=><figure key={i}><img loading="lazy" src={`/api/videos/${identity}/frames/${i}`} alt={`${second}秒采样画面`}/><figcaption>{second}秒</figcaption></figure>)}</div>
    <h4>前5秒</h4>{(data.opening||[]).map((v:Row,i:number)=><p key={i}><strong>{v.second}秒：</strong>{v.visual} · {v.role}</p>)}
    <h4>画面结构</h4>{(data.shot_structure||[]).map((v:Row,i:number)=><p key={i}><strong>{v.range}：</strong>{v.visual} · {v.purpose}</p>)}
    <ul>{(data.verification||[]).map((s:string,i:number)=><li key={i}>{s}</li>)}</ul>
  </section>
}
