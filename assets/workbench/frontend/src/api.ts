let csrf = "";
export function setCsrf(value: string) {
  csrf = value || "";
}
export class ApiError extends Error {
  code: string;
  status: number;
  constructor(message: string, code = "", status = 0) {
    super(message);
    this.code = code;
    this.status = status;
  }
}
export async function api<T = any>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  let response: Response;
  const isFile = body instanceof Blob;
  try {
    response = await fetch("/api" + path, {
      method,
      credentials: "same-origin",
      headers: {
        "Content-Type": isFile ? "application/octet-stream" : "application/json",
        ...(isFile ? { "X-File-Name": encodeURIComponent((body as File).name || 'brief.txt') } : {}),
        ...(csrf ? { "X-CSRF-Token": csrf } : {}),
      },
      ...(body === undefined ? {} : { body: isFile ? body as Blob : JSON.stringify(body) }),
    });
  } catch {
    throw new ApiError(
      "暂时连接不到电脑，请检查工作台和 Tailscale 是否在线。",
      "offline",
    );
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401)
      window.dispatchEvent(new Event("workbench-logout"));
    throw new ApiError(
      data.message ||
        (typeof data.detail === "string"
          ? data.detail
          : "输入格式有误，请检查后重试"),
      data.code,
      response.status,
    );
  }
  return data;
}
export const fmtNumber = (v: number | null | undefined) =>
  v == null
    ? "—"
    : v >= 10000
      ? (v / 10000).toFixed(1) + "万"
      : v.toLocaleString("zh-CN");
export const fmtDate = (v: string | undefined | null) =>
  v
    ? new Date(v).toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
        timeZone: "Asia/Shanghai",
      })
    : "时间未知";
export const fmtFullDate = (v: string | undefined | null) =>
  v ? new Date(v).toLocaleString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Shanghai" }) : "时间未知";
export const sources: Record<string, string> = {
  hotspot: "热点推荐",
  favorites: "我的收藏",
  creator: "对标监控",
  search: "热点发现",
  manual: "手动添加",
};
export const jobNames: Record<string, string> = {
  favorites_login: "连接抖音",
  favorites_status: "检查抖音会话",
  favorites_probe: "验证Edge收藏读取",
  favorites_sync: "同步收藏",
  favorites_backfill: "补齐历史收藏",
  creators_sync: "更新对标",
  author_profiles_refresh: "补全推荐作者粉丝",
  video_refresh: "更新素材数据",
  search: "更新低粉爆款",
  hotspot_sync: "更新热点推荐",
  classify: "AI分类",
  transcribe: "本机转写",
  analyze: "研读拆解",
  hooks: "生成前5秒",
  script: "生成正文",
  backup: "本机备份",
  knowledge_sync: "整理到Obsidian",
  knowledge_deep: "重点素材深读",
  commercial_analyze: "商单参考拆解",
  brief_extract: "自动整理甲方Brief",
  visual_analyze: "关键帧与前5秒拆解",
};
export const jobStates: Record<string, string> = {
  queued: "排队中",
  running: "进行中",
  succeeded: "已完成",
  failed: "失败",
  attention: "需要处理",
  cancelled: "已取消",
};
