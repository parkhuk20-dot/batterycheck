'use client';

import { useEffect, useRef, useState } from 'react';
import safety from '../../shared/safety.json';

const empty = { brand: '', model: '', model_confirmed: false, product_kind: 'power_bank', demo: false,
  manufactured_on: '', sale_region: '', lot: '', serial_number: '', date_code: '', signs: [], signs_answered: false, scenario: 'complete', official_check: 'unknown' };
const signLabels = { smoke: '연기', flame: '불꽃', swelling: '배터리가 부풀어 오름', damage: '제품이 깨지거나 손상됨', discoloration: '색이 변함', heat: '평소보다 심하게 뜨거움', odor: '이상한 냄새가 남', sound: '이상한 소리가 남' };
const fields = { model: '모델명', brand: '브랜드', manufactured_on: '제조일', sale_region: '구매 국가/지역', lot: 'LOT / 제조번호', serial: '일련번호', serial_number: '시리얼번호', date_code: '제조 날짜 코드', extra: '추가 식별 조건' };
const conditionLabels = { match: '일치', mismatch: '다름', unknown: '확인 필요', skipped: '비교 안 함' };
const sourceLabels = { available: '최신 정보 확인 완료', complete: '확인 완료', curated: '공식 공지 확인 완료', not_synced: '아직 업데이트 전', failed: '불러오지 못함', stale: '오래된 정보', unconfigured: '아직 연결되지 않음' };
const actionLabels = { unconfirmed: '아직 확인하지 않음', checking: '확인 중', user_completed: '조치를 마쳤다고 기록' };
const symptomOf = input => input.signs?.length ? 'yes' : input.signs_answered ? 'none' : 'unknown';
const instant = value => value ? new Date(value).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' }) + ' KST' : '기록 없음';
const bodyFor = form => ({ ...form, manufactured_on: form.manufactured_on || null, sale_region: form.sale_region || null, lot: form.lot || null, serial_number: form.serial_number || null, date_code: form.date_code || null });
const unsupportedPhoto = '이 사진 형식은 아직 지원하지 않아요. JPG, PNG 또는 WEBP 사진을 사용해주세요.';
// Phone photos are large and may be rotated; send an upright JPEG no wider than the label needs.
async function prepareImage(file) {
  if (!['image/jpeg', 'image/png', 'image/webp', 'image/heic', 'image/heif'].includes(file.type)) throw new Error(unsupportedPhoto);
  let bitmap;
  try { bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' }); } catch { throw new Error(unsupportedPhoto); }
  const scale = Math.min(1, 2400 / Math.max(bitmap.width, bitmap.height));  // keep small label lettering legible
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(bitmap.width * scale); canvas.height = Math.round(bitmap.height * scale);
  const context = canvas.getContext('2d');
  context.fillStyle = '#fff'; context.fillRect(0, 0, canvas.width, canvas.height);
  context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL('image/jpeg', 0.92).split(',')[1];
}
const safeLink = value => { try { const u = new URL(value); return u.protocol === 'https:' ? u.href : undefined; } catch { return undefined; } };

async function api(path, method = 'GET', body) {
  const response = await fetch(`/api/${path}`, { method, cache: 'no-store', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
  const data = response.status === 204 ? null : await response.json();
  if (!response.ok) { const error = new Error(typeof data?.detail === 'string' ? data.detail : '입력한 내용을 다시 확인해주세요.'); error.status = response.status; throw error; }
  return data;
}
function Sources({ sources = [] }) {
  return <ul className="sources">{sources.map(source => <li key={source.url}><a href={safeLink(source.url)} target="_blank" rel="noreferrer">{source.title} ↗</a><small>게시 {source.published_on} · 확인 {source.checked_on}</small></li>)}</ul>;
}
function Risk({ risk }) {
  if (!risk) return null;
  const urgent = ['emergency', 'concern'].includes(risk.level);
  return <section className={`risk ${risk.level}`} aria-label="현재 제품 상태" role={urgent ? 'alert' : undefined}>
    <strong>{risk.level === 'emergency' ? '긴급 상황 안내' : risk.level === 'concern' ? '이상 증상이 있습니다' : '현재 제품 상태'}</strong>
    {urgent && <p>리콜 대상인지와 관계없이 사용과 충전을 중지하고 안전 안내를 확인해주세요.</p>}<p>{risk.text}</p>
    {urgent && <><a className="phone" href="tel:119">119 전화 연결</a><small>직접 전화를 거는 링크입니다. 자동으로 신고되지 않습니다.</small></>}
    <Sources sources={risk.sources} />
  </section>;
}
function Result({ result, historical = false, onExplain, children }) {
  return <article className="result" aria-label={historical ? '과거 확인 기록' : '확인 결과'}>
    <div className="result-title"><span className={`status ${result.status}`}>{result.label}</span><span className="tag">{result.synthetic ? '가상 공지 기반 시연' : '공식 리콜 정보 기준'}</span></div>
    {historical && <p className="warning">과거 기록입니다. 지금 다시 확인한 결과가 아닙니다.</p>}
    <p>{result.limitations}</p><div className="next-action"><strong>지금 해야 할 일</strong><p>{result.next_action}</p></div>
    {children}
    {!children && result.questions?.length > 0 && <div className="questions"><strong>추가로 필요한 정보</strong><ul>{result.questions.map((q, i) => <li key={q.notice_id + q.field + i}>{q.text}</li>)}</ul></div>}
    {result.notices?.map(n => <details key={n.id} open><summary>{n.title}</summary>
      {safeLink(n.source_url || n.url) && <p><a href={safeLink(n.source_url || n.url)} target="_blank" rel="noreferrer">공식 리콜 공지 보기 ↗</a></p>}
      {n.checks?.length > 0 && <div className="table-wrap"><table><caption>확인한 정보</caption><thead><tr><th>항목</th><th>내 제품</th><th>공식 리콜 조건</th><th>결과</th></tr></thead><tbody>{n.checks.map((c, i) => <tr key={c.field + i}><th scope="row">{fields[c.field] || c.field}</th><td>{c.input || '미입력'}</td><td>{Array.isArray(c.expected) ? c.expected.join(' / ') : c.expected}</td><td className={c.state}>{c.field === 'extra' ? '확인 완료' : conditionLabels[c.state] || c.state}</td></tr>)}</tbody></table></div>}
      <p>{n.action}</p>
    </details>)}
    <dl className="evidence"><div><dt>확인한 시각</dt><dd>{instant(result.checked_at)}</dd></div>
      {result.sources?.map(s => <div key={s.id}><dt>{s.name}</dt><dd>{sourceLabels[s.state] || '상태 확인 필요'}{s.freshness === 'stale_or_invalid' && ' · 오래된 정보'}<br />마지막 업데이트: {instant(s.last_success_at)}</dd></div>)}
    </dl>
    {onExplain && <p className="explain"><span>이 내용이 어렵나요?</span> <button type="button" className="secondary" onClick={onExplain}>AI에게 쉽게 설명받기</button></p>}
    <Risk risk={result.risk} />
  </article>;
}
function Account({ user, busy, onAuth, onLogout, onPreferences, onExport, onDelete }) {
  const [register, setRegister] = useState(false);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [consent, setConsent] = useState(false);
  const [deletePassword, setDeletePassword] = useState('');
  if (user) return <section className="account panel" id="account" aria-label="계정 관리"><div className="account-bar"><div><strong>{user.username}님</strong><small>내 제품과 확인 기록은 이 계정에서만 볼 수 있습니다.</small></div><button className="secondary" disabled={busy} onClick={onLogout}>로그아웃</button></div>
    <div className="notify-setting"><h3>리콜 정보 변경 알림</h3><label className="checkbox"><input type="checkbox" disabled={busy} checked={Boolean(user.notification_consent)} onChange={e => onPreferences(e.target.checked)} />저장한 제품의 변경 사항을 알려주세요.</label><p className="small">공식 리콜 정보가 새로 등록되거나 변경되면, 저장한 제품이 해당되는지 다시 확인해 알려드립니다.</p><p className="small">알림은 배터리체크 웹사이트 안에서 확인할 수 있으며, 이메일이나 문자로는 발송되지 않습니다.</p></div>
    <details><summary>내 기록 · 계정 관리</summary><button className="secondary" onClick={onExport} disabled={busy}>내 기록 내려받기</button>
      <details className="delete-account"><summary>계정과 모든 기록 삭제</summary><p>내 제품, 확인 기록, 알림, AI 대화가 모두 삭제됩니다. 되돌릴 수 없습니다.</p><label>삭제 확인 비밀번호<input type="password" autoComplete="current-password" value={deletePassword} onChange={e => setDeletePassword(e.target.value)} /></label><button className="secondary danger" disabled={busy || !deletePassword} onClick={() => onDelete(deletePassword)}>모든 내 기록 영구 삭제</button></details>
    </details></section>;
  return <section className="account panel" id="account" aria-label="로그인"><h2>{register ? '회원가입' : '로그인'}</h2><p className="small">리콜 확인은 로그인 없이 할 수 있습니다. 로그인하면 제품을 저장하고 새 리콜 알림을 받을 수 있습니다.</p>
    <form onSubmit={e => { e.preventDefault(); onAuth(register, { username, password, ...(register ? { privacy_consent: consent } : {}) }); }}><fieldset disabled={busy}><div className="auth-fields"><label>아이디<input value={username} onChange={e => setUsername(e.target.value)} autoComplete="username" required minLength={3} maxLength={32} pattern="(?:[A-Za-z0-9_.]|-)+" placeholder="영문·숫자·점·밑줄·하이픈" /></label><label>비밀번호<input type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete={register ? 'new-password' : 'current-password'} minLength={10} maxLength={128} required /></label><button type="submit" disabled={register && !consent}>{register ? '회원가입' : '로그인'}</button></div>
      {register && <label className="checkbox"><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} required />아이디·암호화된 비밀번호·제품·확인 이력을 계정 삭제 때까지 보관하는 데 동의합니다. AI 도우미는 별도 동의 후에만 작동합니다.</label>}
    </fieldset></form>
    <p className="auth-switch">{register ? '이미 계정이 있으신가요?' : '아직 계정이 없으신가요?'} <button type="button" className="text-button" disabled={busy} onClick={() => setRegister(!register)}>{register ? '로그인' : '회원가입'}</button></p></section>;
}

function Assistant({ user, status, seed, onConsent }) {
  const [items, setItems] = useState([]);
  const [question, setQuestion] = useState('');
  const [waiting, setWaiting] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { if (user?.ai_consent) api('assistant/history').then(setItems).catch(() => {}); else setItems([]); }, [user?.ai_consent]);
  useEffect(() => { if (seed) setQuestion(seed.text); }, [seed]);
  async function ask(event) {
    event.preventDefault();
    const asked = question.trim();
    if (!asked) return;
    setWaiting(true); setError('');
    try {
      const reply = await api('assistant', 'POST', { question: asked });
      setItems(current => [...current, { role: 'user', content: asked, sources: [] }, { role: 'assistant', content: reply.answer, sources: reply.sources }]);
      setQuestion('');
    } catch (e) { setError(e.message); } finally { setWaiting(false); }
  }
  async function clear() { setError(''); try { await api('assistant/history', 'DELETE'); setItems([]); } catch (e) { setError(e.message); } }
  return <section className="panel assistant" id="assistant" aria-label="AI 안내 도우미"><p className="eyebrow">AI 도우미</p><h2>어려운 내용은 AI에게 물어보세요</h2>
    <p className="small">리콜 여부는 공식 정보로 먼저 확인하고, AI는 그 내용을 쉽게 설명합니다. <strong>AI가 만든 답변이며 리콜 여부나 제품 안전을 결정하지 않습니다.</strong></p>
    {!user ? <p className="small"><a href="#account">로그인</a>하면 이용할 수 있습니다.</p> : !status?.available ? <p className="warning">지금은 AI 도우미를 이용할 수 없습니다. 리콜 확인과 내 제품은 그대로 쓸 수 있습니다.</p> : <>
      <label className="checkbox"><input type="checkbox" checked={Boolean(user.ai_consent)} disabled={waiting} onChange={e => onConsent(e.target.checked)} />질문 내용과 내 제품 정보(브랜드·모델·구매 국가·제조일·LOT·조치 상태·확인 결과)를 답변 생성을 위해 {status.provider}로 보내고, 대화를 내 계정에 저장하는 데 동의합니다. {status.vision ? '사진으로 확인을 쓰면 그 사진도 보내며, 사진은 저장하지 않습니다.' : '사진은 보내지 않습니다.'} 동의는 언제든 해제할 수 있습니다.</label>
      {user.ai_consent && <>
        <div className="chat" aria-live="polite">{items.length === 0 && <p className="small">예: “내 제품에 지금 확인할 게 있나요?”, “LOT 번호는 어디에 있나요?”</p>}
          {items.map((item, i) => <article key={i} className={`chat-item ${item.role}`}><span className="tag">{item.role === 'user' ? '내 질문' : 'AI 생성 답변 · 공식 판정 아님'}</span><p>{item.content}</p>
            {item.sources?.length > 0 && <ul className="sources">{item.sources.map(s => <li key={s.url}><a href={safeLink(s.url)} target="_blank" rel="noreferrer">{s.title} ↗</a><small>참고한 공식 자료 · {s.id}</small></li>)}</ul>}</article>)}
          {waiting && <p className="small" role="status">공식 정보와 내 기록을 확인하는 중…</p>}</div>
        {error && <p className="error" role="alert">{error}</p>}
        <form onSubmit={ask}><label>질문<textarea value={question} maxLength={1000} rows={3} disabled={waiting} onChange={e => setQuestion(e.target.value)} placeholder="리콜 공지 내용이나 지금 해야 할 일을 물어보세요" /></label>
          <div className="form-actions"><button type="submit" disabled={waiting || !question.trim()}>{waiting ? '답변 생성 중…' : '질문하기'}</button>{items.length > 0 && <button type="button" className="text-button danger" disabled={waiting} onClick={clear}>대화 기록 지우기</button>}</div></form></>}
    </>}
  </section>;
}
function Feedback() {
  const [rating, setRating] = useState('');
  const [comment, setComment] = useState('');
  const [state, setState] = useState('');
  async function send(event) { event.preventDefault(); try { await api('feedback', 'POST', { rating: Number(rating), comment }); setState('의견을 저장했습니다. 고맙습니다.'); setRating(''); setComment(''); } catch (e) { setState(e.message); } }
  return <details className="panel feedback"><summary>사용 의견 보내기</summary><p className="small">서비스 개선에만 사용합니다. 이름·연락처·일련번호는 적지 마세요. 로그인 상태에서 보낸 의견은 계정을 삭제하면 함께 삭제됩니다.</p>
    <form onSubmit={send}><label>이 서비스가 도움이 되었나요?<select value={rating} required onChange={e => setRating(e.target.value)}><option value="">선택</option><option value="5">5 · 매우 도움 됨</option><option value="4">4 · 도움 됨</option><option value="3">3 · 보통</option><option value="2">2 · 별로</option><option value="1">1 · 전혀 아님</option></select></label>
      <label>불편했던 점이나 바라는 점 <span>선택</span><textarea value={comment} maxLength={1000} rows={3} onChange={e => setComment(e.target.value)} /></label><button type="submit" className="secondary">의견 보내기</button></form>
    {state && <p className="message" role="status">{state}</p>}</details>;
}

export default function Home() {
  const [form, setForm] = useState(empty);
  const [result, setResult] = useState(null);
  const [products, setProducts] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [history, setHistory] = useState(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [server, setServer] = useState('연결 확인 중');
  const [dirty, setDirty] = useState(true);
  const [user, setUser] = useState(null);
  const [notifications, setNotifications] = useState([]);
  const [ocr, setOcr] = useState(null);
  const [meta, setMeta] = useState(null);
  const [sourceStatus, setSourceStatus] = useState(null);
  const [online, setOnline] = useState(true);
  const [symptom, setSymptom] = useState('unknown');
  const [moreOpen, setMoreOpen] = useState(false);
  const [demoMode, setDemoMode] = useState(false);
  const [askSeed, setAskSeed] = useState(null);
  const accountGeneration = useRef(0);
  const accountChannel = useRef(null);
  const workSequence = useRef(0);
  const photoInput = useRef(null);
  const cameraInput = useRef(null);
  async function guardedApi(...args) {
    const generation = accountGeneration.current;
    let data;
    try { data = await api(...args); } catch (error) {
      if (generation !== accountGeneration.current) error.stale = true;
      throw error;
    }
    if (generation !== accountGeneration.current) {
      const error = new Error('계정이 바뀌어 이전 요청을 취소했습니다.');
      error.stale = true;
      throw error;
    }
    return data;
  }

  async function loadPrivate() { const generation = accountGeneration.current; const [items, alerts] = await Promise.all([guardedApi('products'), guardedApi('notifications')]); if (generation !== accountGeneration.current) return; setProducts(items); setNotifications(alerts); }
  function resetForm(next = empty) { setForm(next); setSymptom(symptomOf(next)); setResult(null); setDirty(true); setHistory(null); }
  function clearPrivate() { accountGeneration.current += 1; workSequence.current += 1; setBusy(false); setUser(null); setProducts([]); setNotifications([]); setSelectedId(null); setOcr(null); setMoreOpen(false); resetForm(); }
  useEffect(() => {
    // Practice (virtual notice) controls stay out of the consumer screen unless asked for with ?demo.
    setDemoMode(new URLSearchParams(window.location.search).has('demo'));
    guardedApi('health').then(() => setServer('서버 연결됨')).catch(() => setServer('서버 연결 필요'));
    guardedApi('meta').then(setMeta).catch(() => {});
    guardedApi('sources/status').then(setSourceStatus).catch(() => {});
    const generation = accountGeneration.current;
    guardedApi('auth/me').then(async account => { if (generation !== accountGeneration.current) return; setUser(account); await loadPrivate(); }).catch(() => {});
    const channel = typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel('batterycheck-auth') : null;
    accountChannel.current = channel;
    if (channel) channel.onmessage = () => {
      clearPrivate();
      const generation = accountGeneration.current;
      guardedApi('auth/me').then(async account => { if (generation !== accountGeneration.current) return; setUser(account); await loadPrivate(); }).catch(() => {});
    };
    const update = () => setOnline(navigator.onLine);
    update(); window.addEventListener('online', update); window.addEventListener('offline', update);
    if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
    return () => { channel?.close(); window.removeEventListener('online', update); window.removeEventListener('offline', update); };
  }, []);
  function change(field, value) {
    setForm(current => {
      if (field === 'demo') return { ...empty, demo: value, signs: current.signs, signs_answered: current.signs_answered };
      const next = { ...current, [field]: value };
      // Typing the brand and model yourself is the confirmation; values read from a photo go through the photo result buttons.
      if (['brand', 'model'].includes(field)) { next.model_confirmed = Boolean(next.model.trim()); next.official_check = 'unknown'; next.serial_number = ''; next.date_code = ''; }
      return next;
    });
    setResult(null); setHistory(null); setDirty(true); setMessage('');
  }
  function chooseSymptom(value) { setSymptom(value); setForm(current => ({ ...current, signs: value === 'yes' ? current.signs : [], signs_answered: value === 'none' || (value === 'yes' && current.signs.length > 0) })); setResult(null); setDirty(true); }
  function toggleSign(sign) { setForm(current => { const signs = current.signs.includes(sign) ? current.signs.filter(s => s !== sign) : [...current.signs, sign]; return { ...current, signs, signs_answered: signs.length > 0 }; }); setResult(null); setDirty(true); }
  const localRisk = form.signs.length ? { level: form.signs.some(s => ['smoke', 'flame'].includes(s)) ? 'emergency' : 'concern', text: form.signs.some(s => ['smoke', 'flame'].includes(s)) ? safety.emergency_text : safety.concern_text, sources: safety.sources } : null;
  async function run(work) { const sequence = ++workSequence.current; setBusy(true); setError(''); setMessage(''); try { await work(); } catch (e) { if (e.stale) return; if (e.status === 401) clearPrivate(); setError(e.message); } finally { if (sequence === workSequence.current) setBusy(false); } }
  function check(event) { event.preventDefault(); return runCheck(form); }
  async function runCheck(next) { await run(async () => { setResult(null); const found = await guardedApi('checks', 'POST', bodyFor(next)); setResult(found); if (found.status === 'needs_information') setMoreOpen(true); setDirty(false); setServer('서버 연결됨'); }); }
  async function save() { await run(async () => { const saved = await guardedApi(selectedId ? `products/${selectedId}` : 'products', selectedId ? 'PUT' : 'POST', bodyFor(form)); setSelectedId(saved.id); setResult(saved.latest_result); setDirty(false); setProducts(current => [saved, ...current.filter(p => p.id !== saved.id)]); setHistory(null); setMessage('내 제품에 저장했습니다.'); }); }
  function selectProduct(product) { const input = { ...product.input, official_check: product.input.official_check || 'unknown', serial_number: product.input.serial_number || '', date_code: product.input.date_code || '', manufactured_on: product.input.manufactured_on || '', sale_region: product.input.sale_region || '', lot: product.input.lot || '' }; resetForm(input); setSelectedId(product.id); setOcr(null); setMoreOpen(Boolean(input.manufactured_on || input.sale_region || input.lot)); setMessage('저장된 내용을 불러왔습니다. 최신 정보로 다시 확인해주세요.'); }
  async function action(product, state) { await run(async () => { const updated = await guardedApi(`products/${product.id}/action`, 'PATCH', { state }); setProducts(current => current.map(p => p.id === product.id ? updated : p)); setHistory(null); setMessage('조치 상태를 기록했습니다. 사용 재개 허가나 회수 인증이 아닙니다.'); }); }
  async function remove(product) { await run(async () => { await guardedApi(`products/${product.id}`, 'DELETE'); setProducts(current => current.filter(p => p.id !== product.id)); setHistory(null); setNotifications(current => current.filter(n => n.product_id !== product.id)); if (selectedId === product.id) { setSelectedId(null); setHistory(null); setResult(null); setDirty(true); } setMessage('제품과 확인 기록을 삭제했습니다.'); }); }
  function sample() { resetForm({ ...empty, brand: 'DEMO LAB', model: 'DEMO PB-100', model_confirmed: true, demo: true, manufactured_on: '2026-02-15', sale_region: 'DEMO-KR', lot: '001A' }); setSelectedId(null); setOcr(null); setMoreOpen(true); setMessage('연습용 가상 예시입니다. 실제 리콜 공지가 아닙니다.'); }
  async function identify(file) { if (!file) return; const generation = accountGeneration.current; await run(async () => { if (!user) throw new Error('사진으로 확인하려면 로그인이 필요합니다.'); setOcr(null); setMessage('사진에서 제품 정보를 확인하고 있어요.'); const data = await prepareImage(file); if (generation !== accountGeneration.current) return; setOcr(await guardedApi('identify', 'POST', { image_base64: data })); setMessage(''); }); }
  // Photo readings reach the form only through these two buttons, after the user has seen them.
  function applyPhoto(search, brand = ocr.brand || '', model = ocr.model || '') { const next = { ...empty, brand, model, model_confirmed: Boolean(model) }; resetForm(next); setSelectedId(null); setMoreOpen(false); setOcr(null); if (search) return runCheck(next); setMessage('브랜드와 모델명을 라벨과 비교해 고친 뒤 리콜 정보를 확인해주세요.'); document.querySelector(model ? 'input[name=brand]' : 'input[name=model]')?.focus(); }
  function retake() { (window.matchMedia('(pointer: coarse)').matches ? cameraInput : photoInput).current?.click(); }
  async function sync() { await run(async () => { const data = await guardedApi('sources/sync', 'POST', {}); setSourceStatus(data.source_status); await loadPrivate(); setMessage(`최신 정보를 다시 확인했습니다. 내 제품 ${data.rechecked ?? 0}개를 다시 확인했고 새 알림은 ${data.notifications_created ?? 0}건입니다.`); }); }
  // The answer changes the outcome, so the lookup runs again at once with it.
  // Answers to what the matched notice asks for; the result stays on screen until they are submitted.
  function setExtra(field, value) { setForm(current => ({ ...current, [field]: value })); setDirty(true); }
  function explain() { setAskSeed({ text: `${form.brand} ${form.model} 확인 결과가 "${result.label}"인데, 무슨 뜻이고 지금 무엇을 해야 하는지 쉽게 설명해 주세요.` }); document.getElementById('assistant')?.scrollIntoView({ behavior: 'smooth' }); }
  const sourceItems = Array.isArray(sourceStatus?.sources) ? sourceStatus.sources : [];
  const domesticReady = sourceItems.some(s => s.id === 'safety-korea' && s.state !== 'unconfigured');
  const canSave = result && !dirty && form.model_confirmed && form.brand.trim() && result.status !== 'needs_identification';

  return <><a className="skip-link" href="#main">본문으로 이동</a><header><div className="brand"><span className="brand-mark" aria-hidden="true">B</span><span>배터리체크<small>보조배터리 리콜 확인 서비스</small></span></div><span className="header-note"><span>{server}</span>{user ? <a href="#account">{user.username}님</a> : <a href="#account">로그인</a>}</span></header>
    <main id="main"><div className="intro"><div><p className="eyebrow">보조배터리 리콜 확인부터 후속 조치까지</p><h1>내 보조배터리,<br />리콜 대상인지 확인해보세요.</h1><p>제품 라벨을 촬영하거나 모델명을 입력하면<br className="wide" /> AI가 제품 정보를 확인하고 공식 리콜 공지와 비교해드립니다.<br className="wide" /> 리콜 대상이라면 무엇을 해야 하는지도 함께 안내합니다.</p></div><aside className="recall-info" aria-label="리콜 안내"><p className="eyebrow">리콜이란 무엇인가요?</p><h2>안전 문제가 확인된 제품을<br />제조사가 회수하거나 교환하는 조치입니다.</h2><p>제품에서 화재, 발열 등 안전 문제가 확인되면 제조사나 관계 기관에서 해당 제품의 리콜을 공지할 수 있습니다.</p><p>하지만 같은 제품처럼 보여도 모델명이나 제조기간 등에 따라 리콜 대상 여부가 달라질 수 있습니다.</p><p>배터리체크는 공식 리콜 공지를 찾아 내 제품이 대상인지 확인할 수 있도록 도와드립니다.</p><ol className="steps"><li className="step"><svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="5" y="3" width="14" height="18" rx="2" /><path d="M9 8h6" /><path d="M9 12h6" /><path d="M9 16h3" /></svg><span>리콜 공지 발표<small>(제조사 또는 관계 기관)</small></span></li><li className="arrow" aria-hidden="true"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12h14" /><path d="M13 6l6 6-6 6" /></svg></li><li className="step"><svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="6" /><path d="M20 20l-4.5-4.5" /></svg><span>내 제품이 대상인지 확인<small>(모델명, 제조기간 등)</small></span></li><li className="arrow" aria-hidden="true"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12h14" /><path d="M13 6l6 6-6 6" /></svg></li><li className="step"><svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4 12a8 8 0 0 1 13.7-5.6L20 8" /><path d="M20 4v4h-4" /><path d="M20 12a8 8 0 0 1-13.7 5.6L4 16" /><path d="M4 20v-4h4" /></svg><span>회수·교환 등<br /> 필요한 조치 확인</span></li></ol></aside></div>
      {!online && <p className="error" role="alert">오프라인입니다. 최신 리콜 정보를 확인할 수 없습니다. 화면의 과거 결과를 지금 결과로 보지 마세요.</p>}
      {localRisk && <Risk risk={localRisk} />}
      {error && <div className="error" role="alert">{error}</div>}{message && <p className="message" role="status">{message}</p>}
      <div className="workspace" id="check"><section className="panel"><div className="section-heading"><div><p className="eyebrow">리콜 확인</p><h2>{selectedId ? '저장한 제품 다시 확인' : '모델명을 입력하세요'}</h2></div>{demoMode && <button type="button" className="secondary" disabled={busy} onClick={sample}>가상 예시 채우기</button>}</div>
        <p className="small">{domesticReady ? '국내(Safety Korea)와 미국(CPSC)의 공식 리콜 공지를 확인합니다.' : '미국(CPSC)의 공식 리콜 공지를 확인합니다. 국내 리콜은 Safety Korea 사이트에서 직접 확인해주세요.'} 배터리 수명이나 내부 상태를 검사하는 서비스가 아닙니다.</p>
        <div className="photo-input"><div className="photo-buttons"><label className="camera">카메라로 촬영<input ref={cameraInput} type="file" accept="image/*" capture="environment" disabled={busy || !user} onChange={e => { identify(e.target.files?.[0]); e.target.value = ''; }} /></label><label>사진으로 확인<input ref={photoInput} type="file" accept="image/jpeg,image/png,image/webp,image/heic,image/heif" disabled={busy || !user} onChange={e => { identify(e.target.files?.[0]); e.target.value = ''; }} /></label></div><small>{!user ? '사진으로 확인하려면 로그인이 필요합니다. 직접 입력은 바로 할 수 있습니다.' : user.ai_consent && meta?.assistant?.vision ? '제품 뒷면이나 바닥의 라벨을 가까이 찍어주세요. AI가 브랜드와 모델명을 찾아드립니다. 사진 원본은 확인 후 저장하지 않습니다.' : '제품 뒷면이나 바닥의 라벨을 가까이 찍어주세요. 브랜드와 모델명을 찾아드립니다. 사진 원본은 확인 후 저장하지 않습니다.'}</small>
          {ocr && <div className="ocr-result" role="status"><strong>{ocr.message}</strong>
            {(ocr.brand || ocr.model || ocr.status === 'unsure') && <dl className="found"><div><dt>브랜드</dt><dd>{ocr.brand || '찾지 못함'}{ocr.brand_source === 'db_enrichment' && <small>공식 리콜 정보에서 확인한 브랜드입니다. 모델명은 사진에서 확인했어요.</small>}{ocr.brand_source === 'website_domain' && <small>사진 속 웹사이트 주소에서 추정했어요. 맞는지 확인해주세요.</small>}</dd></div>{ocr.product_name && <div><dt>제품명</dt><dd>{ocr.product_name}</dd></div>}<div><dt>모델명</dt><dd>{ocr.model || (ocr.status === 'unsure' && ocr.model_candidates.length ? ocr.model_candidates.join(' 또는 ') + ' (확실하지 않음)' : '찾지 못함')}</dd></div></dl>}
            {ocr.model_options?.length > 0 && <p className="small">공식 리콜 공지에 있는 {ocr.brand} 모델입니다. 라벨의 모델명과 같을 때만 선택해주세요: {ocr.model_options.map(option => option.model).join(', ')}</p>}
            <div className="form-actions">{ocr.status === 'found' ? <><button type="button" disabled={busy} onClick={() => applyPhoto(true)}>맞아요, 리콜 확인</button><button type="button" className="secondary" disabled={busy} onClick={() => applyPhoto(false)}>직접 수정</button></>
              : ocr.status === 'found_db' ? <><button type="button" disabled={busy} onClick={() => applyPhoto(true)}>맞아요</button><button type="button" className="secondary" disabled={busy} onClick={() => applyPhoto(false, '')}>아니에요</button></>
              : ocr.status === 'choose' ? <>{ocr.brand_options.map(brand => <button type="button" className="secondary" key={brand} disabled={busy} onClick={() => applyPhoto(true, brand)}>{brand} {ocr.model}</button>)}<button type="button" className="secondary" disabled={busy} onClick={() => applyPhoto(false, '')}>직접 입력</button></>
              : ocr.status === 'model_only' ? <><button type="button" disabled={busy} onClick={() => applyPhoto(true)}>모델명으로 리콜 확인</button><button type="button" className="secondary" disabled={busy} onClick={() => applyPhoto(false)}>직접 수정</button></>
              : <>{(ocr.model_options || []).map(option => <button type="button" className="secondary" key={option.model} disabled={busy} onClick={() => applyPhoto(true, option.brand, option.model)}>내 제품은 {option.model}</button>)}<button type="button" className="secondary" disabled={busy} onClick={retake}>다시 촬영</button><button type="button" className="secondary" disabled={busy} onClick={() => applyPhoto(false)}>직접 입력</button></>}</div>
            {ocr.ai_hint && <small>AI 도우미 이용에 동의하면 사진을 더 정확하게 읽을 수 있어요. 동의하면 올린 사진이 AI로 전송됩니다.</small>}
            {ocr.text && <details><summary>개발용: 읽은 글자 전체</summary><pre>{ocr.text}</pre></details>}</div>}
        </div>
        {form.demo && <aside className="demo-banner"><strong>연습용 가상 예시</strong><span>실제 리콜 공지가 아닙니다.</span></aside>}
        <form onSubmit={check}><fieldset disabled={busy}><div className="fields"><label>브랜드 <span>모르면 비워두세요</span><input name="brand" value={form.brand} maxLength={80} onChange={e => change('brand', e.target.value)} placeholder="예: Anker" /></label><label>모델명<input name="model" value={form.model} maxLength={80} onChange={e => change('model', e.target.value)} placeholder="라벨에 적힌 그대로 (예: A1263)" /></label></div>
          {demoMode && <label className="checkbox"><input type="checkbox" checked={form.demo} onChange={e => change('demo', e.target.checked)} />가상 DEMO LAB 제품으로 시연합니다.</label>}
          {form.demo && <details className="more-fields" open={moreOpen} onToggle={e => setMoreOpen(e.currentTarget.open)}><summary>더 정확하게 확인하기 (선택)</summary><p className="small">모르는 항목은 비워두세요.</p>
            <div className="fields"><label>제조일 <span>제품에 적힌 날짜</span><input type="date" value={form.manufactured_on} onChange={e => change('manufactured_on', e.target.value)} /></label><label>구매 국가/지역 <span>해외 직구는 판매처 기준</span><select value={form.sale_region} onChange={e => change('sale_region', e.target.value)}><option value="">모름</option><option value="KR">대한민국</option><option value="US">미국</option><option value="OTHER">그 밖의 지역</option>{form.demo && <><option value="DEMO-KR">DEMO-KR (가상)</option><option value="DEMO-US">DEMO-US (가상)</option></>}</select></label></div>
            <label>LOT / 제조번호 <span>제품 뒷면의 LOT, Batch, 제조번호 · 전체 시리얼번호(S/N)는 입력하지 마세요</span><input value={form.lot} maxLength={32} onChange={e => change('lot', e.target.value)} /></label>
          </details>}
          <fieldset className="signs"><legend>현재 제품에 이상 증상이 있나요?</legend><div className="symptom-choice">{[['none', '없음'], ['yes', '있음'], ['unknown', '잘 모르겠음']].map(([value, label]) => <label className="checkbox" key={value}><input type="radio" name="symptom" checked={symptom === value} onChange={() => chooseSymptom(value)} />{label}</label>)}</div>
            {symptom === 'yes' && <><p className="small">어떤 증상이 있나요?</p><div className="sign-grid">{Object.entries(signLabels).map(([sign, label]) => <label className="checkbox" key={sign}><input type="checkbox" checked={form.signs.includes(sign)} onChange={() => toggleSign(sign)} />{label}</label>)}</div></>}</fieldset>
          {form.demo && <label>데모 출처 상태<select value={form.scenario} onChange={e => change('scenario', e.target.value)}><option value="complete">가상 저장소 확인 완료</option><option value="outage">불러오기 실패 시연</option><option value="stale">오래된 정보 시연</option></select></label>}
          <div className="form-actions"><button type="submit">{busy ? '확인 중…' : '리콜 정보 확인'}</button></div>
        </fieldset></form>
        <details className="emergency-link"><summary>연기, 불꽃, 심한 발열이 있나요? 긴급 안전 안내 보기</summary><p>제품 검색을 계속하지 말고 사용과 충전을 중지하세요. 위험하면 안전한 곳으로 이동한 뒤 119에 신고하세요.</p><a className="phone" href="tel:119">119 전화 연결</a><Sources sources={[safety.sources[0], safety.sources[2]]} /></details>
        {result ? <><Result result={result} onExplain={meta?.assistant?.available ? explain : undefined}>
          {result.fields?.length > 0 && !form.demo ? <form onSubmit={e => { e.preventDefault(); runCheck(form); }}><fieldset className="signs official-check" disabled={busy}><legend>{result.required_fields?.length ? '추가로 필요한 정보' : '입력한 추가 정보'}</legend>
            {result.questions.filter((q, i, all) => q.field !== 'official_check' && all.findIndex(o => o.field === q.field) === i).map(q => <p className="small" key={q.field}>{q.text}</p>)}
            {result.fields.filter((q, i, all) => all.findIndex(o => o.field === q.field) === i).map(q => q.field === 'official_check' ? <div key={q.field}><p className="small">{q.text}</p>{safeLink(q.url) && <p><a href={safeLink(q.url)} target="_blank" rel="noreferrer">제조사 조회 페이지 열기 ↗</a></p>}<div className="symptom-choice">{[['unknown', '아직 조회하지 않았어요'], ['affected', '대상이라고 나왔어요'], ['not_affected', '대상이 아니라고 나왔어요']].map(([value, label]) => <label className="checkbox" key={value}><input type="radio" name="official-check" checked={(form.official_check || 'unknown') === value} onChange={() => setExtra('official_check', value)} />{label}</label>)}</div></div>
              : q.field === 'sale_region' ? <label key={q.field}>구매 국가 <span>{q.hint}</span><select value={form.sale_region} onChange={e => setExtra('sale_region', e.target.value)}><option value="">선택</option><option value="KR">대한민국</option><option value="US">미국</option><option value="OTHER">그 밖의 지역</option></select></label>
              : <label key={q.field}>{q.label} <span>{q.hint}</span><input name={q.field} value={form[q.field] || ''} maxLength={q.field === 'date_code' ? 16 : 40} autoCapitalize="characters" autoComplete="off" onChange={e => setExtra(q.field, e.target.value)} /></label>)}
            <div className="form-actions"><button type="submit">{busy ? '확인 중…' : '입력한 정보로 확인'}</button></div></fieldset></form> : null}
          </Result>
          {result.status !== 'needs_identification' && <div className="save-offer"><strong>이 제품을 저장하시겠어요?</strong><p className="small">저장해 두면 새로운 리콜이 등록됐을 때 다시 확인하기 쉽습니다.{user && !form.brand.trim() && ' 저장하려면 브랜드도 입력해주세요.'}</p>{user ? <button type="button" disabled={busy || !canSave} onClick={save}>{selectedId ? '다시 확인한 결과 저장' : '내 제품에 저장'}</button> : <a className="button-link" href="#account">로그인하고 저장</a>}</div>}</>
          : <div className="empty-result"><strong>모델명만 있어도 확인할 수 있습니다</strong><p>내용을 고치면 다시 확인해야 합니다.</p></div>}
      </section>
      <aside className="panel vault"><p className="eyebrow">내 제품</p><h2>내 제품 <span className="count">{products.length}</span></h2><p className="small">{user ? '저장한 제품과 지난 확인 기록입니다.' : '로그인하면 제품을 저장하고 새 리콜 알림을 받을 수 있습니다.'}</p>
        {products.length === 0 && <div className="vault-empty"><span aria-hidden="true">＋</span><strong>한 번 저장해두면</strong><p>새로운 리콜이 나왔을 때 다시 확인할 수 있어요.</p></div>}
        {products.map(product => <article className="saved-product" key={product.id}><span className="tag">{product.input.demo ? '가상 제품' : '저장한 제품'} · 지난 결과</span><h3>{product.input.model}</h3><p>{product.input.brand}</p><p className="small">마지막 확인: {instant(product.latest_result.checked_at)}<br />그때 결과: {product.latest_result.label}</p><Risk risk={product.latest_result.risk} /><label>조치 상태<select aria-label={`${product.input.model} 조치 상태`} disabled={busy} value={product.action_state} onChange={e => action(product, e.target.value)}>{Object.entries(actionLabels).map(([state, label]) => <option value={state} key={state}>{label}</option>)}</select></label><p className="small">내가 직접 남기는 기록입니다. 제조사의 회수 확인이나 사용 허가가 아닙니다.</p><div className="vault-actions"><button className="secondary" disabled={busy} onClick={() => selectProduct(product)}>입력 불러오기</button><button className="text-button" disabled={busy} onClick={() => run(async () => setHistory(await guardedApi(`products/${product.id}/history`)))}>확인 이력</button><button className="text-button danger" disabled={busy} onClick={() => remove(product)}>삭제</button></div></article>)}
        {user && <section className="notifications"><h3>알림 <span className="count">{notifications.filter(n => !n.read_at).length}</span></h3><p className="small">{user.notification_consent ? '내 제품의 확인 결과가 바뀌면 여기에 표시됩니다.' : '계정 관리에서 알림을 켜면 결과가 바뀔 때 여기에 표시됩니다.'}</p>{notifications.length === 0 && <p className="small">아직 알림이 없습니다.</p>}{notifications.map(n => <article key={n.id} className="notification"><strong>{n.title || '내 제품의 확인 결과 변경'}</strong><p>{n.message || n.body}</p><small>{instant(n.created_at)}</small>{!n.read_at && <button className="text-button" disabled={busy} onClick={() => run(async () => { await guardedApi(`notifications/${n.id}/read`, 'PATCH', {}); setNotifications(await guardedApi('notifications')); })}>읽음으로 표시</button>}</article>)}</section>}
      </aside></div>
      {history && <section className="panel history"><div className="section-heading"><h2>확인 이력</h2><button className="text-button" onClick={() => setHistory(null)}>이력 닫기</button></div>{history.checks.map(check => <div key={check.id}><p>{check.input.brand} / {check.input.model} · 제조일 {check.input.manufactured_on || '모름'} · LOT {check.input.lot || '모름'}</p><Result result={check.result} historical /></div>)}<h3>조치 기록</h3><ul>{history.actions.map((a, i) => <li key={i}>{actionLabels[a.state]} · {instant(a.recorded_at)}</li>)}</ul></section>}
      <Assistant key={`assistant-${user?.id || 'anonymous'}`} user={user} status={meta?.assistant} seed={askSeed} onConsent={consent => run(async () => { setUser(await guardedApi('auth/preferences', 'PATCH', { ai_consent: consent })); setMessage(consent ? 'AI 도우미 이용에 동의했습니다.' : 'AI 도우미 동의를 해제했습니다. 이후 질문은 전송되지 않습니다.'); })} />
      <Account key={user?.id || "anonymous"} user={user} busy={busy} onAuth={(register, data) => run(async () => { const generation = ++accountGeneration.current; const account = await guardedApi(`auth/${register ? 'register' : 'login'}`, 'POST', data); if (generation !== accountGeneration.current) return; setUser(account); accountChannel.current?.postMessage('changed'); await loadPrivate(); setMessage('로그인했습니다.'); })} onLogout={() => run(async () => { await guardedApi('auth/logout', 'POST', {}); clearPrivate(); accountChannel.current?.postMessage('changed'); setMessage('로그아웃했습니다.'); })} onPreferences={consent => run(async () => { setUser(await guardedApi('auth/preferences', 'PATCH', { notification_consent: consent })); setMessage(consent ? '앱내 알림을 켰습니다.' : '앱내 알림을 껐습니다.'); })} onExport={() => run(async () => { const data = await guardedApi('auth/export'); const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })); const a = document.createElement('a'); a.href = url; a.download = 'batterycheck-my-records.json'; a.click(); URL.revokeObjectURL(url); })} onDelete={password => run(async () => { await guardedApi('auth/account', 'DELETE', { password }); clearPrivate(); accountChannel.current?.postMessage('changed'); setMessage('계정과 모든 기록을 삭제했습니다.'); })} />
      <section className="source-status panel" aria-label="정보 출처"><div className="account-bar"><div><p className="eyebrow">정보 출처</p><h2>어디서 확인하나요?</h2></div><button className="secondary" disabled={busy || !user} onClick={sync}>최신 정보 다시 확인</button></div><p className="small">공식 기관의 리콜 공지만 사용합니다. 해외 공지는 국내에서의 회수·교환을 보장하지 않습니다.{!user && ' 최신 정보 다시 확인은 로그인 후 할 수 있습니다.'}</p>
        {sourceItems.length > 0 && <div className="source-grid">{sourceItems.map((s, i) => <div key={s.id || i}><strong>{s.name || s.id}</strong>{s.state === 'unconfigured' ? <small>{s.scope}</small> : <><small>{sourceLabels[s.state || s.status] || '상태 확인 필요'} · {s.scope}</small><small>마지막 업데이트: {instant(s.last_success_at || s.fetched_at)}</small>{s.error && <small className="warning">{s.error}</small>}</>}</div>)}</div>}
        <div className="source-links"><a href="https://www.cpsc.gov/Recalls" target="_blank" rel="noreferrer">미국 CPSC 리콜 공지 ↗</a><a href="https://www.safetykorea.kr/" target="_blank" rel="noreferrer">Safety Korea에서 직접 확인 ↗</a></div>
      </section>
      <section className="facts" aria-label="확인 범위"><h2>배터리체크가 확인하는 범위</h2><dl><div><dt>{sourceStatus?.notice_count ?? '–'}</dt><dd>저장된 공식 리콜 공지</dd></div><div><dt>{safety.sources.length}</dt><dd>공식 안전 안내 출처</dd></div><div><dt>{Object.keys(signLabels).length}</dt><dd>이상 증상 확인 항목</dd></div><div><dt>0</dt><dd>서버에 저장하는 사진</dd></div></dl></section>
      <Feedback />
      <footer><p>AI 도우미의 답변은 AI가 만든 것입니다. 정확한 내용은 함께 표시되는 공식 자료에서 확인하세요.</p><p>리콜 대상인지와 실제 회수·교환 방법은 공식 공지와 제조사에서 최종 확인하세요. 안전 안내는 소방청 등 공식 자료를 바탕으로 했으며 전문가 검토는 아직 받지 않았습니다.</p><p>개인정보: 계정·내 제품·확인 기록·AI 대화는 계정을 삭제할 때까지 보관합니다. AI 도우미에 동의한 경우에만 질문과 제품 정보를 외부 AI API에 보냅니다. 사진 원본은 저장하지 않습니다.</p></footer>
    </main></>;
}
