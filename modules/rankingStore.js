// 랭킹 기록을 저장하고, 바뀌면 바로 알려 주는 파일입니다.
// 기록은 server.py(/api/ranking)에 한 벌만 두어 여러 기기의 친구가 같은 나눔·예약·후기를 봅니다.
// 서버에 닿지 않으면 이 브라우저(localStorage)에만 저장하고, 화면은 계속 쓸 수 있어요.
import { createEmptyState, normalizeState } from './rankingLogic.js';

const STATE_KEY = 'fitshare.ranking.v1';
const VIEWER_KEY = 'fitshare.ranking.viewer';
const VIEWER_NAME_KEY = 'fitshare.ranking.viewerName';
const REV_KEY = 'fitshare.ranking.rev';
const CHANNEL = 'fitshare-ranking';

function readStorage(key) {
  try { return window.localStorage.getItem(key); } catch { return null; }
}
function writeStorage(key, value) {
  try { window.localStorage.setItem(key, value); return true; } catch { return false; }
}

export function loadState() {
  const raw = readStorage(STATE_KEY);
  if (!raw) return createEmptyState();
  try { return normalizeState(JSON.parse(raw)); } catch { return createEmptyState(); }
}

// 저장에 실패해도(용량 부족, 시크릿 모드) 화면은 계속 쓸 수 있게 true/false만 돌려줍니다.
export function saveState(state) {
  const saved = writeStorage(STATE_KEY, JSON.stringify(state));
  if (saved) {
    try { new BroadcastChannel(CHANNEL).postMessage('changed'); } catch { /* 지원하지 않는 브라우저 */ }
  }
  return saved;
}

export function loadViewerId() { return readStorage(VIEWER_KEY) || ''; }
export function saveViewerId(id) { writeStorage(VIEWER_KEY, id); }

// 다른 탭에서 기록이 바뀌면 callback 을 부릅니다. 돌려주는 함수를 부르면 듣기를 멈춰요.
export function subscribe(callback) {
  const onStorage = (event) => { if (event.key === STATE_KEY || event.key === null) callback(); };
  window.addEventListener('storage', onStorage);
  let channel = null;
  try {
    channel = new BroadcastChannel(CHANNEL);
    channel.onmessage = () => callback();
  } catch { /* storage 이벤트만 사용 */ }
  return () => {
    window.removeEventListener('storage', onStorage);
    if (channel) channel.close();
  };
}

// 내 이름은 이 브라우저에 기억해요. (옷장과 랭킹 탭이 함께 씁니다.)
export function loadViewerName() { return readStorage(VIEWER_NAME_KEY) || ''; }
export function saveViewerName(name) { writeStorage(VIEWER_NAME_KEY, name); }

function cacheState(state, rev) {
  writeStorage(STATE_KEY, JSON.stringify(state));
  writeStorage(REV_KEY, String(rev));
}
function cachedRev() { return Number(readStorage(REV_KEY)) || 0; }

// 서버의 최신 기록을 받아 와요. 서버에 닿지 않으면 null 을 돌려주고 이 브라우저의 기록을 그대로 씁니다.
export async function pullState() {
  try {
    const response = await fetch('/api/ranking', { cache: 'no-store' });
    if (!response.ok) return null;
    const result = await response.json();
    if (result.state) {
      const state = normalizeState(result.state);
      cacheState(state, result.rev);
      return state;
    }
    // 서버에 아직 기록이 없으면 이 브라우저에 있던 기록을 처음 기록으로 올려요.
    writeStorage(REV_KEY, String(result.rev ?? 0));
    return loadState();
  } catch { return null; }
}

// 최신 기록에 action(state) 을 적용해 저장합니다. 그 사이 다른 친구가 저장했으면(409) 최신 기록으로 다시 적용해요.
// action 이 RangeError 를 던지면 그대로 올려 보내고, 저장에 실패해도 바뀐 기록은 돌려줍니다.
export async function mutateState(action) {
  let base = loadState();
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const next = action(base);
    let response;
    try {
      response = await fetch('/api/ranking', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ baseRev: cachedRev(), state: next })
      });
    } catch {
      return { state: next, saved: saveState(next), shared: false };
    }
    if (response.ok) {
      const result = await response.json().catch(() => ({}));
      cacheState(next, result.rev ?? cachedRev() + 1);
      try { new BroadcastChannel(CHANNEL).postMessage('changed'); } catch { /* 지원하지 않는 브라우저 */ }
      return { state: next, saved: true, shared: true };
    }
    if (response.status !== 409) return { state: next, saved: saveState(next), shared: false };
    const latest = await response.json().catch(() => ({}));
    base = latest.state ? normalizeState(latest.state) : base;
    writeStorage(REV_KEY, String(latest.rev ?? cachedRev()));
  }
  throw new RangeError('다른 친구들이 동시에 저장하고 있어요. 잠시 뒤에 다시 눌러 주세요.');
}

// 교사 전달 완료는 서버가 교사 권한과 예약 상태를 직접 확인한 뒤 저장합니다.
export async function markTeacherHandoffShared(exchangeId) {
  for (let attempt = 0; attempt < 4; attempt += 1) {
    let response;
    try {
      response = await fetch('/api/ranking/teacher-handoff', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ exchangeId, baseRev: cachedRev() })
      });
    } catch {
      throw new Error('서버에 연결하지 못해 전달 완료를 저장하지 못했어요. 다시 연결한 뒤 시도해 주세요.');
    }
    const result = await response.json().catch(() => ({}));
    if (response.ok && result.state) {
      const state = normalizeState(result.state);
      cacheState(state, result.rev ?? cachedRev() + 1);
      try { new BroadcastChannel(CHANNEL).postMessage('changed'); } catch { /* 지원하지 않는 브라우저 */ }
      return { state, shared: true };
    }
    if (response.status !== 409 || !result.state) throw new RangeError(result.error || '전달 완료를 저장하지 못했어요. 다시 시도해 주세요.');
    cacheState(normalizeState(result.state), result.rev ?? cachedRev());
  }
  throw new RangeError('다른 친구들이 동시에 저장하고 있어요. 새로고침한 뒤 다시 눌러 주세요.');
}
