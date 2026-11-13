type Candidate = {
  id: string;
  title: string;
  state?: string;
  topic_key?: string | null;
  content_tracks?: string[];
};

const tutorial = /教程|实操|实战|上手|手把手|教你|怎么用|如何|工作流|安装|配置|搭建|操作步骤|使用方法/;
const comparison = /实测|实拍|对比|评测|测试结果/;
const visual = /分镜|运镜|首尾帧|图生视频|文生视频|短片制作|画面复刻|镜头设计/;
const genericTopics = new Set(["其他", "AI教程", "AIGC", "资讯", "工具与新功能", "Agent工作流", "AI视频与剪辑"]);

export function pickTopics<T extends Candidate>(rows: T[]) {
  const seen = new Set<string>();
  const picks: { video: T; reason: string; demo: string; verify: string }[] = [];
  for (const video of rows) {
    if (video.state && !["待看", "备选"].includes(video.state)) continue;
    const headline = video.title.split(/[#＃]/, 1)[0].trim();
    const isComparison = comparison.test(headline);
    const isTutorial = tutorial.test(headline);
    const isVisual = video.content_tracks?.includes("AIGC") && visual.test(headline);
    if (!isComparison && !isTutorial && !isVisual) continue;
    const topic = video.topic_key?.trim();
    const key = (topic && !genericTopics.has(topic) ? topic : headline).replace(/[^\p{L}\p{N}]/gu, "").toLowerCase();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    picks.push({
      video,
      reason: isComparison ? "标题给出了实测或比较方向，适合用实录结果回答观众的疑问。" : isVisual ? "标题有具体镜头或分镜方法，适合用画面对照讲清楚。" : "标题给出了教程或操作方向，适合把一个任务讲给新手听。",
      demo: isComparison ? "同一输入、同样设置下，录两次结果做对照。" : isVisual ? "录一个关键操作，接上该操作生成的实际画面。" : "先录操作前后的结果，再录最关键的一步。",
      verify: isComparison ? "核对比较条件、版本和实际差异。" : "先复现一遍，核对工具版本和可展示的结果。",
    });
    if (picks.length === 5) break;
  }
  return picks;
}
