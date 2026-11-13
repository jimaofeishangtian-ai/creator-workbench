import {
  useState,
  useEffect,
  useCallback,
  useRef,
  type ReactNode,
} from "react";
import {
  Sparkles,
  Flame,
  Bookmark,
  PenLine,
  Settings2,
  Search,
  Plus,
  ArrowUpRight,
  ArrowRight,
  X,
  RefreshCw,
  ChevronDown,
  ChevronRight,
  Check,
  Circle,
  CheckCircle2,
  AlertCircle,
  Clock3,
  Heart,
  MessageCircle,
  Users,
  MoreHorizontal,
  ExternalLink,
  FileText,
  Download,
  Laptop,
  Smartphone,
  Link2,
  FolderOpen,
  Layers3,
  Zap,
  ShieldCheck,
  LogOut,
  LoaderCircle,
  SlidersHorizontal,
  Copy,
  CheckCheck,
  Activity,
  Wifi,
  LockKeyhole,
  Play,
  Trash2,
  ListChecks,
  History,
  CloudOff,
  TrendingUp,
  Star,
} from "lucide-react";
import { VisualEvidence } from './VisualEvidence';
import Discovery from './Discovery';
import BrowserConnection from './BrowserConnection';
import { CoverImage, CoverPlayHint, CoverDuration } from './CoverImage';
import PhoneAccess from './PhoneAccess';
import CollectionClassifier, { collectionGroups, collectionLabel } from './CollectionClassifier';
import { pickTopics } from './topicPicks';
import { MaterialBrief } from './MaterialBrief';
import {
  api,
  ApiError,
  setCsrf,
  fmtNumber,
  fmtDate,
  fmtFullDate,
  sources,
  jobNames,
  jobStates,
} from "./api";

type Row = Record<string, any>;
type Notice = (message: string, error?: boolean, action?: "jobs") => void;

function jobPayload(job: Row): Row {
  try { return typeof job.payload === "string" ? JSON.parse(job.payload) : job.payload || {}; } catch { return {}; }
}
const tabs = [
  { id: "topics", label: "今日推荐", short: "推荐", icon: Flame },
  { id: "saved", label: "备选选题", short: "备选", icon: Star },
  { id: "favorites", label: "我的收藏", short: "收藏", icon: Bookmark },
  { id: "creators", label: "对标账号", short: "对标", icon: Users },
  { id: "settings", label: "我的工作台", short: "我的", icon: Settings2 },
];

const phoneTabs = [
  { id:'hot',page:'topics',view:'hot',label:'热点推荐',icon:Flame },
  { id:'focus',page:'topics',view:'focus',label:'低粉爆款',icon:TrendingUp },
  { id:'new-works',page:'topics',view:'creators',label:'对标新作',icon:Users },
  { id:'saved',page:'saved',view:'',label:'备选',icon:Star },
  { id:'favorites',page:'favorites',view:'',label:'收藏',icon:Bookmark },
];

