import { useState } from 'react';
import { api } from './api';

const browsers = [
  { id: 'chrome', name: 'Google Chrome（谷歌浏览器）', manager: 'chrome://extensions/', standard: true },
  { id: 'edge', name: 'Microsoft Edge', manager: 'edge://extensions/', standard: true },
  { id: '360', name: '360 浏览器', manager: '', standard: false },
  { id: 'sogou', name: '搜狗浏览器', manager: '', standard: false },
  { id: 'other', name: '其他 Chromium 浏览器', manager: '', standard: false },
];

export default function BrowserConnection({ready,directory,paired,notify}:{ready:boolean;directory?:string;paired:boolean;notify:(message:string,error?:boolean)=>void}) {
  const [choice,setChoice]=useState('');
  const [code,setCode]=useState('');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const browser=browsers.find(item=>item.id===choice);
  return <div className="modal-content">
    <label className="field"><span>抖音已登录在哪个浏览器？</span><select aria-label="选择采集浏览器" value={choice} onChange={e=>{setChoice(e.target.value);setCode('');setError('');}} disabled={busy}><option value="">请选择，不会自动切换或重新登录</option>{browsers.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    <p className="hint">选择你平时刷抖音的浏览器及原来的用户配置。工作台可以在其他浏览器查看，收藏仍从实际配对的浏览器读取。</p>
    {paired&&<p className="hint">当前已有连接。选择或查看说明不会断开；只有完成新配对才会替换旧的采集连接。</p>}
    {browser&&<>
      <ol className="browser-connection-steps">
        <li>在{browser.name}中加载下面的本地连接扩展。{browser.manager&&<>手动打开 <code>{browser.manager}</code>，开启开发人员模式，选择「加载解压缩的扩展」。</>}</li>
        <li>在同一个浏览器打开 <code>http://127.0.0.1:8765/</code>。生成配对码，再通过该浏览器的扩展图标粘贴配对；检测到本页组件时，也可直接配对。</li>
        <li>配对后手动同步少量收藏，确认读取正常，再开启每日同步。</li>
      </ol>
      {!browser.standard&&<p className="hint">此浏览器需要先核对版本、Chromium/极速模式及 Manifest V3 支持，并在扩展弹窗检查兼容性。接口检查通过后仍需实际读取验收；不能加载时请让 Agent 检查兼容性，无需重登抖音。</p>}
      <a className="button" href="/api/connections/edge/extension.zip" download>下载本地连接扩展</a>
      <p className="hint">扩展目录：{directory}</p>
      <button className="button primary" disabled={busy} onClick={async()=>{setBusy(true);setError('');try{const result=await api('/connections/edge/pairing','POST',{});setCode(result.code);}catch(e){setError((e as Error).message);}finally{setBusy(false);}}}>{busy?'正在生成…':code?'重新生成配对码':'生成一次性配对码'}</button>
      {code&&<>
        <label className="field"><span>一次性配对码（10分钟有效）</span><input aria-label="一次性配对码" type="password" readOnly value={code} autoComplete="off"/></label>
        <button className="button" onClick={async()=>{try{await navigator.clipboard.writeText(code);notify('配对码已复制，请粘贴到所选浏览器的扩展。');}catch{notify('复制未完成，请手动复制配对码。',true);}}}>复制配对码</button>
        <button className="button" disabled={!ready} onClick={()=>window.postMessage({source:'AIWorkbenchApp',type:'pair',code},location.origin)}>配对本页检测到的组件</button>
        <p className="hint">{ready?'请确认本页位于上面选择的浏览器中，再直接配对。':'本页未检测到扩展；请在所选浏览器加载扩展后刷新工作台，或使用扩展图标粘贴配对码。'}</p>
      </>}
    </>}
    {error&&<p role="alert">{error}</p>}
    <p className="hint">只把收藏作品信息传回本机，不读取 Cookie，不另建抖音登录；出现安全验证会暂停。</p>
  </div>;
}
