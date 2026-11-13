import { useState } from "react";
import { Copy } from "lucide-react";

type Row = Record<string, any>;
type Section = { title: string; lines: string[] };

function materialResult(video: Row): Section[] {
  const a = video.analysis?.payload;
  const visual = video.visual_analysis;
  const sections: Section[] = [
    { title: "内容摘要", lines: [a?.summary || visual?.summary] },
    { title: "受众与切入角度", lines: [a?.audience, a?.angle, ] },
    { title: "开头参考", lines: [a?.hook] },
    { title: "原文结构与依据", lines: (a?.structure || []).map((s: Row) => [s.role, s.quote && `「${s.quote}」`, s.reason].filter(Boolean).join(" · ")) },
    { title: "操作步骤", lines: a?.steps?.length ? a.steps : [] },
    { title: "可借鉴的做法", lines: [...(a?.opportunities || []), ...(visual?.reuse || [])] },
    { title: "画面证据（抽帧）", lines: visual ? [visual.coverage, ...(visual.opening || []).map((s: Row) => `${s.second} 秒 · ${s.visual}${s.role ? `（作用推断：${s.role}）` : ""}`), ...(visual.shot_structure || []).map((s: Row) => [s.range, s.visual, s.purpose].filter(Boolean).join(" · "))] : [] },
    { title: "拍前准备", lines: a?.requirements || [] },
    { title: "待核实", lines: [...(a?.verification || []), ...(visual?.verification || [])] },
  ];
  return sections.map(({title, lines}) => ({ title, lines: [...new Set(lines.filter(x => typeof x === "string" && x.trim()))] })).filter(s => s.lines.length);
}

export function MaterialBrief({ video, notify }: { video: Row; notify: (message: string, error?: boolean) => void }) {
  const [showCopy, setShowCopy] = useState(false);
  const result = materialResult(video);
  const transcript = video.transcript?.text?.trim();
  const packet = [
    "# 写稿参考素材", `作品：${video.title}`, `作者：${video.author || "未知"}`, `原链接：${video.url}`,
    `素材方向：${video.content_tracks?.join(" / ") || "待分类"}`,
    "请借鉴题材、结构或演示方式，并用自己的案例表达。分析是参考；把原作者说法和未核实结论分开。",
    ...result.map(({title, lines}) => `\n## ${title}\n${lines.map(line => `- ${line}`).join("\n")}`),
    transcript ? `\n## 已保存原文（${video.transcript.source || "来源见工作台"}）\n${transcript}` : "\n## 原文\n尚无已保存原文，请回原片核对。",
    ...(video.visual_analysis ? ["\n采样画面可在工作台展开查看；以上为按时间点记录的画面描述。"] : []),
  ].join("\n");

  async function copy() {
    try {
      await navigator.clipboard.writeText(packet);
      notify("已复制写稿材料，可以粘贴给你的写稿 Skill");
    } catch {
      setShowCopy(true);
    }
  }

  return <div className="material-brief">
    {(result.length > 0 || transcript) && <button type="button" className="button" onClick={copy}><Copy size={15}/>复制给写稿 Skill</button>}
    {showCopy && <label className="field"><span>可手动复制以下写稿材料</span><textarea aria-label="可复制的写稿材料" readOnly rows={10} value={packet} onFocus={event => event.target.select()}/></label>}
    {result.length > 0 && <div className="analysis-content">{result.map(({title, lines}) => {
      const body = lines.length === 1 ? <p>{lines[0]}</p> : <ul>{lines.map(line => <li key={line}>{line}</li>)}</ul>;
      return ["原文结构与依据", "画面证据（抽帧）", "可借鉴的做法"].includes(title)
        ? <details className="reading-evidence" key={title}><summary>{title}</summary>{body}</details>
        : <section key={title}><h4>{title}</h4>{body}</section>;
    })}</div>}
  </div>;
}
