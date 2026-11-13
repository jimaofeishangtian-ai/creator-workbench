import { useRef, useState } from 'react';

export const collectionGroups: Record<string, string> = {
  ai_tutorial: 'AI工具与教程',
  aigc_tutorial: 'AIGC教程',
  aigc_example: 'AIGC成片',
  entertainment: '娱乐',
  review: '待确认',
  other: '其他',
};

export const collectionLabel = (group: string) => collectionGroups[group] || group;

export default function CollectionClassifier({ group = '', confirmed = false, options, save, bulk = false }: {
  group?: string;
  confirmed?: boolean;
  options: Record<string, string>;
  save: (group: string) => Promise<void>;
  bulk?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const [custom, setCustom] = useState(false);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const [error, setError] = useState('');
  const preferred = ['aigc_example', 'aigc_tutorial', 'ai_tutorial', 'entertainment'].filter(key => key in options);
  const quickKeys = preferred.length ? preferred : Object.keys(options).filter(key => !['review','other'].includes(key)).slice(0,4);
  async function choose(value: string) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError('');
    try {
      await save(value);
      setCustom(false);
      setExpanded(false);
      setText('');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  return <div className="collection-classifier" aria-label={bulk ? '批量收藏分类' : '整理这条收藏'} aria-busy={busy}>
    <div className="collection-classifier-heading">
      <span>{bulk ? '选中素材归到' : confirmed ? `已确认：${collectionLabel(group)}` : '未确认分类 · 点一下归类'}</span>
      {confirmed && !bulk && <button type="button" disabled={busy} onClick={() => setExpanded(!expanded)} aria-expanded={expanded}>{expanded ? '收起' : '改分类'}</button>}
    </div>
    {(bulk || !confirmed || expanded) && <>
      <div className="collection-quick-choices">
        {quickKeys.map(key =>
          <button key={key} type="button" disabled={busy} aria-pressed={confirmed && group === key} onClick={() => choose(key)}>{options[key]}</button>)}
        {!confirmed && !bulk && ![...quickKeys, 'review'].includes(group) && group &&
          <button type="button" disabled={busy} onClick={() => choose(group)}>确认{collectionLabel(group)}</button>}
        <button type="button" disabled={busy} onClick={() => setCustom(!custom)} aria-expanded={custom}>其他 / 自填</button>
      </div>
      {custom && <div className="collection-custom">
        <select aria-label="选择已有收藏分类" disabled={busy} value="" onChange={e => { if (e.target.value) void choose(e.target.value); }}>
          <option value="">选择已有分类…</option>
          {Object.entries(options).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select>
        <div className="collection-custom-input">
          <input aria-label="自定义收藏分类" placeholder="自填，如开头参考、动作纠错" maxLength={40} value={text} disabled={busy}
            onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.nativeEvent.isComposing && text.trim()) { e.preventDefault(); void choose(text.trim()); } }}/>
          <button type="button" disabled={busy || !text.trim()} onClick={() => choose(text.trim())}>保存分类</button>
        </div>
        {confirmed && !bulk && <button className="collection-reset" type="button" disabled={busy} onClick={() => choose('')}>撤销人工分类，恢复自动</button>}
      </div>}
    </>}
    {busy && <small role="status">正在保存分类…</small>}
    {error && <p className="form-error" role="alert">{error}</p>}
  </div>;
}
