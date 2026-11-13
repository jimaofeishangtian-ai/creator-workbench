import { useEffect, useState } from "react";
import QRCode from "qrcode";
import { Copy, LoaderCircle, RefreshCw, Smartphone } from "lucide-react";
import { api, fmtFullDate } from "./api";

type Status = { state: string; message: string; url: string; last_visit?: string };

export default function PhoneAccess({ notify }: { notify: (text: string, error?: boolean) => void }) {
  const [status, setStatus] = useState<Status>({ state: "loading", message: "正在检查连接…", url: "" });
  const [qr, setQr] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let current = true;
    const load = () => api<Status>("/phone/status").then(value => { if (current) setStatus(value); }).catch(error => {
      if (current) setStatus(old => ({ ...old, state: "error", message: error.message }));
    });
    load();
    const timer = setInterval(load, 2500);
    return () => { current = false; clearInterval(timer); };
  }, []);
  useEffect(() => {
    let current = true;
    setQr("");
    if (status.state === "ready" && status.url)
      QRCode.toDataURL(status.url, { width: 260, margin: 2, errorCorrectionLevel: "M" })
        .then(value => { if (current) setQr(value); }).catch(() => notify("二维码生成失败，可以复制下方地址", true));
    return () => { current = false; };
  }, [status.url, status.state, notify]);
  async function action(name: "start" | "stop") {
    setBusy(true);
    try { setStatus(await api("/phone/" + name, "POST", {})); }
    catch (error) { notify((error as Error).message, true); }
    finally { setBusy(false); }
  }
  return <section className="phone-access">
    <h3><Smartphone size={20} />扫码连接手机</h3>
    <p>手机用5G或Wi-Fi扫码，在浏览器输入工作台密码即可使用。</p>
    <p className="hint">首次会下载约55MB连接组件。临时地址经Cloudflare提供，重建连接后需要重新扫码；电脑与工作台需要保持运行。</p>
    <p role="status">{status.state === "starting" && <LoaderCircle className="spin" size={16} />} {status.message}</p>
    {qr && <div className="phone-qr"><img src={qr} alt="手机工作台访问二维码" width={260} height={260} /><p>用手机相机扫码，选择在浏览器打开。</p></div>}
    {status.state === "ready" && status.url && <>
      <a className="phone-url" href={status.url} target="_blank" rel="noreferrer">{status.url}</a>
      <button className="button" onClick={() => navigator.clipboard.writeText(status.url).then(() => notify("手机地址已复制")).catch(() => notify("请手动复制上方地址", true))}><Copy size={15} />复制地址</button>
      <p className="hint">先关闭手机Tailscale、切换到5G测试。这个入口使用HTTPS和工作台密码，连接管理、密钥及备份操作请在电脑进行。</p>
    </>}
    {status.last_visit && <p className="hint">最近扫码入口访问 {fmtFullDate(status.last_visit)}</p>}
    <div className="card-buttons">
      {status.state === "ready" || status.state === "starting"
        ? <button className="button" disabled={busy} onClick={() => action("stop")}>关闭手机访问</button>
        : <button className="button primary" disabled={busy || status.state === "loading"} onClick={() => action("start")}><RefreshCw size={15} />{busy ? "正在开启…" : "开启手机访问"}</button>}
    </div>
  </section>;
}