function Button({
  children,
  onClick,
  kind = "",
  disabled = false,
  className = "",
  type = "button",
  title,
  label,
  pressed,
}: {
  children: ReactNode;
  onClick?: () => void;
  kind?: string;
  disabled?: boolean;
  className?: string;
  type?: "button" | "submit";
  title?: string;
  label?: string;
  pressed?: boolean;
}) {
  return (
    <button
      type={type}
      className={`button ${kind} ${className}`}
      onClick={onClick}
      disabled={disabled}
      title={title}
      aria-label={label}
      aria-pressed={pressed}
    >
      {children}
    </button>
  );
}
function Modal({
  title,
  subtitle,
  close,
  children,
  wide = false,
}: {
  title: string;
  subtitle?: string;
  close: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const closeRef = useRef(close);
  closeRef.current = close;
  useEffect(() => {
    const before = document.activeElement as HTMLElement;
    const old = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    panel.current?.focus();
    const escape = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeRef.current();
      if (e.key === "Tab") {
        const all = panel.current?.querySelectorAll<HTMLElement>(
          "button:not(:disabled), input, select, textarea, a[href]",
        );
        if (!all?.length) return;
        const first = all[0],
          last = all[all.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener("keydown", escape);
    return () => {
      document.body.style.overflow = old;
      window.removeEventListener("keydown", escape);
      before?.focus();
    };
  }, []);
  return (
    <div
      className="modal-shade"
      onClick={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <div
        ref={panel}
        tabIndex={-1}
        className={`modal ${wide ? "wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <header className="modal-header">
          <div>
            <h2>{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
          <button className="icon-button" aria-label="关闭" onClick={close}>
            <X size={20} />
          </button>
        </header>
        {children}
      </div>
    </div>
  );
}
function Field({
  label,
  help,
  children,
}: {
  label: string;
  help?: string;
  children: ReactNode;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {help && <small>{help}</small>}
    </label>
  );
}
function Empty({
  icon,
  title,
  text,
  children,
}: {
  icon: ReactNode;
  title: string;
  text: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">{icon}</div>
      <h3>{title}</h3>
      <p>{text}</p>
      {children}
    </div>
  );
}
function Tag({
  children,
  green = false,
}: {
  children: ReactNode;
  green?: boolean;
}) {
  return <span className={`tag ${green ? "green" : ""}`}>{children}</span>;
}

function Auth({
  status,
  done,
  notify,
}: {
  status: Row;
  done: () => void;
  notify: Notice;
}) {
  const [password, setPassword] = useState("");
  const [code, setCode] = useState(
    new URLSearchParams(location.hash.slice(1)).get("setup") || "",
  );
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (location.hash) history.replaceState(null, "", location.pathname);
  }, []);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const r = await api(
        status.configured ? "/auth/login" : "/auth/setup",
        "POST",
        { password, setup_token: code },
      );
      setCsrf(r.csrf);
      done();
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="auth-screen">
      <div className="auth-brand">
        <span className="brand-mark">
          <Sparkles size={24} />
        </span>
        <span>AI 创作台</span>
        <span className="mini-label">PRIVATE WORKSPACE</span>
      </div>
      <div className="auth-layout">
        <section className="auth-intro">
          <div className="eyebrow">
            <span className="live-dot" /> 你的个人 AI 内容工作台
          </div>
          <h1>
            让每一次收藏，
            <br />
            成为下一条<span>好作品。</span>
          </h1>
          <p>
            发现值得做的选题，拆出有用的方法。
            <br />
            少刷无关内容，快速找到值得借鉴的账号和视频。
          </p>
          <div className="auth-steps">
            <span>
              <Flame size={17} />
              发现选题
            </span>
            <ArrowRight size={14} />
            <span>
              <FileText size={17} />
              研读拆解
            </span>
            <ArrowRight size={14} />
            <span>
              <PenLine size={17} />
              开始创作
            </span>
          </div>
          <div className="privacy-note">
            <ShieldCheck size={16} /> 数据保存在你的电脑 · 手机与电脑共享
          </div>
        </section>
        <section className="auth-card">
          <span className="auth-lock">
            <LockKeyhole size={25} />
          </span>
          <h2>{status.configured ? "欢迎回来" : "开启你的创作工作台"}</h2>
          <p>
            {status.configured
              ? "登录后，继续你的下一条作品。"
              : "先为只属于你的工作台设置登录密码。"}
          </p>
          <form onSubmit={submit}>
            {!status.configured && (
              <Field
                label="初始设置码"
                help="通过「启动工作台」打开会自动填入；也可查看电脑数据目录中的 initial-setup-code.txt。"
              >
                <input
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  required
                  autoComplete="off"
                />
              </Field>
            )}
            <Field
              label={status.configured ? "工作台密码" : "设置密码（至少8位）"}
            >
              <input
                autoFocus
                type="password"
                minLength={8}
                autoComplete={
                  status.configured ? "current-password" : "new-password"
                }
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </Field>
            <Button type="submit" kind="primary full" disabled={busy}>
              {busy ? <LoaderCircle className="spin" size={17} /> : null}
              {status.configured ? "进入工作台" : "创建我的工作台"}
              <ArrowRight size={17} />
            </Button>
          </form>
          <small className="auth-foot">
            外出访问需要电脑开机，并连接已获准的私有网络。
          </small>
        </section>
      </div>
      <footer>
        AI CREATOR WORKBENCH <span>发现 · 理解 · 创作</span>
      </footer>
    </main>
  );
}

export default function App() {
  const [auth, setAuth] = useState<Row | null>(null);
  const [offline, setOffline] = useState(false);
  const [page, setPage] = useState(["topics","saved","favorites","creators","settings"].includes(localStorage.getItem("aw-page") || "") ? localStorage.getItem("aw-page")! : "topics");
  const [topicView,setTopicView]=useState(()=>{const saved=localStorage.getItem('aw-topic-view');return saved&&['hot','focus','creators','dismissed'].includes(saved)?saved:'hot';});
  const [settingsSection, setSettingsSection] = useState("overview");
  const [overview, setOverview] = useState<Row>({
    counts: {},
    configured: {},
    jobs: [],
    expenses: [],
    budget: { total: 300 },
  });
  const [settings, setSettings] = useState<Row | null>(null);
  const [toast, setToast] = useState<{ text: string; error: boolean; action?: "jobs" } | null>(
    null,
  );
  const [refresh, setRefresh] = useState(0);
  const [adding, setAdding] = useState(false);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [detailInitialTab,setDetailInitialTab]=useState("result");
  function openDetail(id:string,tab="result"){setDetailInitialTab(tab);setDetailId(id);}
  const [showJobs, setShowJobs] = useState(false);
  const toastTimer=useRef<number|null>(null);
  const dismissToast=useCallback(()=>{if(toastTimer.current!==null)window.clearTimeout(toastTimer.current);toastTimer.current=null;setToast(null);},[]);
  const openJobs=useCallback(()=>{dismissToast();setShowJobs(true);},[dismissToast]);
  const notify = useCallback<Notice>((text, error = false, action) => {
    if(toastTimer.current!==null)window.clearTimeout(toastTimer.current);
    setToast({ text, error, action });
    toastTimer.current=window.setTimeout(()=>{toastTimer.current=null;setToast(null);},error?10000:action?6000:4500);
  }, []);
  useEffect(()=>()=>{if(toastTimer.current!==null)window.clearTimeout(toastTimer.current);},[]);
  const loadAuth = useCallback(async () => {
    try {
      const s = await api("/auth/status");
      setCsrf(s.csrf);
      setAuth(s);
      setOffline(false);
    } catch {
      setOffline(true);
    }
  }, []);
  useEffect(() => {
    loadAuth();
    const fn = () => setAuth((s) => ({ ...s, authenticated: false }));
    window.addEventListener("workbench-logout", fn);
    return () => window.removeEventListener("workbench-logout", fn);
  }, [loadAuth]);
  const reload = useCallback(() => setRefresh((n) => n + 1), []);
  useEffect(() => {
    if (auth?.public_access && ["settings", "creators"].includes(page)) setPage("topics");
  }, [auth?.public_access, page]);
  useEffect(() => {
    if (!auth?.authenticated) return;
    const resume = () => {
      if (document.visibilityState === "visible") reload();
    };
    window.addEventListener("focus", resume);
    window.addEventListener("pageshow", resume);
    document.addEventListener("visibilitychange", resume);
    return () => {
      window.removeEventListener("focus", resume);
      window.removeEventListener("pageshow", resume);
      document.removeEventListener("visibilitychange", resume);
    };
  }, [auth?.authenticated, reload]);
  const loadOverview = useCallback(async () => {
    try {
      const [o, s] = await Promise.all([api("/overview"), api("/settings")]);
      setOverview(o);
      setSettings(s);
      setOffline(false);
    } catch (e) {
      if ((e as ApiError).code === "offline") setOffline(true);
    }
  }, []);
  useEffect(() => {
    if (auth?.authenticated) loadOverview();
  }, [auth?.authenticated, refresh, loadOverview]);
  useEffect(() => {
    if (!auth?.authenticated) return;
    let last = "";
    const events = new EventSource("/api/events");
    events.onmessage = (e) => {
      const jobs = JSON.parse(e.data);
      const signature = jobs.map((j: Row) => j.id + j.status).join("|");
      if (last && last !== signature) {
        reload();
      }
      last = signature;
      setOverview((o) => ({
        ...o,
        jobs: [
          ...jobs.map((j: Row) => ({
            ...o.jobs.find((x: Row) => x.id === j.id),
            ...j,
          })),
        ],
      }));
      setOffline(false);
    };
    events.onerror = () => setOffline(true);
    const t = setInterval(loadOverview, 15000);
    return () => {
      events.close();
      clearInterval(t);
    };
  }, [auth?.authenticated, loadOverview, reload]);
  useEffect(() => localStorage.setItem("aw-page", page), [page]);
  useEffect(()=>localStorage.setItem("aw-topic-view",topicView),[topicView]);
  async function queue(kind: string, payload: Row = {}) {
    notify("正在提交任务…",false,"jobs");
    try {
      const result = await api("/jobs", "POST", { kind, payload });
      if (result.job_id && !result.cached) setOverview(o => ({ ...o, jobs: o.jobs.some((j: Row) => j.id === result.job_id) ? o.jobs.map((j: Row) => j.id === result.job_id ? { kind, payload, ...j } : j) : [{ id: result.job_id, kind, payload, status: "queued", progress: 0, message: "已提交，等待处理" }, ...o.jobs] }));
      notify(result.cached ? "已复用最近一次结果，没有新增API调用" : "任务已加入队列，可在任务中心查看进度",false,"jobs");
      reload();
      return result as Row;
    } catch (e) {
      notify((e as Error).message, true,"jobs");
      return null;
    }
  }
  const used = overview.expenses.reduce((n: number, e: Row) => n + e.used, 0);
  const running = overview.jobs.filter((j: Row) =>
    ["queued", "running"].includes(j.status),
  );
  const activeJob = running.find((j: Row) => j.status === "running") || running[0];
  const mobileTasks=<button type="button" className="button mobile-task-button" aria-label="任务中心" aria-expanded={showJobs} onClick={openJobs}><ListChecks size={16}/><span>任务</span>{running.length>0&&<b className="mobile-header-task-count">{running.length}</b>}</button>;
  const detailTask = running.find((j: Row) => ["analyze", "transcribe", "visual_analyze"].includes(j.kind) && jobPayload(j).video_id === detailId);
  const latestDetailTask = overview.jobs.find((j: Row) => ["analyze", "transcribe", "visual_analyze"].includes(j.kind) && jobPayload(j).video_id === detailId);
  async function cancelTask(identity: string) {
    try { const response = await api(`/jobs/${identity}/cancel`, "POST", {}); notify(response.message,false,"jobs"); reload(); }
    catch (error) { notify((error as Error).message, true); }
  }
  if (!auth)
    return (
      <div className="loading-screen">
        <Sparkles size={32} />
        <h2>AI 创作台</h2>
        {offline ? (
          <>
            <p>连接不到本机服务，请先启动工作台。</p>
            <Button onClick={loadAuth}>重新连接</Button>
          </>
        ) : (
          <LoaderCircle className="spin" />
        )}
      </div>
    );
  if (!auth.authenticated)
    return (
      <>
        <Auth status={auth} done={loadAuth} notify={notify} />
        {toast && (
          <div className={`toast ${toast.error ? "error" : ""}`} role="status">
            {toast.error ? (
              <AlertCircle size={18} />
            ) : (
              <CheckCircle2 size={18} />
            )}
            <span>{toast.text}</span>
          </div>
        )}
      </>
    );
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          aria-label="自媒体选题工作台"
          onClick={(e) => {
            e.preventDefault();
            setPage("topics");
          }}
        >
          <span className="brand-mark">
            <Sparkles size={22} />
          </span>
          <div className="brand-copy">
            <span className="brand-name">自媒体<br/>选题工作台</span><small>CREATOR WORKBENCH</small>
          </div>
        </a>
        <div className="workspace-label">
          个人工作空间 <span>V1.0</span>
        </div>
        <nav>
          {tabs.filter(t => !auth.public_access || !["settings", "creators"].includes(t.id)).map((t) => (
            <button
              key={t.id}
              className={`nav-item ${page === t.id ? "active" : ""}`}
              onClick={() => setPage(t.id)}
            >
              <t.icon size={20} />
              <span>{t.label}</span>
              {t.id === "favorites" && overview.counts.personal_materials > 0 ? (
                <b>{overview.counts.personal_materials}</b>
              ) : null}
              {t.id === "projects" && overview.counts.projects > 0 ? (
                <b>{overview.counts.projects}</b>
              ) : null}
            </button>
          ))}
        </nav>
        <div className="sidebar-project">
          <span className="mini-label">少刷一点，找到重点</span>
          <div>
            <span className="tiny-orbit" />
            <p>
              留住灵感
              <br />
              <strong>更快找到参考</strong>
            </p>
          </div>
          <Button onClick={() => setAdding(true)} kind="outline">
            <Plus size={15} />
            添加一条灵感
          </Button>
        </div>
        <div className="sidebar-bottom">
          <button className="nav-item" onClick={openJobs}>
            <Activity size={18} />
            <span>任务中心</span>
            {running.length > 0 && (
              <span className="count-bubble">{running.length}</span>
            )}
          </button>
          <div className="local-state">
            <span className={`live-dot ${offline ? "off" : ""}`} />
            <div>
              {offline ? "正在重新连接" : "本机工作台在线"}
              <small>数据仅保存在这台电脑</small>
            </div>
            <Laptop size={17} />
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumbs">
            个人工作空间 <ChevronRight size={13} />
            <strong>{tabs.find((t) => t.id === page)?.label}</strong>
          </div>
          <div className="top-actions">
            <span className="private-pill">
              <ShieldCheck size={14} />
              {auth.public_access ? "手机工作台" : "私有工作台"}
            </span>
            <button
              className="budget-pill"
              onClick={() => { if (auth.public_access) { notify("预算设置请在电脑工作台查看"); return; } setSettingsSection("budget"); setPage("settings"); }}
              title="本月数据服务与DeepSeek合计预估占用，含待确认额度；点击查看明细"
            >
              <span>本月预估</span> ¥{used.toFixed(2)}
              <small>/ {overview.budget.total}</small>
            </button>
            <button className="avatar" onClick={() => auth.public_access ? notify("账号与连接设置请在电脑工作台操作") : setPage("settings")}>
              我
            </button>
          </div>
        </header>
        {offline && (
          <div className="offline-banner">
            <CloudOff size={16} />
            正在重新连接电脑。离线时请保留当前内容，恢复连接后再保存。
          </div>
        )}
        <main className="main-content">
          {page === "topics" || page === "saved" ? <Discovery key={page} initialMode={page==='saved'?'saved':'hot'} view={page==='saved'?'saved':topicView} onViewChange={page==='topics'?setTopicView:undefined} mobileTasks={mobileTasks} showJobs={openJobs} refresh={refresh} reload={reload} notify={notify} open={openDetail} queue={queue} settings={settings} jobs={overview.jobs} goCreators={()=>auth.public_access ? notify("关键词与对标请在电脑工作台管理") : setPage('creators')} goRules={()=>{if(auth.public_access)notify('筛选条件请在电脑工作台修改');else{setSettingsSection('profile');setPage('settings');}}}/> : page === "favorites" ? (
            <Library key={page} page={page} mobileTasks={mobileTasks} settings={settings} overview={overview} refresh={refresh} notify={notify} reload={reload} queue={queue} open={openDetail} add={()=>setAdding(true)} goSettings={()=>auth.public_access ? notify("连接设置请在电脑工作台操作") : setPage('settings')}/>
          ) : <Settings key={page} initialSection={page==='creators'?'creators':settingsSection} settings={settings} overview={overview} reload={reload} notify={notify} queue={queue} showJobs={openJobs}/>}
        </main>
        <footer className="page-footer">
          <span>
            <span className="live-dot" />
            你的灵感，正在这里生长
          </span>
          <span>本地存储 · 双端共享</span>
        </footer>
      </div>
      <nav className="mobile-nav" aria-label="手机主导航">
        {phoneTabs.map(t=>{
          const active=page===t.page&&(t.page!=='topics'||topicView===t.view);
          return <button type="button" key={t.id} className={active&&!showJobs?'active':''} aria-label={t.label} aria-current={active?'page':undefined} onClick={()=>{setShowJobs(false);if(t.view)setTopicView(t.view);setPage(t.page);}}><t.icon size={22}/><span>{t.label}</span></button>;
        })}
      </nav>
      {activeJob && !showJobs && !toast && (
        <div className="floating-job">
          <button className="floating-job-main" onClick={openJobs}><LoaderCircle className="spin" size={16} />{jobNames[activeJob.kind] || "任务"}<span>{activeJob.progress || 0}%</span></button>
          <button className="floating-job-cancel" onClick={() => cancelTask(activeJob.id)}>取消</button>
        </div>
      )}
      {adding && (
        <AddMaterial
          close={() => setAdding(false)}
          notify={notify}
          added={(id) => {
            setAdding(false);
            reload();
            openDetail(id);
          }}
        />
      )}
      {detailId && (
        <MaterialDetail
          key={detailId}
          id={detailId}
          initialTab={detailInitialTab}
          close={() => setDetailId(null)}
          settings={settings}
          notify={notify}
          queue={queue}
          refresh={refresh}
          reload={reload}
          task={detailTask}
          latestTask={latestDetailTask}
          cancelTask={cancelTask}
        />
      )}
      {showJobs && (
        <Jobs
          close={() => setShowJobs(false)}
          refresh={refresh}
          notify={notify}
          reload={reload}
        />
      )}
      {toast && (
        <div className={`toast ${toast.error ? "error" : ""} ${toast.action==='jobs'?'actionable':''}`} role="status">
          {toast.action==='jobs'?<button className="toast-message" aria-label="查看任务中心" onClick={openJobs}>
            {toast.error?<AlertCircle size={18}/>:<CheckCircle2 size={18}/>}
            <span>{toast.text}<small>点击查看任务中心 <ChevronRight size={12}/></small></span>
          </button>:<>{toast.error?<AlertCircle size={18}/>:<CheckCircle2 size={18}/>}<span>{toast.text}</span></>}
          <button className="toast-close" aria-label="关闭通知" onClick={dismissToast}><X size={14}/></button>
        </div>
      )}
    </div>
  );
}

function ShortlistButton({ video, notify, reload, disabled = false }: {
  video: Row;
  notify: Notice;
  reload: () => void;
  disabled?: boolean;
}) {
  const [saving, setSaving] = useState(false);
  const shortlisted = video.state === "备选";
  if (!shortlisted && video.state !== "待看") return null;
  const label = shortlisted ? "取消备选" : "加入备选";
  async function toggle() {
    setSaving(true);
    try {
      await api("/videos/" + video.id, "PATCH", { state: shortlisted ? "待看" : "备选" });
      notify(shortlisted ? "已取消备选，恢复为待看" : "已加入备选");
      reload();
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setSaving(false);
    }
  }
  return <Button onClick={toggle} label={label} pressed={shortlisted} disabled={disabled || saving}>
    <Bookmark size={15} fill={shortlisted ? "currentColor" : "none"} />
    {label}
  </Button>;
}

function Library({
  page,
  settings,
  overview,
  refresh,
  notify,
  reload,
  queue,
  open,
  add,
  goSettings,
  mobileTasks,
}: {
  page: string;
  mobileTasks?: ReactNode;
  settings: Row | null;
  overview: Row;
  refresh: number;
  notify: Notice;
  reload: () => void;
  queue: (kind: string, payload?: Row) => void;
  open: (id: string) => void;
  add: () => void;
  goSettings: () => void;
}) {
  const favorite = page === "favorites";
  const [items, setItems] = useState<Row[]>([]);
  const [followerTracking, setFollowerTracking] = useState<Row>({});
  const [total, setTotal] = useState(0);
  const [fetching, setFetching] = useState(true);
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState("low");
  const [days, setDays] = useState("3");
  const [shortlistOnly, setShortlistOnly] = useState(false);
  const [loadedShortlistOnly, setLoadedShortlistOnly] = useState(false);
  const windowLabel = days === "7" ? "最近7天" : `最近${Number(days) * 24}小时`;
  const [category, setCategory] = useState("");
  const [state, setState] = useState("");
  const [source, setSource] = useState("");
  const [tool, setTool] = useState("");
  const [purpose, setPurpose] = useState("");
  const [aiOnly, setAiOnly] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [offset, setOffset] = useState(0);
  const pageSize = 60;
  const librarySection = useRef<HTMLElement>(null);
  const [groupSimilar, setGroupSimilar] = useState(false);
  const [track, setTrack] = useState("");
  const [collectionGroup, setCollectionGroup] = useState("");
  const [collectionUnconfirmed, setCollectionUnconfirmed] = useState(false);
  const [collectionOptions, setCollectionOptions] = useState(collectionGroups);
  const listKey = JSON.stringify([page, query, category, state, mode, days, source, aiOnly, tool, purpose, track, collectionGroup, collectionUnconfirmed, shortlistOnly, offset]);
  const [loadedListKey, setLoadedListKey] = useState("");
  // A background refresh must not unmount cards and discard an unfinished category input.
  const loading = loadedListKey !== listKey || loadedShortlistOnly !== shortlistOnly || (fetching && items.length === 0);
  useEffect(() => {
    if (!favorite) return;
    let current = true;
    api("/collection-groups").then(r => { if (current) setCollectionOptions(r.groups); }).catch(e => { if (current) notify(e.message, true); });
    return () => { current = false; };
  }, [favorite, refresh]);
  const [showFilters, setShowFilters] = useState(false);
  const [bulkMode, setBulkMode] = useState(false);
  const [showAllCandidates, setShowAllCandidates] = useState(false);
  const [listTick, setListTick] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") setListTick((n) => n + 1);
    }, 60000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    const t = setTimeout(() => setQuery(q), 300);
    return () => clearTimeout(t);
  }, [q]);
  useEffect(() => {
    setSelected([]);
    setOffset(0);
  }, [page, query, category, state, mode, days, source, aiOnly, tool, purpose, track, collectionGroup, collectionUnconfirmed, shortlistOnly]);
  useEffect(() => {
    let current = true;
    setFetching(true);
    const p = new URLSearchParams({
      q: query,
      source,
      scope: favorite ? "personal" : shortlistOnly ? "discovery" : "recommendations",
      category,
      state: shortlistOnly ? "备选" : state,
      mode: favorite || shortlistOnly ? "recent" : mode,
      days: favorite || shortlistOnly ? "0" : days,
      ai_only: String(favorite || shortlistOnly ? aiOnly : true),
      limit: String(pageSize),
      offset: String(offset),
      tool,
      purpose,
      track,
      collection_group: favorite ? collectionGroup : "",
      collection_unconfirmed: String(favorite && collectionUnconfirmed),
    });
    api("/videos?" + p)
      .then((r) => {
        if (current) {
          if (offset && offset >= r.total) {
            setOffset(Math.max(0, Math.floor((r.total - 1) / pageSize) * pageSize));
          }
          setItems(r.items);
          setTotal(r.total);
          setLoadedListKey(listKey);
        }
      })
      .catch((e) => {
        if (current) { setItems([]); setTotal(0); setLoadedListKey(listKey); notify(e.message, true); }
      })
      .finally(() => {
        if (current) {
          setLoadedShortlistOnly(shortlistOnly);
          setFetching(false);
        }
      });
    return () => {
      current = false;
    };
  }, [
    page,
    query,
    category,
    state,
    mode,
    days,
    source,
    aiOnly,
    tool,
    purpose,
    offset,
    refresh,
    listTick,
    settings?.low_followers,
    settings?.low_min_likes,
    settings?.low_ratio,
    track,
    collectionGroup,
    collectionUnconfirmed,
    shortlistOnly,
  ]);
  async function saveCollection(ids: string[], group: string) {
    if (ids.length === 1) await api('/videos/' + ids[0], 'PATCH', { collection_group: group });
    else await api('/videos/batch', 'POST', { ids, changes: { collection_group: group } });
    notify(group ? `已归到「${collectionLabel(group)}」${ids.length > 1 ? ` · ${ids.length}条` : ''}` : '已恢复自动分类');
    setSelected([]);
    reload();
  }
  async function batch(changes: Row) {
    try {
      await api("/videos/batch", "POST", { ids: selected, changes });
      notify("已更新 " + selected.length + " 条素材");
      setSelected([]);
      reload();
    } catch (e) {
      notify((e as Error).message, true);
    }
  }
  const emptyAll = overview.counts.materials === 0;
  const extraFilterCount = [category, state, source, tool, purpose, aiOnly, groupSimilar].filter(Boolean).length;
  const hasFilters = !!(q || track || collectionGroup || collectionUnconfirmed || extraFilterCount || (!favorite && days !== "3"));
  const showPicks = !favorite && !shortlistOnly && !hasFilters && !bulkMode && offset === 0;
  const picks = showPicks ? pickTopics(items as (Row & { id: string; title: string })[]) : [];
  const collapseCandidates = picks.length > 0 && !showAllCandidates;
  useEffect(() => {
    if (favorite || shortlistOnly || loading || !items.length) return;
    let current = true;
    const shown = collapseCandidates ? pickTopics(items as (Row & { id: string; title: string })[]).map(pick => pick.video) : items;
    api("/recommendations/record", "POST", { ids: shown.map(v => v.id) })
      .then(tracked => { if (current) setFollowerTracking(tracked.records); })
      .catch(e => { if (current) notify(e.message, true); });
    return () => { current = false; };
  }, [favorite, shortlistOnly, loading, items, collapseCandidates, notify]);
  function resetFilters() {
    setDays("3"); setCategory(""); setState(""); setQ(""); setSource("");
    setTool(""); setPurpose(""); setAiOnly(false); setMode("low"); setTrack(""); setGroupSimilar(false);
    setCollectionGroup(""); setCollectionUnconfirmed(false);
  }
  function toggleShortlist() {
    resetFilters();
    setShowFilters(false);
    setBulkMode(false);
    setShortlistOnly(value => !value);
  }
  function changeListPage(next: number) {
    setSelected([]);
    setOffset(next);
    librarySection.current?.scrollIntoView({ block: "start" });
  }
  const grouped = new Map<string, Row[]>();
  for (const v of items) {
    const key = groupSimilar ? v.topic_key || v.id : v.id;
    grouped.set(key, [...(grouped.get(key) || []), v]);
  }
  const card = (v: Row) => (
    <MaterialCard
      key={v.id}
      v={{ ...v, follower_tracking: shortlistOnly ? undefined : followerTracking[v.id], sources: v.sources?.filter((entry: Row) => (favorite ? ['favorites', 'manual'] : ['creator', 'search']).includes(entry.kind)) }}
      selected={selected.includes(v.id)}
      selectable={bulkMode}
      collectionOptions={collectionOptions}
      classify={favorite ? group => saveCollection([v.id], group) : undefined}
      toggle={() =>
        setSelected((s) =>
          s.includes(v.id) ? s.filter((x) => x !== v.id) : [...s, v.id],
        )
      }
      open={() => open(v.id)}
      shortlist={<ShortlistButton video={v} notify={notify} reload={reload} />}
      refreshMetadata={() => queue("video_refresh", { video_id: v.id })}
      metadataEstimate={settings?.pricing?.data_request_usd != null && settings?.usd_cny != null ? Number(settings.pricing.data_request_usd) * Number(settings.usd_cny) * 2 : undefined}
    />
  );
  return (
    <>
      <section className="page-heading">
        <div>
          <div className="eyebrow">
            {favorite ? "YOUR PERSONAL COLLECTION" : "FIND YOUR NEXT GOOD IDEA"}
          </div>
          <h1>
            {favorite ? "我的收藏" : "低粉选题"}
            <span className="heading-dot" />
          </h1>
          <p>
            {shortlistOnly
              ? favorite ? "从个人收藏里留作备选的素材。" : "从对标和搜索中留作备选的素材，按标题或作者找回。"
              : favorite
              ? "找回有用的视频，按用途分类，或加入备选选题留到以后。"
              : `${windowLabel}的低粉高互动候选。个人收藏在「收藏」里单独查看。`}
          </p>
        </div>
        <div className="heading-actions">{mobileTasks}
          <Button
            kind="outline"
            disabled={favorite && !settings?.edge_bridge?.paired}
            title={favorite && !settings?.edge_bridge?.paired ? "先在「我的」连接现有浏览器；已导入的收藏可正常浏览与创作。" : undefined}
            onClick={() => queue(favorite ? "favorites_sync" : "creators_sync", favorite ? {foreground:true} : {})}
          >
            <RefreshCw size={16} />
            {favorite ? "同步收藏" : "更新对标"}
          </Button>
          <Button kind="primary" onClick={add}>
            <Plus size={17} />
            添加链接
          </Button>
        </div>
      </section>
      <details className="library-summary" hidden={shortlistOnly}>
        <summary>{favorite ? `已收录 ${overview.counts.personal_materials || 0} 条 · 查看统计` : `当前 ${total} 条候选 · 查看筛选规则与统计`}</summary>
      <div className="stat-grid">
        {(favorite
          ? [
              {
                label: "收藏素材",
                value: overview.counts.personal_materials,
                icon: Bookmark,
                sub: "抖音收藏与手动收录",
              },
              {
                label: "待整理素材",
                value: overview.counts.personal_unclassified,
                icon: Layers3,
                sub: "支持批量 AI 分类",
              },
              {
                label: "已加入备选",
                value: overview.counts.personal_shortlisted,
                icon: CheckCheck,
                sub: "留给下一条作品",
              },
              {
                label: "正在创作",
                value: overview.counts.personal_projects,
                icon: PenLine,
                sub: "脚本与版本持续保存",
              },
            ]
          : [
              {
                label: "系统发现的作品",
                value: overview.counts.discovery_materials,
                icon: Layers3,
                sub: "对标与搜索 · 已排除个人收藏",
              },
              {
                label: "当前低粉候选",
                value: total,
                icon: Sparkles,
                sub: "符合当前时间与筛选条件",
              },
              {
                label: "监控中的博主",
                value: overview.counts.creators,
                icon: Users,
                sub: `白天每${(settings?.automation?.creator_minutes || 240) / 60}小时检查`,
              },
              {
                label: "推荐选题的创作项目",
                value: overview.counts.discovery_projects,
                icon: PenLine,
                sub: "从开头到完整脚本",
              },
            ]
        ).map((s) => (
          <div className="stat-card" key={s.label}>
            <div>
              <span>{s.label}</span>
              <s.icon size={17} />
            </div>
            <strong>{s.value || 0}</strong>
            <small>{s.sub}</small>
          </div>
        ))}
      </div>
        {!favorite && <p className="list-explainer">以下条件必须同时满足：{windowLabel}、粉丝不超过{Number(settings?.low_followers ?? 50000).toLocaleString("zh-CN")}、点赞至少{Number(settings?.low_min_likes ?? 1000).toLocaleString("zh-CN")}、点赞/粉丝至少{Math.round((settings?.low_ratio ?? 0.25) * 10000) / 100}%。仅对标与搜索作品，排除全部个人收藏；粉丝未知或采样过期不入榜。数据由TikHub提供，属于当前采集样本的候选，并非全站榜单。</p>}
      </details>
      {emptyAll && (
        <section className="welcome-card">
          <div className="welcome-copy">
            <Tag green>从这里开始</Tag>
            <h2>
              你的下一条好作品，
              <br />
              从一条灵感开始。
            </h2>
            <p>连接收藏或添加链接，工作台会帮你把素材整理成可用的选题。</p>
            <Button kind="primary" onClick={add}>
              添加第一条灵感
              <ArrowUpRight size={16} />
            </Button>
          </div>
          <div className="onboarding-steps">
            {[
              {
                n: "01",
                icon: Bookmark,
                title: "连接抖音收藏",
                text: "手机随手收藏，回到这里按分类查找",
                done: overview.collection_state?.status === "authenticated",
              },
              {
                n: "02",
                icon: Users,
                title: "添加你的对标博主",
                text: "收拢账号新作品，积累真实数据快照",
                done: overview.counts.creators > 0,
              },
              {
                n: "03",
                icon: Sparkles,
                title: "接入 AI，开始创作",
                text: "拆解方法，先选前5秒，再写正文",
                done: overview.configured.deepseek,
              },
            ].map((s) => (
              <button key={s.n} onClick={goSettings}>
                <span className="step-number">
                  {s.done ? <Check size={16} /> : s.n}
                </span>
                <div>
                  <strong>{s.title}</strong>
                  <p>{s.text}</p>
                </div>
                <ArrowUpRight size={17} />
              </button>
            ))}
          </div>
        </section>
      )}
      <section className="library-section" ref={librarySection}>
        <div className="section-title">
          <div>
            <h2>{shortlistOnly ? (favorite ? "收藏备选" : "选题备选") : favorite ? "收藏素材库" : "选题灵感库"}</h2>
            <span>
              {loading ? "载入中" : `${total} 条${shortlistOnly ? "" : !favorite ? " · 当前采集样本" : ""}`}
            </span>
          </div>
          <span className="sync-note">
            <Clock3 size={13} />
            {favorite
              ? `最近同步 ${fmtDate(overview.last_sync?.favorites)}`
              : overview.last_sync?.creators
                ? `对标更新 ${fmtDate(overview.last_sync.creators)}`
                : "尚未开始采集"}
          </span>
          </div>
        {favorite && <details className="collection-data-help"><summary>数据为何显示未知？</summary><p>收藏同步先收录标题、作者、封面和链接。发布时间、粉丝及互动数据按需点「补全信息」查询，避免为全部历史收藏逐条付费。补全通常调用两次 TikHub；失败重试可能增加费用，也可能另行触发 DeepSeek 初分类。已有数据不会因重新打开收藏列表而自动刷新。</p></details>}
        {favorite && <div className="collection-groups" role="group" aria-label="收藏分类">
          <Button pressed={!collectionGroup} onClick={() => setCollectionGroup("")}>全部</Button>
          {Object.entries(collectionOptions).map(([key, label]) =>
            <Button key={key} pressed={collectionGroup === key} onClick={() => setCollectionGroup(key)}>{label}</Button>
          )}
          <Button pressed={collectionUnconfirmed} onClick={() => setCollectionUnconfirmed(v => !v)}>只看未确认分类</Button>
        </div>}
        <div className="filters">
          <div className="search-field">
            <Search size={17} />
            <input
              aria-label="搜索素材"
              placeholder="搜索标题、作者、工具或逐字稿…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            {q && (
              <button
                className="icon-button"
                aria-label="清除搜索"
                onClick={() => setQ("")}
              >
                <X size={14} />
              </button>
            )}
          </div>
          {!favorite && <select aria-label="内容方向" value={track} onChange={e=>setTrack(e.target.value)}><option value="">全部方向</option><option>AI教程</option><option>AIGC</option><option>其他</option></select>}
          {!favorite && !shortlistOnly && <select aria-label="作品时间范围" value={days} onChange={e=>setDays(e.target.value)}><option value="1">最近24小时</option><option value="3">最近72小时</option><option value="7">最近7天</option></select>}
          <Button onClick={toggleShortlist} pressed={shortlistOnly}><Bookmark size={15}/>{shortlistOnly ? "返回列表" : "只看备选"}</Button>
          {!shortlistOnly && <>
            <Button onClick={() => setShowFilters(v => !v)} pressed={showFilters} label="更多筛选"><SlidersHorizontal size={15}/>更多筛选{extraFilterCount ? ` (${extraFilterCount})` : ""}</Button>
            <Button onClick={() => { setBulkMode(v => !v); setSelected([]); }} pressed={bulkMode}>{bulkMode ? "完成整理" : "批量整理"}</Button>
          </>}
          {hasFilters && <Button onClick={resetFilters} kind="subtle">清除筛选</Button>}
        </div>
        <div className="advanced-filters" hidden={!showFilters}>
        <div className="filters">
          <select
            aria-label="主题分类"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
          >
            <option value="">全部主题</option>
            {settings?.categories.map((c: string) => (
              <option key={c}>{c}</option>
            ))}
          </select>
          <select
            aria-label="创作状态"
            value={state}
            onChange={(e) => setState(e.target.value)}
          >
            <option value="">全部状态</option>
            {settings?.states.map((s: string) => (
              <option key={s}>{s}</option>
            ))}
          </select>
          {(
            <select
              aria-label="素材来源"
              value={source}
              onChange={(e) => setSource(e.target.value)}
            >
              <option value="">{favorite ? "全部个人收录" : "全部发现来源"}</option>
              {Object.entries(sources).filter(([kind]) => (favorite ? ['favorites', 'manual'] : ['creator', 'search']).includes(kind)).map(([k, v]) => (
                <option value={k} key={k}>
                  {v}
                </option>
              ))}
            </select>
          )}
          {favorite && (
            <label className="check-line">
              <input
                type="checkbox"
                checked={aiOnly}
                onChange={(e) => setAiOnly(e.target.checked)}
              />
              仅AI相关
            </label>
          )}
        </div>
        {favorite && (
          <div className="secondary-filters">
            <input
              aria-label="工具标签"
              placeholder="工具标签，如 Codex"
              value={tool}
              onChange={(e) => setTool(e.target.value)}
            />
            <select
              aria-label="素材用途"
              value={purpose}
              onChange={(e) => setPurpose(e.target.value)}
            >
              <option value="">全部用途</option>
              {settings?.purposes.map((s: string) => (
                <option key={s}>{s}</option>
              ))}
            </select>
            <Button disabled={!settings?.edge_bridge?.paired} onClick={() => queue("favorites_backfill", {foreground:true})} kind="subtle">
              <History size={15} />
              前台整理历史收藏
            </Button>
            <span className="muted">手动同步会打开同一Edge中的收藏页；请保持前台。收藏时间未知时不以发布时间代替。</span>
          </div>
        )}
        <label className="check-line group-toggle">
          <input
            type="checkbox"
            checked={groupSimilar}
            onChange={(e) => setGroupSimilar(e.target.checked)}
          />
          合并相近选题 <small>对已加载素材按题材归组，保留全部原始作品</small>
        </label>
        </div>
        {selected.length > 0 && (
          <div className="bulk-bar">
            <strong>已选 {selected.length} 条</strong>
            {favorite && <CollectionClassifier key={selected.join(',')} options={collectionOptions} bulk save={group => saveCollection(selected, group)}/>}
            <Button onClick={() => batch({ state: "备选" })}>加入备选</Button>
            <select
              aria-label="批量设置主题"
              defaultValue=""
              onChange={(e) => {
                if (e.target.value) batch({ category: e.target.value });
                e.target.value = "";
              }}
            >
              <option value="">批量设置主题</option>
              {settings?.categories.map((c: string) => (
                <option key={c}>{c}</option>
              ))}
            </select>
            <Button
              onClick={() => queue("classify", { ids: selected.slice(0, 20) })}
            >
              <Sparkles size={14} />
              AI分类（最多20条）
            </Button>
            <Button onClick={() => batch({ state: "已归档" })}>归档</Button>
            <button
              className="icon-button"
              aria-label="取消选择"
              onClick={() => setSelected([])}
            >
              <X size={16} />
            </button>
          </div>
        )}
        {showPicks && !loading && <section className="today-picks" aria-labelledby="today-picks-title">
          <div className="inline-heading"><h2 id="today-picks-title"><Sparkles size={18}/>今天值得拍</h2><span>{picks.length} 条建议</span></div>
          <p className="hint">从最近72小时的低粉候选里挑有实操线索的题。拍法来自标题初筛，采用前先核对原片。</p>
          {picks.length ? <ol>{picks.map(({ video, reason, demo, verify }) => <li key={video.id}>
            <div><button className="pick-title" onClick={() => open(video.id)}>{video.title.split(/[#＃]/, 1)[0].trim() || video.title}</button>
              <p>{video.author || "作者待补全"} · {fmtNumber(video.followers)} 粉丝 · {fmtNumber(video.likes)} 赞 · 发布于 {fmtDate(video.published_at)}{followerTracking[video.id]?.delta != null ? ` · 较上次推荐 ${followerTracking[video.id].delta > 0 ? '+' : ''}${fmtNumber(followerTracking[video.id].delta)} 粉丝` : ''}</p>
              <p>{reason}</p><p><strong>可以怎么拍</strong> {demo}</p><p><strong>拍前确认</strong> {verify}</p></div>
            <div className="pick-actions">
              <Button onClick={() => open(video.id)}>看素材<ChevronRight size={14}/></Button>
              <ShortlistButton video={video} notify={notify} reload={reload} />
            </div>
          </li>)}</ol> : items.length ? <p className="hint">这批候选暂时缺少明确的实操线索，可以继续浏览下方候选。</p> : <>
            <p className="hint">最近72小时暂无符合条件的候选。</p>
            <Button onClick={() => setDays("7")}>看近7天<ChevronRight size={14}/></Button>
          </>}
          {picks.length > 0 && <Button pressed={showAllCandidates} onClick={() => setShowAllCandidates(!showAllCandidates)}>{showAllCandidates ? "收起候选列表" : `查看全部 ${total} 条候选`}<ChevronDown size={15}/></Button>}
        </section>}
        {loading ? (
          <div className="loading-block">
            <LoaderCircle className="spin" size={22} />
          </div>
        ) : items.length === 0 && showPicks && !emptyAll ? null : items.length === 0 ? (
          <Empty
            icon={favorite ? <Bookmark size={28} /> : <Search size={28} />}
            title={
              shortlistOnly && !hasFilters ? "这里还没有备选" : emptyAll ? "素材库正在等待你的第一条灵感" : "当前筛选下还没有素材"
            }
            text={
              shortlistOnly && !hasFilters ? "看到想以后再用的素材，点「加入备选」就能在这里找回。"
                : emptyAll
                ? "先添加一条抖音链接，也可以前往「我的」连接收藏和对标账号。"
                : favorite ? "可以调整分类、状态或搜索词。收藏不会因为互动低而被剔除。" : "当前采集样本暂无符合条件的低粉作品，可切换近7天或等待更新。这里不会使用你的收藏补位。"
            }
          >
            {shortlistOnly && !hasFilters ? (
              <Button onClick={toggleShortlist}>返回列表</Button>
            ) : emptyAll ? (
              <Button onClick={add}>
                <Plus size={16} />
                添加抖音链接
              </Button>
            ) : (
              <Button
                onClick={resetFilters}
              >
                重置筛选
              </Button>
            )}
          </Empty>
        ) : collapseCandidates ? null : (
          <div className="material-list">
            {[...grouped.entries()].map(([key, members]) => (
              <div className="topic-group" key={key}>
                {card(members[0])}
                {members.length > 1 && (
                  <details>
                    <summary>
                      <Layers3 size={14} />
                      同组选题还有 {members.length - 1} 条作品 · 展开比较
                    </summary>
                    {members.slice(1).map(card)}
                  </details>
                )}
              </div>
            ))}
          </div>
        )}
        {!loading && !collapseCandidates && total > pageSize && (
          <div className="load-more">
            <Button disabled={!offset} onClick={() => changeListPage(offset - pageSize)}>上一页</Button>
            <span className="muted">{offset + 1}–{offset + items.length} / {total}</span>
            <Button disabled={offset + items.length >= total} onClick={() => changeListPage(offset + pageSize)}>下一页<ChevronRight size={15}/></Button>
          </div>
        )}
      </section>
    </>
  );
}

function MaterialCard({
  v,
  selected,
  selectable,
  toggle,
  open,
  shortlist,
  refreshMetadata,
  metadataEstimate,
  collectionOptions,
  classify,
}: {
  v: Row;
  selected: boolean;
  selectable: boolean;
  toggle: () => void;
  open: () => void;
  shortlist: ReactNode;
  refreshMetadata: () => void;
  metadataEstimate?: number;
  collectionOptions: Record<string, string>;
  classify?: (group: string) => Promise<void>;
}) {
  return (
    <article className={`material-card ${selected ? "selected" : ""}`}>
      {selectable && <label className="card-check" aria-label={"选择" + v.title}>
        <input type="checkbox" checked={selected} onChange={toggle} />
      </label>}
      <a
        className="material-cover"
        href={v.url}
        target="_blank"
        rel="noopener noreferrer"
        aria-label="点击封面打开原视频"
        title="打开原视频"
      >
        <CoverImage id={v.id} cover={v.cover} alt="视频封面"/>
        <CoverPlayHint image={v.media_kind === "image"}/>
        <CoverDuration duration={v.duration} image={v.media_kind === "image"}/>
      </a>
      <div className="material-body">
        <div className="material-meta">
          <span className="douyin-badge">抖音</span>
          <strong>{v.author || "作者待补全"}</strong>
          <button className="author-followers" onClick={open} title={`粉丝采集于 ${fmtFullDate(v.followers_at)}；在数据快照中查看记录`}>
            {v.followers == null ? "粉丝未知" : `${Number(v.followers).toLocaleString("zh-CN")} 粉丝`}
          </button>
          <div className="material-source">
            {v.sources
              ?.map((s: Row) => sources[s.kind])
              .filter((x: string, i: number, a: string[]) => a.indexOf(x) === i)
              .join(" / ")}
          </div>
        </div>
        <div className="material-publication">
          <span>{v.published_at ? `发布于 ${fmtFullDate(v.published_at)}` : "发布时间未知"}</span>
          {(!v.published_at || [v.followers,v.likes,v.comments,v.favorites].some(value => value == null)) && <button onClick={refreshMetadata} title="通常查询作品详情和作者资料两次，实际按请求计费；可能另有DeepSeek分类费">补全信息{metadataEstimate != null && Number.isFinite(metadataEstimate) ? ` · 约¥${metadataEstimate.toFixed(3)}` : ''}</button>}
        </div>
        {v.follower_tracking?.delta != null && <div className="follower-observation">较上次推荐 {v.follower_tracking.delta > 0 ? "+" : ""}{Number(v.follower_tracking.delta).toLocaleString("zh-CN")} 粉丝（{fmtDate(v.follower_tracking.previous.shown_at)}）</div>}
        <button className="title-link" onClick={open}>
          <h3>{v.title}</h3>
        </button>
        <div className="metric-row">
          <span>
            <Heart size={14} />
            点赞 {v.likes == null ? "未知" : fmtNumber(v.likes)}
          </span>
          <span>
            <MessageCircle size={14} />
            评论 {v.comments == null ? "未知" : fmtNumber(v.comments)}
          </span>
          <span>
            <Bookmark size={14} />
            收藏 {v.favorites == null ? "未知" : fmtNumber(v.favorites)}
          </span>
          {v.growth_per_hour != null && (
            <span className="growth">
              <TrendingUp size={14} />+{Math.round(v.growth_per_hour)}/时
            </span>
          )}
        </div>
        <div className="card-tags">
          <Tag>{v.category}</Tag>
          {v.collection_group
            ? <Tag green>{collectionLabel(v.collection_group)}{v.collection_group_manual ? " · 已确认" : ""}</Tag>
            : v.content_tracks?.map((t:string)=><Tag key={'track-'+t}>{t}</Tag>)}
          {v.tools?.slice(0, 2).map((t: string) => (
            <Tag key={t}>{t}</Tag>
          ))}
          {v.low_follower_candidate && <Tag green>低粉高互动</Tag>}
          {v.state !== "待看" && <Tag green>{v.state}</Tag>}
          {v.manual_classification ? (
            <span className="classification-note">
              <Check size={12} />
              人工确认
            </span>
          ) : (
            <span className="classification-note">
              {v.classification_method?.startsWith("AI")
                ? "AI初步分类"
                : "初步分类"}
            </span>
          )}
        </div>
        {classify && <CollectionClassifier group={v.collection_group} confirmed={!!v.collection_group_manual} options={collectionOptions} save={classify}/>}
        {v.recommendation && (
          <p className="recommendation">
            <Sparkles size={13} />
            {v.recommendation}
          </p>
        )}
        <div className="card-bottom">
          <div className="card-buttons">
            <a className="button primary" href={v.url} target="_blank" rel="noreferrer"><ExternalLink size={14}/>打开原视频</a>
            <Button onClick={open}><FileText size={14}/>看文稿 / 拆开头</Button>
            {shortlist}
            <details className="card-more"><summary aria-label="更多素材操作">更多<ChevronDown size={13}/></summary><div>
              <a href={v.url} target="_blank" rel="noreferrer"><ExternalLink size={14}/>打开抖音原视频</a>
              <button onClick={refreshMetadata}><RefreshCw size={14}/>更新这条数据</button>
              <small>粉丝采集：{fmtFullDate(v.followers_at)}<br/>数据更新：{fmtFullDate(v.metrics_at)}</small>
            </div></details>
          </div>
          <span className="updated">数据 {fmtDate(v.metrics_at)}</span>
        </div>
      </div>
    </article>
  );
}

function AddMaterial({
  close,
  notify,
  added,
}: {
  close: () => void;
  notify: Notice;
  added: (id: string) => void;
}) {
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const r = await api("/import", "POST", { url, title });
      notify(r.message);
      added(r.video_id);
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title="留住一条好灵感"
      subtitle="粘贴抖音作品链接或整段分享内容"
      close={close}
    >
      <form onSubmit={submit} className="modal-content">
        <Field label="抖音分享链接">
          <textarea
            autoFocus
            rows={4}
            placeholder="https://v.douyin.com/… 或 https://www.douyin.com/video/…"
            required
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
        </Field>
        <Field
          label="标题或备注（可选）"
          help="尚未配置数据接口时，也能先保存链接并补充逐字稿。"
        >
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="这条视频哪里值得参考？"
          />
        </Field>
        <div className="modal-footer">
          <Button onClick={close}>取消</Button>
          <Button kind="primary" type="submit" disabled={busy}>
            {busy ? (
              <LoaderCircle className="spin" size={16} />
            ) : (
              <Plus size={16} />
            )}
            加入素材库
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function MaterialDetail({
  id,
  initialTab="result",
  close,
  settings,
  notify,
  queue,
  refresh,
  reload,
  task,
  latestTask,
  cancelTask,
}: {
  id: string;
  initialTab?: string;
  close: () => void;
  settings: Row | null;
  notify: Notice;
  queue: (kind: string, payload?: Row) => Promise<Row | null>;
  refresh: number;
  reload: () => void;
  task?: Row;
  latestTask?: Row;
  cancelTask: (id: string) => void;
}) {
  const [v, setV] = useState<Row | null>(null);
  const [tab, setTab] = useState(initialTab);
  const [text, setText] = useState("");
  const [dirty, setDirty] = useState(false);
  const [submitting, setSubmitting] = useState<string | null>(null);
  const submitLock = useRef(false);
  const [edit, setEdit] = useState<Row>({});
  const labelsDirty = useRef(false);
  function changeLabels(value: Row) {
    labelsDirty.current = true;
    setEdit(value);
  }
  const load = useCallback(async () => {
    try {
      const r = await api("/videos/" + id);
      setV(r);
      if (!dirty) setText(r.transcript?.text || "");
      if (!labelsDirty.current)
        setEdit({
          title: r.title,
          category: r.category,
          state: r.state,
          is_ai: !!r.is_ai,
          tools: r.tools.join(", "),
          purposes: r.purposes,
          notes: r.notes,
          content_tracks: r.content_tracks,
          ...(r.collection_group ? { collection_group: r.collection_group_manual ? r.collection_group : "" } : {}),
        });
    } catch (e) {
      notify((e as Error).message, true);
    }
  }, [id, dirty, notify]);
  useEffect(() => {
    load();
  }, [id, refresh]);
  async function saveText() {
    try {
      await api("/videos/" + id + "/transcript", "PUT", { text });
      setDirty(false);
      notify("逐字稿已保存");
      reload();
      load();
    } catch (e) {
      notify((e as Error).message, true);
    }
  }
  async function saveLabels() {
    try {
      await api("/videos/" + id, "PATCH", {
        ...edit,
        tools: edit.tools
          .split(/[,，]/)
          .map((t: string) => t.trim())
          .filter(Boolean),
      });
      notify("已保存，人工分类将优先保留");
      labelsDirty.current = false;
      reload();
      load();
    } catch (e) {
      notify((e as Error).message, true);
    }
  }
  const collectionExample = v?.collection_group === "aigc_example";
  const visualFirst = v?.media_kind !== "image" && (collectionExample || (v?.collection_group !== "aigc_tutorial" && v?.content_tracks?.includes("AIGC") && !v?.content_tracks?.includes("AI教程")));
  const needsReading = visualFirst ? !v?.visual_analysis : !v?.analysis;
  const readingBusy = !!task || !!submitting;
  async function startReading(kind: "analyze" | "visual_analyze", force = false) {
    if (submitLock.current || task) return;
    submitLock.current = true;
    setSubmitting(kind);
    try { await queue(kind, { video_id: id, ...(force ? { force: true } : {}) }); }
    finally { submitLock.current = false; setSubmitting(null); }
  }
  return (
    <Modal
      title="素材参考"
      subtitle="先判断是否值得参考，需要时再提取文稿或拆解开头"
      close={() => {
        if (
          (!dirty && !labelsDirty.current) ||
          confirm("有未保存的逐字稿或分类，确定关闭？")
        )
          close();
      }}
      wide
    >
      {!v ? (
        <div className="loading-block">
          <LoaderCircle className="spin" />
        </div>
      ) : (
        <>
          <div className="detail-source">
            <div>
              <span className="douyin-badge">抖音</span>
              <span>{v.author || "作者待补全"}</span>
              <span>{v.published_at ? `发布于 ${fmtFullDate(v.published_at)}` : "发布时间未知"}</span>
            </div>
            <h3>{v.title.length > 160 ? v.title.slice(0, 160) + "…" : v.title}</h3>
            {v.title.length > 160 && <details className="source-description"><summary>展开完整简介</summary><p>{v.title}</p></details>}
            <div className="detail-actions">
              <a
                href={v.url}
                target="_blank"
                rel="noreferrer"
                className="button"
              >
                <ExternalLink size={14} />
                查看原视频
              </a>
              <ShortlistButton video={v} notify={notify} reload={reload} disabled={labelsDirty.current} />
            </div>
          </div>
          <div className="detail-tabs">
            {[
              ["result", "文稿与开头"],
              ["labels", "分类整理"],
              ["data", "数据快照"],
            ].map(([k, n]) => (
              <button
                className={tab === k ? "active" : ""}
                key={k}
                onClick={() => setTab(k)}
              >
                {n}
              </button>
            ))}
          </div>
          <div className="modal-content detail-content">
            {tab === "result" ? (
              <>
                {readingBusy && <div className="project-task reading-progress" role="status" aria-live="polite"><LoaderCircle className="spin" size={16}/><div><strong>{submitting ? `正在提交${submitting === 'visual_analyze' ? '画面分析' : '文稿拆解'}…` : task?.status === 'queued' ? '已提交，排队处理中…' : `${jobNames[task!.kind]} · ${task!.progress || 0}%`}</strong><small>{submitting ? '正在发送请求，请稍候。' : task?.message || '完成后会自动显示结果，可继续浏览。'}</small></div>{!submitting && task && <Button onClick={() => cancelTask(task.id)}>取消</Button>}</div>}
                {!readingBusy && latestTask && ['failed','attention','cancelled'].includes(latestTask.status) && <p className="reading-task-error" role={latestTask.status === 'cancelled' ? 'status' : 'alert'}>{latestTask.status === 'cancelled' ? '上次任务已取消。' : '上次任务未完成：'}{latestTask.message} 可以重新提交。</p>}
                {needsReading && <div className="reading-start">
                  <p className="hint">{dirty ? '先保存下方修改的文稿，再开始拆解。' : visualFirst ? "抽取开头及部分关键画面，分析画面内容和开头呈现方式。" : v.media_kind === "image" && !v.transcript ? "图文素材请先在下方粘贴正文并保存，再开始拆解。" : v.transcript ? "用已保存文稿分析开头和结构。" : "自动提取逐字稿，再由DeepSeek分析开头和结构。"}</p>
                  <Button kind="primary" disabled={readingBusy || dirty || (v.media_kind === "image" && !v.transcript)} onClick={() => void startReading(visualFirst ? "visual_analyze" : "analyze")}><Sparkles size={15}/>{readingBusy ? '处理中…' : visualFirst ? "分析画面与前5秒" : v.transcript ? "拆解文稿" : "提取文稿并拆解"}</Button>
                </div>}
                <MaterialBrief video={v} notify={notify}/>
                <details className="reading-evidence"><summary>{v.transcript ? "查看或修改文稿" : "手动粘贴文稿（可选）"}</summary>
                <div className="inline-heading">
                  <div>
                    <h3>原始逐字稿</h3>
                    <small>
                      {v.transcript
                        ? `${v.transcript.source} · ${fmtDate(v.transcript.updated_at)}`
                        : "已有文稿可以粘贴到这里；自动提取请使用上方主按钮。"}
                    </small>
                  </div>
                </div>
                <textarea
                  className="transcript-editor"
                  placeholder="粘贴你已有的逐字稿；图文素材可粘贴正文。"
                  value={text}
                  onChange={(e) => {
                    setText(e.target.value);
                    setDirty(true);
                  }}
                />
                <div className="editor-foot">
                  <span>
                    {text.length} 字 {dirty ? "· 尚未保存" : ""}
                  </span>
                  <Button
                    kind="primary"
                    disabled={readingBusy || !dirty || !text.trim()}
                    onClick={saveText}
                  >
                    <Check size={15} />
                    保存逐字稿
                  </Button>
                </div>
                {v.transcript?.segments?.length > 0 && (
                  <details className="segments">
                    <summary>
                      查看分段时间码（{v.transcript.segments.length} 段）
                    </summary>
                    {v.transcript.segments.map((s: Row, i: number) => (
                      <p key={i}>
                        <code>
                          {Math.floor(s.start)}s—{Math.floor(s.end)}s
                        </code>
                        {s.text}
                      </p>
                    ))}
                  </details>
                )}
                {v.transcript && (v.analysis || visualFirst) && <Button disabled={readingBusy || dirty} onClick={() => void startReading("analyze", !!v.analysis)}>{v.analysis ? '重新拆解文稿' : '拆解这份文稿'}</Button>}
                </details>
                {v.media_kind !== "image" && <details className="reading-evidence" open={collectionExample && !!v.visual_analysis ? true : undefined}><summary>{v.visual_analysis ? "查看画面与前5秒分析" : "画面与前5秒分析（可选）"}</summary>
                  <p className="hint">抽取开头及部分关键画面，分析构图与呈现方式，适合参考AIGC成片或视频开头。会产生模型调用费用；动作和声音仍需回原视频核对。</p>
                  {!v.visual_analysis && !(visualFirst && needsReading) && <Button disabled={readingBusy || dirty} onClick={() => void startReading("visual_analyze")}>{readingBusy ? '处理中…' : '分析画面与前5秒'}</Button>}
                  <VisualEvidence identity={id} data={v.visual_analysis}/>
                </details>}
                {v.previous_analysis && <details className="reading-evidence"><summary>查看旧版原文拆解</summary><p>{v.previous_analysis.payload?.summary}</p>{v.previous_analysis.payload?.structure?.map((s: Row, i: number) => <p key={i}>{s.role}：{s.reason}</p>)}</details>}
              </>
            ) : tab === "labels" ? (
              <div className="form-grid">
                {v.collection_group && <Field label="收藏分类">
                  <select value={edit.collection_group || ""} onChange={e => changeLabels({ ...edit, collection_group: e.target.value })}>
                    <option value="">自动分类（当前：{collectionLabel(v.collection_group)}）</option>
                    {Object.entries({ ...collectionGroups, ...(!collectionGroups[edit.collection_group] && edit.collection_group ? { [edit.collection_group]: edit.collection_group } : {}) }).map(([key, label]) => <option key={key} value={key}>{String(label)}</option>)}
                  </select>
                </Field>}
                <Field label="内容方向（可多选）"><div className="track-choices">{['AI教程','AIGC','其他'].map(t=><label key={t}><input type="checkbox" checked={edit.content_tracks?.includes(t)||false} onChange={e=>changeLabels({...edit,content_tracks:e.target.checked?[...(edit.content_tracks||[]),t]:(edit.content_tracks||[]).filter((v:string)=>v!==t)})}/>{t}</label>)}</div></Field>
                <Field label="标题">
                  <input
                    value={edit.title}
                    onChange={(e) =>
                      changeLabels({ ...edit, title: e.target.value })
                    }
                  />
                </Field>
                <Field label="主题分类">
                  <select
                    value={edit.category}
                    onChange={(e) =>
                      changeLabels({ ...edit, category: e.target.value })
                    }
                  >
                    {settings?.categories.map((c: string) => (
                      <option key={c}>{c}</option>
                    ))}
                  </select>
                </Field>
                <Field label="使用状态">
                  <select
                    value={edit.state}
                    onChange={(e) =>
                      changeLabels({ ...edit, state: e.target.value })
                    }
                  >
                    {settings?.states.map((c: string) => (
                      <option key={c}>{c}</option>
                    ))}
                  </select>
                </Field>
                <Field label="工具标签（逗号分隔）">
                  <input
                    value={edit.tools}
                    onChange={(e) =>
                      changeLabels({ ...edit, tools: e.target.value })
                    }
                  />
                </Field>
                <label className="check-line">
                  <input
                    type="checkbox"
                    checked={edit.is_ai}
                    onChange={(e) =>
                      changeLabels({ ...edit, is_ai: e.target.checked })
                    }
                  />
                  属于AI相关内容
                </label>
                <div className="full-row">
                  <strong className="field-label">素材用途</strong>
                  <div className="checkbox-tags">
                    {settings?.purposes.map((p: string) => (
                      <label key={p}>
                        <input
                          type="checkbox"
                          checked={edit.purposes?.includes(p)}
                          onChange={(e) =>
                            changeLabels({
                              ...edit,
                              purposes: e.target.checked
                                ? [...edit.purposes, p]
                                : edit.purposes.filter((v: string) => v !== p),
                            })
                          }
                        />
                        {p}
                      </label>
                    ))}
                  </div>
                </div>
                <Field label="我的备注">
                  <textarea
                    rows={3}
                    value={edit.notes}
                    onChange={(e) =>
                      changeLabels({ ...edit, notes: e.target.value })
                    }
                  />
                </Field>
                <div className="full-row detail-actions">
                  <Button
                    onClick={() => queue("classify", { ids: [id] })}
                    disabled={!!v.manual_classification}
                  >
                    <Sparkles size={14} />
                    AI初步分类
                  </Button>
                  <Button kind="primary" onClick={saveLabels}>
                    <Check size={15} />
                    保存人工分类
                  </Button>
                </div>
              </div>
            ) : (
              <>
                <Button onClick={() => queue("video_refresh", { video_id: id })}><RefreshCw size={14}/>更新数据</Button>
                <div className="data-grid">
                  {[
                    ["likes", "点赞"],
                    ["comments", "评论"],
                    ["favorites", "收藏"],
                    ["shares", "分享"],
                    ["followers", "作者当前粉丝"],
                  ].map(([k, n]) => (
                    <div key={k}>
                      <span>{n}</span>
                      <strong>{fmtNumber(v[k])}</strong>
                    </div>
                  ))}
                </div>
                <p className="muted">
                  首次发现 {fmtDate(v.discovered_at)} · 收藏时间{" "}
                  {fmtDate(v.collected_at)}。空值表示未知。
                </p>
                <details className="follower-history" open>
                  <summary>作者粉丝采样记录（最近 {v.follower_history?.length || 0} 条）</summary>
                  <p className="muted">粉丝采集于 {fmtFullDate(v.followers_at)}。保留真实采样时间；重复打开页面不重新抓取作者资料。</p>
                  {!v.follower_history?.length ? <p className="hint">暂无有效采样，可点「更新数据」获取。不会用 0 代替未知粉丝数。</p> : <div className="table-scroll"><table><thead><tr><th>粉丝采集时间</th><th>粉丝数</th><th>较上次采样</th></tr></thead><tbody>
                    {v.follower_history.map((h: Row, i: number) => { const previous = v.follower_history[i + 1]; const delta = previous ? h.followers - previous.followers : null; return <tr key={i}><td>{fmtFullDate(h.observed_at)}</td><td>{Number(h.followers).toLocaleString("zh-CN")}</td><td>{delta == null ? "首条记录" : `${delta > 0 ? "+" : ""}${Number(delta).toLocaleString("zh-CN")}`}</td></tr>; })}
                  </tbody></table></div>}
                </details>
                <details className="follower-history">
                  <summary>推荐时的粉丝记录（最近 {v.recommendation_history?.length || 0} 条）</summary>
                  <p className="muted">同一粉丝采样重复展示时复用记录，取得新采样后再记录、对比。推荐时间与粉丝采集时间分别保存；以前的推荐记录不会补造。</p>
                  <div className="table-scroll"><table><thead><tr><th>首次推荐该采样</th><th>当时已知粉丝</th><th>粉丝采集时间</th></tr></thead><tbody>
                    {v.recommendation_history?.map((h: Row, i: number) => <tr key={i}><td>{fmtFullDate(h.shown_at)}</td><td>{h.followers == null ? "未知" : Number(h.followers).toLocaleString("zh-CN")}</td><td>{fmtFullDate(h.followers_observed_at)}</td></tr>)}
                  </tbody></table></div>
                </details>
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>采集时间</th>
                        <th>点赞</th>
                        <th>评论</th>
                        <th>收藏</th>
                        <th>分享</th>
                      </tr>
                    </thead>
                    <tbody>
                      {v.snapshots.map((s: Row, i: number) => (
                        <tr key={i}>
                          <td>{fmtDate(s.sampled_at)}</td>
                          {["likes", "comments", "favorites", "shares"].map(
                            (k) => (
                              <td key={k}>{fmtNumber(s[k])}</td>
                            ),
                          )}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {v.snapshots.length < 2 && (
                  <p className="hint">有至少两次有效快照后，才显示增长速度。</p>
                )}
              </>
            )}
          </div>
        </>
      )}
    </Modal>
  );
}

function Jobs({
  close,
  refresh,
  notify,
  reload,
}: {
  close: () => void;
  refresh: number;
  notify: Notice;
  reload: () => void;
}) {
  const [items, setItems] = useState<Row[]>([]);
  const [result, setResult] = useState<Row | null>(null);
  useEffect(() => {
    const load = () =>
      api("/jobs")
        .then(setItems)
        .catch(() => {});
    load();
    const t = setInterval(load, 2500);
    return () => clearInterval(t);
  }, [refresh]);
  async function action(id: string, type: string) {
    try {
      const response = await api("/jobs/" + id + "/" + type, "POST", {});
      notify(type === "retry" ? "已重新排队" : response.message || "已取消；在途请求可能仍需收尾",false,"jobs");
      reload();
      setItems(await api("/jobs"));
    } catch (e) {
      notify((e as Error).message, true,"jobs");
    }
  }
  return (
    <Modal
      title="任务中心"
      subtitle="耗时任务在电脑后台执行，关闭面板后仍会继续"
      close={close}
    >
      <div className="modal-content">
        {!items.length ? (
          <Empty
            icon={<Activity size={26} />}
            title="当前没有任务"
            text="同步、转写和文案生成的进度都会显示在这里。"
          />
        ) : (
          items.map((j) => (
            <div className="job-card" key={j.id}>
              <header>
                <strong>{jobNames[j.kind] || j.kind}</strong>
                <span className={"job-state " + j.status}>
                  {jobStates[j.status]}
                </span>
              </header>
              <p>{j.message || "等待执行"}</p>
              {["running", "queued"].includes(j.status) && (
                <div className="progress">
                  <i style={{ width: j.progress + "%" }} />
                </div>
              )}
              <footer>
                <small>{fmtDate(j.created_at)}</small>
                {["failed", "attention", "cancelled"].includes(j.status) && (
                  <Button onClick={() => action(j.id, "retry")}>
                    {j.kind === "search" && j.result?.failed_keywords?.length ? "仅重试失败项" : "重试"}
                  </Button>
                )}
                {["running", "queued"].includes(j.status) && (
                  <Button onClick={() => action(j.id, "cancel")}>
                    取消任务
                  </Button>
                )}
                {j.result && (
                  <Button
                    onClick={async () => setResult(await api("/jobs/" + j.id))}
                  >
                    查看结果
                  </Button>
                )}
              </footer>
            </div>
          ))
        )}
        {result && (
          <section className="job-result">
            <div className="inline-heading">
              <h3>任务结果</h3>
              <button
                className="icon-button"
                onClick={() => setResult(null)}
                aria-label="关闭结果"
              >
                <X size={16} />
              </button>
            </div>
            {result.result?.conflict && (
              <p className="hint">
                生成期间项目已更新。以下新生成内容已保留，未覆盖你的稿件。
              </p>
            )}
            <pre>{JSON.stringify(result.result, null, 2)}</pre>
            <Button
              onClick={() => {
                navigator.clipboard?.writeText(
                  JSON.stringify(result.result, null, 2),
                );
                notify("已复制结果");
              }}
            >
              <Copy size={14} />
              复制结果
            </Button>
          </section>
        )}
      </div>
    </Modal>
  );
}

function Settings({
  initialSection,
  settings,
  overview,
  reload,
  notify,
  queue,
  showJobs,
}: {
  initialSection: string;
  settings: Row | null;
  overview: Row;
  reload: () => void;
  notify: Notice;
  queue: (kind: string, payload?: Row) => void;
  showJobs: () => void;
}) {
  const [active, setActive] = useState(initialSection);
  useEffect(() => setActive(initialSection), [initialSection]);
  const [form, setForm] = useState<Row | null>(null);
  const [creators, setCreators] = useState<Row[]>([]);
  const [backups, setBackups] = useState<Row[]>([]);
  const [system, setSystem] = useState<Row | null>(null);
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [keyName, setKeyName] = useState<string | null>(null);
  const [edgeConnect, setEdgeConnect] = useState(false);
  const [edgeRelay, setEdgeRelay] = useState(false);
  useEffect(() => {
    const receive = (event: MessageEvent) => {
      if (event.source !== window || event.origin !== location.origin || event.data?.source !== "AIWorkbenchEdge") return;
      if (event.data.type === "ready") setEdgeRelay(true);
      if (event.data.type === "paired") {
        if (event.data.ok) { setEdgeConnect(false); reload(); notify("已配对当前浏览器，抖音登录保持原样。"); }
        else notify(event.data.message || "配对未完成", true);
      }
    };
    window.addEventListener("message", receive);
    window.postMessage({source:"AIWorkbenchApp",type:"probe"},location.origin);
    return () => window.removeEventListener("message",receive);
  },[reload,notify]);
  const [key, setKey] = useState("");
  const [restoreName, setRestoreName] = useState<string | null>(null);
  const [restoreConfirm, setRestoreConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const loaded = useRef(false);
  useEffect(() => {
    if (settings && !loaded.current) {
      setForm(structuredClone(settings));
      loaded.current = true;
    }
  }, [settings]);
  const load = () =>
    Promise.all([
      api("/creators").then(setCreators),
      api("/backups").then(setBackups),
      api("/system").then(setSystem),
    ]).catch((e) => notify(e.message, true));
  useEffect(() => {
    load();
  }, []);
  async function save(changes: Row) {
    setBusy(true);
    try {
      await api("/settings", "PATCH", changes);
      notify("设置已保存");
      reload();
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  }
  async function addCreator(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api("/creators", "POST", { url, title: name });
      setUrl("");
      setName("");
      load();
      reload();
      notify("已添加对标博主");
    } catch (e) {
      notify((e as Error).message, true);
    }
  }
  async function storeKey(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api("/secrets/" + keyName, "PUT", { value: key });
      notify("密钥已加密保存在这台电脑");
      setKey("");
      setKeyName(null);
      reload();
    } catch (e) {
      notify((e as Error).message, true);
    }
  }
  async function restore() {
    try {
      await api("/backups/restore", "POST", {
        name: restoreName,
        confirm: restoreConfirm,
      });
      notify("备份已恢复，请重新登录");
      setTimeout(() => location.reload(), 1000);
    } catch (e) {
      notify((e as Error).message, true);
    }
  }
  if (!form)
    return (
      <div className="loading-block">
        <LoaderCircle className="spin" />
      </div>
    );
  const sections = [
    { id: "overview", name: "运行状态", icon: Activity },
    { id: "connections", name: "连接与模型", icon: Link2 },
    { id: "creators", name: "对标博主", icon: Users },
    { id: "profile", name: "内容方向与关键词", icon: SlidersHorizontal },
    { id: "automation", name: "自动任务", icon: Clock3 },
    { id: "budget", name: "API与预算", icon: Activity },
    { id: "devices", name: "手机与设备", icon: Smartphone },
    { id: "backups", name: "数据与备份", icon: FolderOpen },
  ];
  return (
    <>
      <section className="page-heading">
        <div>
          <div className="eyebrow">YOUR WORKSPACE, YOUR RULES</div>
          <h1>
            {active==='creators'?'对标账号':active==='profile'?'内容方向与关键词':'设置与运行状态'}
            <span className="heading-dot" />
          </h1>
          <p>{initialSection==='creators'?'跟踪值得持续关注的作者，让工作台帮你检查新作品。':'日常先看运行状态，需要调整时再进入对应设置。'}</p>
        </div>
        <Button onClick={showJobs}>
          <Activity size={16} />
          查看任务
        </Button>
      </section>
      {active!=="profile"&&<div className="settings-shortcuts"><Button onClick={() => setActive("profile")}><Search size={15}/>编辑发现关键词</Button><span className="muted">搜索词、内容方向和筛选条件统一在这里修改。</span></div>}
      <div className="settings-layout">
        <nav className="settings-nav">
          {sections.map((s) => (
            <button
              key={s.id}
              className={active === s.id ? "active" : ""}
              onClick={() => setActive(s.id)}
            >
              <s.icon size={16} />
              {s.name}
              <ChevronRight size={14} />
            </button>
          ))}
        </nav>
        <section className="settings-panel">
          <div className="settings-panel-heading">
            <h2>{sections.find((s) => s.id === active)?.name}</h2>
            <p>
              {active === "overview" ? "同步按既定计划运行，手动操作可在任务中心查看和取消。" : active === "connections"
                ? "先连接必要服务，再逐步开启自动化。"
                : active === "creators"
                  ? "维护你真正想研究的账号，建议先从10—30个开始。"
                  : active === "devices"
                    ? "手机和电脑访问同一份数据。电脑关机或休眠时，手机暂时无法连接。"
                    : "修改后保存，即可应用到后续任务。"}
            </p>
          </div>
          {active === "overview" ? (
            <div className="run-overview">
              <section><h3>我的收藏</h3><p>{settings?.edge_bridge?.paired ? "采集浏览器已配对" : "尚未连接浏览器"} · 自动任务{settings?.automation?.enabled ? `每${settings.automation.favorites_minutes / 60}小时检查` : "已暂停"}</p><small>最近同步：{fmtFullDate(overview.last_sync?.favorites)}</small><Button onClick={() => setActive("connections")}>查看连接</Button></section>
              <section><h3>选题发现</h3><p>{overview.counts.creators || 0}位对标 · {settings?.automation?.enabled ? `对标每${settings.automation.creator_minutes / 60}小时检查，关键词每天一轮` : "自动任务已暂停"}</p><small>对标更新：{fmtFullDate(overview.last_sync?.creators)}</small><Button onClick={() => setActive("automation")}>调整更新频率</Button></section>
              <section><h3>按需拆解与转写</h3><p>DeepSeek {overview.configured.deepseek ? "已配置" : "待配置"} · {settings?.model}</p><small>DeepSeek仅按需分类与拆解；逐字稿由字幕或本机转写取得。</small><Button onClick={() => setActive("connections")}>查看模型设置</Button></section>
              <section><h3>任务与费用</h3><p>{overview.jobs.filter((j: Row) => ["queued", "running"].includes(j.status)).length}个任务等待或运行中</p><small>已有素材可以直接查看，生成和更新进度在任务中心查看。</small><div className="card-buttons"><Button onClick={showJobs}>任务中心</Button><Button onClick={() => setActive("budget")}>费用明细</Button></div></section>
            </div>
          ) : active === "connections" ? (
            <>
              <div className="connection-card">
                <div className="connection-icon">
                  <Bookmark size={22} />
                </div>
                <div>
                  <h3>
                    抖音私人收藏
                    <Tag
                      green={settings?.edge_bridge?.paired || overview.collection_state?.status === "existing_browser_readable" || overview.collection_state?.status === "authenticated"}
                    >
                      {settings?.edge_bridge?.paired ? "浏览器已配对" : overview.collection_state?.status === "existing_browser_readable" ? "页面已验证" : "待连接浏览器"}
                    </Tag>
                  </h3>
                  <p>
                    {overview.collection_state?.message ||
                      "选择你已登录抖音的浏览器（Chrome、Edge等），连接本地扩展后同步收藏。"}
                  </p>
                  <div className="connection-actions">
                    <Button
                      kind="primary"
                      onClick={() => setEdgeConnect(true)}
                    >
                      连接现有浏览器
                      <ArrowUpRight size={14} />
                    </Button>
                    <Button disabled={!settings?.edge_bridge?.paired} onClick={() => queue("favorites_probe")}>
                      验证收藏读取
                    </Button>
                    <Button disabled={!settings?.edge_bridge?.paired} onClick={() => queue("favorites_sync", {foreground:true})}>
                      前台同步收藏
                    </Button>
                    {settings?.edge_bridge?.paired && <Button kind="subtle" onClick={async () => {
                      try { await api("/connections/edge", "DELETE"); reload(); notify("已解除浏览器连接，现有抖音登录保留。"); }
                      catch(e) { notify((e as Error).message,true); }
                    }}>解除连接</Button>}
                  </div>
                </div>
              </div>
              {[
                {
                  id: "tikhub",
                  name: "TikHub · 公开视频数据",
                  text: "发现关键词热点，读取对标作品和互动数据。",
                  icon: Flame,
                },
                {
                  id: "deepseek",
                  name: "DeepSeek · 创作分析",
                  text: "分类、结构拆解、前5秒和正文生成。",
                  icon: Sparkles,
                },
              ].map((c) => (
                <div className="connection-card" key={c.id}>
                  <div className="connection-icon">
                    <c.icon size={22} />
                  </div>
                  <div>
                    <h3>
                      {c.name}
                      <Tag green={overview.configured[c.id]}>
                        {overview.configured[c.id] ? c.id === "tikhub" && settings?.data_connection_state?.status === "verified" ? "接口已验证" : "密钥已配置" : "待配置"}
                      </Tag>
                    </h3>
                    <p>{c.text}</p>
                    {c.id === "tikhub" && settings?.data_connection_state?.status === "verified" && <p className="muted">视频详情、作者资料、作品列表和关键词搜索均已通过真实请求验证。</p>}
                    <div className="connection-actions">
                      <Button
                        onClick={() => {
                          setKeyName(c.id);
                          setKey("");
                        }}
                      >
                        {overview.configured[c.id] ? "更换密钥" : "配置API密钥"}
                      </Button>
                      {c.id === "deepseek" && (
                        <Button
                          onClick={async () => {
                            try {
                              const r = await api(
                                "/connections/deepseek/test",
                                "POST",
                                {},
                              );
                              setModels(r.models);
                              notify("连接成功，可用模型已更新");
                            } catch (e) {
                              notify((e as Error).message, true);
                            }
                          }}
                          disabled={!overview.configured.deepseek}
                        >
                          验证连接与模型
                        </Button>
                      )}
                      {c.id === "tikhub" && (
                        <a
                          href="https://user.tikhub.io/"
                          target="_blank"
                          rel="noreferrer"
                          className="text-link"
                        >
                          服务控制台
                          <ExternalLink size={12} />
                        </a>
                      )}
                    </div>
                  </div>
                </div>
              ))}
              <div className="settings-subsection">
                <h3>文案模型</h3>
                <Field
                  label="模型ID"
                  help="默认自动跟随 DeepSeek Flash 官方最新版。2026-11-02已核实对应 V4.1 Flash 1017。拆解、开头和脚本使用深度思考；分类使用快速模式。"
                >
                  <select
                    aria-label="文案模型"
                    value={form.model}
                    onChange={(e) =>
                      setForm({ ...form, model: e.target.value })
                    }
                  ><option value="deepseek-flash">DeepSeek Flash · 自动跟随最新版（推荐）</option>{Array.from(new Set([form.model, ...models])).filter(m => m && m !== 'deepseek-flash').map(m => <option key={m} value={m}>{m}</option>)}</select>
                </Field>
                <Button
                  disabled={busy}
                  onClick={() => save({ model: form.model })}
                >
                  保存模型
                </Button>
              </div>
              <div className="settings-subsection">
                <h3>本机转写</h3>
                <p className="muted">
                  优先读取可用字幕，否则用Whisper提取口播。首次使用需要下载模型。
                </p>
                <div className="form-grid">
                  <Field label="Whisper模型">
                    <select
                      value={form.whisper_model}
                      onChange={(e) =>
                        setForm({ ...form, whisper_model: e.target.value })
                      }
                    >
                      {[
                        "tiny",
                        "base",
                        "small",
                        "medium",
                        "large-v3",
                        "turbo",
                      ].map((m) => (
                        <option key={m}>{m}</option>
                      ))}
                    </select>
                  </Field>
                  <Field label="计算设备">
                    <select
                      value={form.whisper_device}
                      onChange={(e) =>
                        setForm({ ...form, whisper_device: e.target.value })
                      }
                    >
                      <option value="auto">自动选择GPU / CPU</option>
                      <option value="cuda">GPU（CUDA）</option>
                      <option value="cpu">CPU</option>
                    </select>
                  </Field>
                </div>
                <label className="check-line">
                  <input
                    type="checkbox"
                    checked={form.transcription_download_approved}
                    onChange={(e) =>
                      setForm({
                        ...form,
                        transcription_download_approved: e.target.checked,
                      })
                    }
                  />
                  允许首次转写时下载本地Whisper模型
                </label>
                <Button
                  onClick={() =>
                    save({
                      whisper_model: form.whisper_model,
                      whisper_device: form.whisper_device,
                      transcription_download_approved:
                        form.transcription_download_approved,
                    })
                  }
                >
                  保存转写设置
                </Button>
              </div>
            </>
          ) : active === "creators" ? (
            <>
              <form className="creator-add" onSubmit={addCreator}>
                <Field label="博主主页分享链接">
                  <input
                    required
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    placeholder="https://www.douyin.com/user/…"
                  />
                </Field>
                <Field label="显示名称（可选）">
                  <input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="博主名称"
                  />
                </Field>
                <Button kind="primary" type="submit">
                  <Plus size={15} />
                  添加博主
                </Button>
              </form>
              <div className="inline-heading">
                <h3>{creators.filter((c) => c.enabled).length} 个账号监控中</h3>
                <Button onClick={() => queue("creators_sync")}>
                  <RefreshCw size={14} />
                  更新作品
                </Button>
              </div>
              {!creators.length ? (
                <Empty
                  icon={<Users size={26} />}
                  title="添加你的第一个对标"
                  text="只监控你指定的名单，不会自动把全部抖音关注账号纳入。"
                />
              ) : (
                creators.map((c) => (
                  <div className="creator-row" key={c.id}>
                    <div className="creator-avatar">{c.name[0]}</div>
                    <div>
                      <strong>{c.name}</strong>
                      <p>
                        {fmtNumber(c.followers)} 粉丝 · {fmtDate(c.updated_at)}
                      </p>
                    </div>
                    <a
                      href={c.url}
                      target="_blank"
                      rel="noreferrer"
                      className="icon-button"
                      aria-label="查看博主"
                    >
                      <ExternalLink size={15} />
                    </a>
                    <label className="switch">
                      <input
                        type="checkbox"
                        checked={!!c.enabled}
                        onChange={async (e) => {
                          await api("/creators/" + c.id, "PATCH", {
                            enabled: e.target.checked,
                          });
                          load();
                          reload();
                        }}
                      />
                      <span />
                    </label>
                  </div>
                ))
              )}
            </>
          ) : active === "profile" ? (
            <>
              <details className="reading-evidence" open={!!form.creator_domain}>
                <summary>行业与赛道匹配</summary>
                <p className="muted">安装时可让Agent根据访谈填写。匹配词只在本地筛选，不产生搜索费用；低粉爆款始终同时应用互动门槛。</p>
                <Field label="行业 / 赛道"><input value={form.creator_domain?.name || ''} onChange={e=>setForm({...form,creator_domain:{...form.creator_domain,name:e.target.value}})}/></Field>
                <Field label="领域匹配词（每行一个）"><textarea rows={4} value={(form.creator_domain?.include_keywords || []).join('\n')} onChange={e=>setForm({...form,creator_domain:{...form.creator_domain,include_keywords:e.target.value.split('\n')}})}/></Field>
                <Field label="不想看的内容词（每行一个）"><textarea rows={3} value={(form.creator_domain?.exclude_keywords || []).join('\n')} onChange={e=>setForm({...form,creator_domain:{...form.creator_domain,exclude_keywords:e.target.value.split('\n')}})}/></Field>
              </details>
              <Field
                label="内容方向"
                help="告诉AI你做什么、给谁看，用于分类适配判断与文稿拆解。实际搜索由「发现关键词」控制，这里不会自动扩展搜索词。"
              >
                <textarea
                  rows={6}
                  value={form.profile}
                  onChange={(e) =>
                    setForm({ ...form, profile: e.target.value })
                  }
                />
              </Field>
              <Field label="发现关键词（每行一个）" help="实际交给TikHub搜索的词，决定能发现哪些作品。无需添加对标或连接抖音账号；素材分类标签不会自动变成搜索词。">
                <textarea rows={7} value={form.keywords.join("\n")} onChange={e=>setForm({...form,keywords:e.target.value.split("\n")})}/>
              </Field>
              <details className="reading-evidence"><summary>发布话题标签（{form.traffic_tags?.length || 0}）</summary><p className="muted">由Agent按行业整理并注明来源；与发现关键词分开保存，不会自动搜索、投稿或报名。活动有效期与流量资格需在发布前核实。</p>{(form.traffic_tags||[]).map((item:Row)=><p key={item.platform+item.tag}><b>#{item.tag}</b> · {item.platform} · {item.status}<br/><small>{item.purpose} · 核对时间 {fmtFullDate(item.checked_at)}{item.valid_until?` · 截止 ${item.valid_until}`:''}</small><br/><a href={item.source_url} target="_blank" rel="noopener noreferrer">查看来源</a></p>)}</details>
              <details className="reading-evidence"><summary>高级：素材分类标签</summary><p className="muted">用于整理收藏和分析结果，例如教程方法、案例效果。它们是归档标签，不控制TikHub搜索，通常保留默认即可。</p><Field label="素材分类标签（每行一项）">
                <textarea
                  rows={8}
                  value={form.categories.join("\n")}
                  onChange={(e) =>
                    setForm({ ...form, categories: e.target.value.split("\n") })
                  }
                />
              </Field></details>
              <Button
                kind="primary"
                onClick={() =>
                  save({
                    profile: form.profile,
                    ...(form.creator_domain ? {creator_domain:{configured:true,name:form.creator_domain.name,include_keywords:(form.creator_domain.include_keywords||[]).map((v:string)=>v.trim()).filter(Boolean),exclude_keywords:(form.creator_domain.exclude_keywords||[]).map((v:string)=>v.trim()).filter(Boolean),prefer_low_followers:true}} : {}),
                    keywords: form.keywords.map((s: string)=>s.trim()).filter(Boolean),
                    categories: form.categories
                      .map((s: string) => s.trim())
                      .filter(Boolean),
                  })
                }
              >
                保存内容方向与关键词
              </Button>
              <p className="muted">保存后，返回「热点推荐」点「更新热点推荐」，或到「低粉爆款」点「更新低粉爆款」。保存本身不发起付费搜索。</p>
              <div className="settings-subsection">
                <h3>低粉爆款筛选</h3>
                <p className="muted">
                  以下为筛选默认值，不代表行业标准。使用作者当前粉丝数；未知时不入榜。
                </p>
                <div className="form-grid">
                  <Field label="最高粉丝数">
                    <input
                      type="number"
                      min="1"
                      value={form.low_followers}
                      onChange={(e) =>
                        setForm({
                          ...form,
                          low_followers: Number(e.target.value),
                        })
                      }
                    />
                  </Field>
                  <Field label="最低点赞数">
                    <input
                      type="number"
                      min="1"
                      value={form.low_min_likes}
                      onChange={(e) =>
                        setForm({
                          ...form,
                          low_min_likes: Number(e.target.value),
                        })
                      }
                    />
                  </Field>
                  <Field label="最低点赞 / 粉丝比（%）" help="点赞数÷作者最近采样粉丝数。例如1000赞÷5000粉=20%。阈值可以自行调整，不是抖音官方标准，也不是粉丝真实点赞率。">
                    <input
                      type="number"
                      min="0.1"
                      step="0.1"
                      value={Math.round(form.low_ratio * 10000) / 100}
                      onChange={(e) =>
                        setForm({ ...form, low_ratio: Number(e.target.value) / 100 })
                      }
                    />
                  </Field>
                </div>
                <Button
                  onClick={() =>
                    save({
                      low_followers: form.low_followers,
                      low_min_likes: form.low_min_likes,
                      low_ratio: form.low_ratio,
                    })
                  }
                >
                  保存筛选规则
                </Button>
              </div>
              <div className="settings-subsection">
                <h3>热点推荐</h3>
                <p className="muted">筛选近期高赞作品，不限制作者粉丝数。每轮最多读取1次热榜、搜索5个词，每词按最多点赞与最新发布各查1页；最多11次数据请求，热榜AI选词最多1次模型调用。不会更改你保存的发现关键词。</p>
                <label className="check-line"><input type="checkbox" checked={!!form.hotspots?.enabled} onChange={e=>setForm({...form,hotspots:{...form.hotspots,enabled:e.target.checked}})}/>每天北京时间08:40更新热点推荐（需启用自动任务，错过则上线补跑）</label>
                <Field label="热点推荐默认最低点赞数"><input type="number" min="1" max="10000000" value={form.hotspots?.min_likes ?? 10000} onChange={e=>setForm({...form,hotspots:{...form.hotspots,min_likes:Number(e.target.value)}})}/></Field>
                <p className="muted">按当前保存单价，一轮数据调用预估上限约¥{((form.pricing.search_request_usd*10+form.pricing.data_request_usd)*form.usd_cny).toFixed(3)}，实际按请求计费；模型选词另计，均受现有预算限制。手动更新每10分钟最多一轮。</p>
                <Button onClick={()=>save({hotspots:form.hotspots})}>保存热点推荐设置</Button>
              </div>
            </>
          ) : active === "automation" ? (
            <>
              <div className="setting-toggle">
                <div>
                  <h3>启用自动任务</h3>
                  <p>
                    仅在电脑开机、工作台运行时执行。收藏默认间隔 24 小时；手动同步不受该间隔限制，并推迟下一次自动同步。
                  </p>
                </div>
                <label className="switch">
                  <input
                    type="checkbox"
                    checked={form.automation.enabled}
                    onChange={(e) =>
                      setForm({
                        ...form,
                        automation: {
                          ...form.automation,
                          enabled: e.target.checked,
                        },
                      })
                    }
                  />
                  <span />
                </label>
              </div>
              <div className="form-grid">
                <Field label="收藏同步间隔">
                  <select
                    value={form.automation.favorites_minutes}
                    onChange={(e) =>
                      setForm({
                        ...form,
                        automation: {
                          ...form.automation,
                          favorites_minutes: Number(e.target.value),
                        },
                      })
                    }
                  >
                    {[240, 480, 720, 1440].map((n) => (
                      <option key={n} value={n}>
                        {n / 60} 小时
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="对标更新间隔（08:00—24:00）">
                  <select
                    value={form.automation.creator_minutes}
                    onChange={(e) =>
                      setForm({
                        ...form,
                        automation: {
                          ...form.automation,
                          creator_minutes: Number(e.target.value),
                        },
                      })
                    }
                  >
                    {[240, 480, 720, 1440].map((n) => (
                      <option key={n} value={n}>
                        {n / 60} 小时
                      </option>
                    ))}
                  </select>
                </Field>
              </div>
              <div className="schedule-summary">
                <div>
                  <Search size={17} />
                  <span>关键词发现</span>
                  <strong>每天 08:30 一轮</strong>
                </div>
                <div>
                  <FolderOpen size={17} />
                  <span>数据库备份</span>
                  <strong>每日首次运行自动创建</strong>
                </div>
                <div>
                  <Clock3 size={17} />
                  <span>时区</span>
                  <strong>北京时间 UTC+8</strong>
                </div>
              </div>
              <p className="hint">关键词发现每天一轮，错过后在电脑上线时补跑当天一轮，也可手动触发。HTTP 400不自动重试；服务端临时故障每个关键词最多重试一次，全轮最多增加2次请求。</p>
              <p className="hint">收藏读取使用你的Edge登录态：自动间隔至少4小时；手动读取之间设10分钟保护间隔；历史收藏仅手动补齐，出现验证即暂停。这些措施减少访问量，不能保证平台零风控。对标、搜索和粉丝资料通过TikHub获取。</p>
              <Button
                kind="primary"
                onClick={() => save({ automation: form.automation })}
              >
                保存任务安排
              </Button>
            </>
          ) : active === "budget" ? (
            <>
              <div className="budget-overview">
                <div>
                  <span>本月预估用量</span>
                  <strong>
                    ¥
                    {overview.expenses
                      .reduce((n: number, e: Row) => n + e.used, 0)
                      .toFixed(2)}
                  </strong>
                </div>
                <span>数据服务与 DeepSeek 合计，按配置单价预估，并非充值余额或服务商实际账单。</span>
              </div>
              <div className="cost-breakdown">
                {([['data', 'TikHub 数据服务'], ['model', 'DeepSeek 文案模型']] as const).map(([provider, name]) => {
                  const expense = overview.expenses.find((e: Row) => e.provider === provider);
                  return <div key={provider}><span>{name}</span><strong>¥{Number(expense?.used || 0).toFixed(4)}</strong><small>{expense?.calls || 0} 次请求 · 含待确认预留 ¥{Number(expense?.pending || 0).toFixed(4)}</small></div>;
                })}
                <p className="muted">DeepSeek 用于分类、账号适配与可执行性判断、逐字稿拆解、前5秒开头和脚本生成。浏览、筛选、手动编辑不调用模型；本机 Whisper 转写不扣 DeepSeek 费用。费用未知时保留预留额度，最终以两家服务商账单为准。</p>
              </div>
              <div className="form-grid">
                {[
                  ["total", "每月总上限"],
                  ["data", "公开数据额度"],
                  ["model", "文案模型额度"],
                  ["reserve", "预留额度"],
                ].map(([k, n]) => (
                  <Field key={k} label={n + "（元）"}>
                    <input
                      type="number"
                      min="1"
                      value={form.budgets[k]}
                      onChange={(e) =>
                        setForm({
                          ...form,
                          budgets: {
                            ...form.budgets,
                            [k]: Number(e.target.value),
                          },
                        })
                      }
                    />
                  </Field>
                ))}
              </div>
              <div className="settings-subsection">
                <h3>调用单价 · 接入前核对</h3>
                <p className="muted">
                  这里采用保守预估。各接口价格不同，实际账单以服务商为准。搜索按每次请求计费。
                </p>
                <div className="form-grid">
                  {[
                    ["data_request_usd", "普通数据请求（美元/次）"],
                    ["search_request_usd", "搜索请求（美元/次）"],
                    ["model_input_cny_million", "模型输入（元/百万Token）"],
                    ["model_output_cny_million", "模型输出（元/百万Token）"],
                  ].map(([k, n]) => (
                    <Field key={k} label={n}>
                      <input
                        type="number"
                        min="0.000001"
                        step="0.001"
                        value={form.pricing[k]}
                        onChange={(e) =>
                          setForm({
                            ...form,
                            pricing: {
                              ...form.pricing,
                              [k]: Number(e.target.value),
                            },
                          })
                        }
                      />
                    </Field>
                  ))}
                  <Field
                    label="预算换算参考汇率（人民币/美元）"
                    help="仅用于保守估算，不代表实时汇率。"
                  >
                    <input
                      type="number"
                      min="1"
                      step="0.1"
                      value={form.usd_cny}
                      onChange={(e) =>
                        setForm({ ...form, usd_cny: Number(e.target.value) })
                      }
                    />
                  </Field>
                </div>
                <div className="connection-actions">
                  <a
                    href="https://tikhub.io/pricing"
                    target="_blank"
                    rel="noreferrer"
                    className="text-link"
                  >
                    TikHub计费
                    <ExternalLink size={12} />
                  </a>
                  <a
                    href="https://api-docs.deepseek.com/quick_start/pricing/"
                    target="_blank"
                    rel="noreferrer"
                    className="text-link"
                  >
                    DeepSeek计费
                    <ExternalLink size={12} />
                  </a>
                </div>
                <label className="check-line">
                  <input
                    type="checkbox"
                    checked={form.pricing.confirmed}
                    onChange={(e) =>
                      setForm({
                        ...form,
                        pricing: {
                          ...form.pricing,
                          confirmed: e.target.checked,
                        },
                      })
                    }
                  />
                  文案模型单价已核对，允许在上述预算内调用
                </label>
                <label className="check-line">
                  <input
                    type="checkbox"
                    checked={!!form.data_pricing_confirmed}
                    onChange={(e) =>
                      setForm({
                        ...form,
                        data_pricing_confirmed: e.target.checked,
                      })
                    }
                  />
                  已在TikHub账户Marketplace核对普通接口单价，允许数据调用
                </label>
                <Button
                  kind="primary"
                  onClick={() =>
                    save({
                      budgets: form.budgets,
                      pricing: form.pricing,
                      usd_cny: form.usd_cny,
                      data_pricing_confirmed: form.data_pricing_confirmed,
                    })
                  }
                >
                  保存预算与单价
                </Button>
              </div>
            </>
          ) : active === "devices" ? (
            <>
              <PhoneAccess notify={notify}/>
              <details className="reading-evidence"><summary>Tailscale私有连接（原有方式）</summary>
              <div className="device-illustration">
                <Laptop size={35} />
                <span className="connection-line" />
                <ShieldCheck size={24} />
                <span className="connection-line" />
                <Smartphone size={32} />
              </div>
              <div className="system-checks">
                <div>
                  <CheckCircle2 size={17} />
                  工作台数据库<strong>本机存储</strong>
                </div>
                <div>
                  {system?.tailscale_installed ? (
                    <CheckCircle2 size={17} />
                  ) : (
                    <AlertCircle size={17} />
                  )}
                  Tailscale
                  <strong>
                    {system?.tailscale_status === "Running"
                      ? "已连接"
                      : system?.tailscale_installed
                        ? "已安装，待连接"
                        : "待安装"}
                  </strong>
                </div>
                <div>
                  {system?.ffmpeg ? (
                    <CheckCircle2 size={17} />
                  ) : (
                    <AlertCircle size={17} />
                  )}
                  FFmpeg<strong>{system?.ffmpeg ? "可用" : "待安装"}</strong>
                </div>
                <div>
                  {system?.whisper ? (
                    <CheckCircle2 size={17} />
                  ) : (
                    <AlertCircle size={17} />
                  )}
                  本机转写库
                  <strong>{system?.whisper ? "可用" : "待安装"}</strong>
                </div>
              </div>
              <ol className="device-steps">
                <li>电脑和手机安装 Tailscale，并登录自己的账户。</li>
                <li>
                  在电脑运行「配置手机访问.ps1」，指定手机设备并验证白名单。
                </li>
                <li>用手机浏览器打开私有 HTTPS 地址，再添加到主屏幕。</li>
              </ol>
              {system?.computer_name && (
                <p className="hint">电脑私有域名：{system.computer_name}</p>
              )}
              <Field
                label="私有工作台地址"
                help="仅支持 https://电脑名.网络名.ts.net；请完成Serve和访问规则后保存。"
              >
                <input
                  placeholder="https://your-pc.your-tailnet.ts.net"
                  value={form.remote_access.url}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      remote_access: {
                        ...form.remote_access,
                        url: e.target.value,
                      },
                    })
                  }
                />
              </Field>
              <Field label="获准手机的设备名称">
                <input
                  value={form.remote_access.allowed_device}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      remote_access: {
                        ...form.remote_access,
                        allowed_device: e.target.value,
                      },
                    })
                  }
                />
              </Field>
              <div className="connection-actions">
                <Button
                  onClick={() => save({ remote_access: form.remote_access })}
                >
                  保存设备记录
                </Button>
                <Button onClick={() => load()}>
                  <RefreshCw size={14} />
                  重新检查
                </Button>
              </div>
              <p className="hint">
                这里记录访问地址；真正的设备权限由 Tailscale
                规则控制。保存记录不会自动放行设备。
              </p>
              </details>
            </>
          ) : (
            <>
              <div className="storage-path">
                <FolderOpen size={19} />
                <div>
                  <span>本机数据目录</span>
                  <code>{settings?.data_directory}</code>
                </div>
              </div>
              <p className="muted">
                API密钥使用Windows用户加密。抖音登录由Edge管理，不放入工作台备份；更换电脑后重新配置密钥并连接已登录的Edge。
              </p>
              <div className="connection-actions">
                <Button kind="primary" onClick={() => queue("backup")}>
                  <FolderOpen size={15} />
                  创建一致性备份
                </Button>
                <Button onClick={() => load()}>
                  <RefreshCw size={14} />
                  刷新列表
                </Button>
                <a href="/api/export" className="button">
                  <Download size={15} />
                  导出素材与项目JSON
                </a>
              </div>
              <div className="backup-list">
                {!backups.length ? (
                  <p className="muted">
                    暂时没有备份。完成初始设置后会每日自动创建，也可以立即手动备份。
                  </p>
                ) : (
                  backups.map((b) => (
                    <div key={b.name}>
                      <div>
                        <strong>{b.name}</strong>
                        <small>{(b.size / 1024).toFixed(0)} KB</small>
                      </div>
                      <a
                        className="icon-button"
                        aria-label="下载备份"
                        href={"/api/backups/" + b.name}
                      >
                        <Download size={16} />
                      </a>
                      <Button
                        onClick={() => {
                          setRestoreName(b.name);
                          setRestoreConfirm("");
                        }}
                      >
                        恢复
                      </Button>
                    </div>
                  ))
                )}
              </div>
              <div className="settings-subsection">
                <Button
                  onClick={async () => {
                    await api("/auth/logout", "POST", {});
                    location.reload();
                  }}
                >
                  <LogOut size={15} />
                  退出工作台
                </Button>
              </div>
            </>
          )}
        </section>
      </div>
      {edgeConnect && <Modal title="连接你现有的浏览器" subtitle="先选择抖音已登录的浏览器，再配对收藏连接" close={() => setEdgeConnect(false)}>
        <BrowserConnection ready={edgeRelay} directory={settings?.edge_extension_directory} paired={!!settings?.edge_bridge?.paired} notify={notify}/>
      </Modal>}
      {keyName && (
        <Modal
          title={
            "配置 " +
            (keyName === "tikhub" ? "TikHub" : "DeepSeek") +
            " API密钥"
          }
          subtitle="密钥仅发送到本机后端并加密保存，不回传给页面"
          close={() => {
            setKeyName(null);
            setKey("");
          }}
        >
          <form onSubmit={storeKey} className="modal-content">
            <Field label="API Key">
              <input
                type="password"
                autoComplete="off"
                required
                value={key}
                onChange={(e) => setKey(e.target.value)}
                placeholder="粘贴服务商提供的API密钥"
              />
            </Field>
            <Button kind="primary full" type="submit">
              <LockKeyhole size={15} />
              加密保存到电脑
            </Button>
          </form>
        </Modal>
      )}
      {restoreName && (
        <Modal
          title="恢复数据库备份"
          subtitle="当前数据库会先自动备份。恢复后需要重新登录。"
          close={() => setRestoreName(null)}
        >
          <div className="modal-content">
            <p>{restoreName}</p>
            <Field label="输入「恢复备份」确认">
              <input
                value={restoreConfirm}
                onChange={(e) => setRestoreConfirm(e.target.value)}
              />
            </Field>
            <Button
              kind="primary"
              disabled={restoreConfirm !== "恢复备份"}
              onClick={restore}
            >
              恢复此备份
            </Button>
          </div>
        </Modal>
      )}
    </>
  );
}
