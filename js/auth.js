// 아이디·비밀번호 로그인을 서버(/api/auth)와 주고받는 공통 파일입니다. 로그인 표는 서버가 주는 쿠키에만 있어요.
import { saveViewerName } from '../modules/rankingStore.js';

let currentUser = null;

export async function requestApi(path, payload) {
  let response;
  try {
    response = await fetch(path, payload === undefined ? { cache: 'no-store' } : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
    });
  } catch {
    throw new Error('서버에 연결하지 못했어요. 서버가 켜져 있는지 확인해 주세요.');
  }
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.error || '문제가 생겼어요. 다시 시도해 주세요.');
  return result;
}

async function call(path, payload) { return requestApi(`/api/auth/${path}`, payload); }

// 랭킹과 옷 상세 창은 저장된 '내 이름'을 써요. 로그인 상태와 항상 같게 맞춥니다.
function remember(user) {
  currentUser = user;
  saveViewerName(user ? user.name : '');
  return user;
}

export function getUser() { return currentUser; }
export async function fetchMe() {
  try { return remember((await call('me')).user); } catch { return remember(null); }
}
export async function signup(username, password, name, teacherCode = '') {
  const user = (await call('signup', { username, password, name, ...(teacherCode ? { teacherCode } : {}) })).user;
  return remember(user);
}
export async function login(username, password) { return remember((await call('login', { username, password })).user); }
export async function authAction(action, payload) { return call(action, payload); }
export async function changeOwnPassword(currentPassword, newPassword) {
  const result = await call('change-password', { currentPassword, newPassword });
  remember(null);
  window.dispatchEvent(new Event('fitshare:auth-changed'));
  return result;
}
export async function logout() { try { await call('logout', {}); } finally { remember(null); } }
