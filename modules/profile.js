// 내페이지와 공개 프로필입니다. 나눔 계산·저장 규칙은 기존 모듈을 사용해요.
import {
  AVATAR_OPTIONS, BADGE_ICONS, defaultAvatar, ensureUser, finalizeMonths, formatMonth,
  getProfile, sanitizeAvatar, setAvatar
} from './rankingLogic.js';
import { loadState, loadViewerName, mutateState, pullState, subscribe } from './rankingStore.js';
import { authAction, changeOwnPassword, getUser } from '../js/auth.js?v=auth-3';
import { createMySharing, reviewsHtml } from './mySharing.js';

const CSS_ID = 'ranking-style';
let activeDialog = null;
const escapeHtml = text => String(text ?? '').replace(/[&<>"']/g,char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));

export function ensureStyle() {
  if (document.getElementById(CSS_ID)) return;
  const link = document.createElement('link');
  link.id = CSS_ID; link.rel = 'stylesheet'; link.href = new URL('../css/ranking.css?v=ranking-10',import.meta.url).href;
  document.head.append(link);
}

export function avatarHtml(avatar,size=40) {
  const clean = sanitizeAvatar(avatar) ?? defaultAvatar();
  const pixels = Number.isFinite(Number(size)) ? Math.max(16,Math.min(120,Number(size))) : 40;
  return `<span class="avatar" role="img" aria-label="아바타" style="--av-bg:${clean.bg};--av-size:${pixels}px"><span class="av-face">${clean.face}</span>${clean.hat ? `<span class="av-hat">${clean.hat}</span>` : ''}${clean.outfit ? `<span class="av-outfit">${clean.outfit}</span>` : ''}</span>`;
}

function badgeHtml(profile) {
  return profile.badges.length ? `<ul class="rk-badges">${profile.badges.map(badge => `<li title="${formatMonth(badge.month)} ${badge.rank}위">${BADGE_ICONS[badge.rank]}<span>${formatMonth(badge.month)} ${badge.rank}위</span></li>`).join('')}</ul>` : '<p class="rk-empty">아직 배지가 없어요. 한 달 순위 1·2·3위가 되면 받아요!</p>';
}

function statsHtml(profile) {
  return `<dl class="rk-stats"><div><dt>나눔한 횟수</dt><dd>${profile.donations}번</dd></div><div><dt>수령한 횟수</dt><dd>${profile.receipts}번</dd></div><div><dt>이번 달 포인트</dt><dd>${profile.monthPoints}점${profile.monthRank ? ` · ${profile.monthRank}위` : ''}</dd></div></dl>`;
}

function createDialog(classes,label) {
  activeDialog?.close();
  ensureStyle();
  const dialog = document.createElement('dialog');
  dialog.className = `profile-dialog ${classes}`;
  dialog.setAttribute('aria-label',label);
  document.body.append(dialog);
  activeDialog = dialog;
  return dialog;
}

function showDialog(dialog) {
  if (typeof dialog.showModal === 'function') dialog.showModal();
  else dialog.setAttribute('open','');
}

// 5초 동기화와 다른 탭 변경을 한 곳에서 관리하고 닫히거나 제거되면 정리해요.
function watchState(dialog,onState,isCurrent=()=>true,onDetached=()=>{}) {
  let stopped = false,inFlight = false,version = 0;
  const alive = () => !stopped && dialog.isConnected && dialog.open;
  const apply = state => {
    if (!alive()) return;
    if (!isCurrent()) { dialog.close(); return; }
    onState(finalizeMonths(state));
  };
  const reload = () => apply(loadState());
  const sync = async () => {
    if (!alive() || inFlight || document.hidden) return;
    if (!isCurrent()) { dialog.close(); return; }
    inFlight = true;
    const requestVersion = ++version;
    try { const state = await pullState(); if (state && requestVersion === version) apply(state); }
    finally { inFlight = false; }
  };
  const unsubscribe = subscribe(reload);
  const timer = setInterval(sync,5000);
  const onVisible = () => { if (!document.hidden) sync(); };
  document.addEventListener('visibilitychange',onVisible);
  const observer = new MutationObserver(() => { if (!dialog.isConnected) { dispose(); onDetached(); } });
  observer.observe(document.body,{childList:true,subtree:true});
  function dispose() {
    if (stopped) return;
    stopped = true; version++;
    clearInterval(timer); unsubscribe(); observer.disconnect();
    document.removeEventListener('visibilitychange',onVisible);
  }
  dialog.addEventListener('close',dispose,{once:true});
  return {sync,dispose,invalidate(){version++;}};
}

export function openProfileDialog(onSaved) {
  const dialog = createDialog('my-page-dialog','내페이지');
  const name = loadViewerName().trim();
  let closed = false,shownState = finalizeMonths(loadState()),draft = defaultAvatar(),touched = false,draftRevision = 0,busy = false,watcher,sharing;
  const currentName = () => loadViewerName().trim().toLowerCase() === name.toLowerCase();
  const alive = () => !closed && dialog.isConnected && dialog.open && currentName();
  const withMe = state => ensureUser(finalizeMonths(state),name);
  const viewerIdIn = state => {
    if (!currentName()) throw new RangeError('로그인한 계정이 바뀌었어요. 내페이지를 다시 열어 주세요.');
    const user = state.users.find(item => item.name.toLowerCase() === name.toLowerCase());
    if (!user) throw new RangeError('내 나눔 기록을 찾지 못했어요. 내페이지를 다시 열어 주세요.');
    return user.id;
  };
  const message = (selector,text,isError=false) => {
    const target = dialog.querySelector(selector);
    if (!target || closed) return;
    target.textContent = text; target.classList.toggle('is-error',isError);
  };
  function cleanup() {
    if (closed) return;
    closed = true; watcher?.dispose(); sharing?.dispose();
    dialog.querySelectorAll('[data-account-action]').forEach(form => form.reset());
    dialog.remove();
    if (activeDialog === dialog) activeDialog = null;
  }
  dialog.addEventListener('close',cleanup,{once:true});
  dialog.addEventListener('click',event => { if (event.target.closest('[data-close]')) dialog.close(); });

  if (!name) {
    dialog.innerHTML = '<h2>내페이지</h2><p class="rk-empty">로그인한 뒤에 내페이지를 볼 수 있어요.</p><div class="garment-detail-buttons"><button type="button" class="rk-ghost" data-close>닫기</button></div>';
    showDialog(dialog);
    return dialog;
  }

  const teacher = getUser()?.role === 'teacher';
  const account = getUser();
  dialog.innerHTML = `
    <div class="mypage-header"><h2>🙋 내페이지</h2><button type="button" class="rk-ghost" data-close>닫기</button></div>
    <section class="mypage-section" data-profile-summary></section>
    <p id="rk-message" class="rk-message" role="status" aria-live="polite"></p>
    <div data-my-sharing></div>
    <details class="mypage-section rk-box" data-avatar-editor><summary>🎨 아바타 꾸미기</summary>
      <div data-avatar-choices>${['bg','face','hat','outfit'].map(key => `<p class="rk-label">${{bg:'배경색',face:'얼굴',hat:'모자·머리',outfit:'옷'}[key]}</p><div class="avatar-choices" data-key="${key}">${AVATAR_OPTIONS[key].map(value => key === 'bg' ? `<button type="button" class="avatar-swatch" data-value="${value}" style="background:${value}" aria-label="배경색 ${value}"></button>` : `<button type="button" class="avatar-pick" data-value="${value}">${value || '없음'}</button>`).join('')}</div>`).join('')}</div>
      <p id="pf-message" class="garment-detail-message" role="status"></p>
      <div class="garment-detail-buttons"><button type="button" class="rk-ghost" data-reset>되돌리기</button><button type="button" class="primary-action" data-save>아바타 저장</button></div>
    </details>
    <section class="mypage-section rk-box" data-account-settings><h3>계정 설정</h3><p class="rk-label">${escapeHtml(name)}${account?.username ? ` · 아이디 ${escapeHtml(account.username)}` : ''}</p>
      <section class="teacher-profile-controls"><h3 class="profile-sub">내 비밀번호</h3><form data-account-action="change-own-password"><label>현재 비밀번호<input name="currentPassword" type="password" maxlength="200" autocomplete="current-password" required></label><label>새 비밀번호<input name="newPassword" type="password" maxlength="200" autocomplete="new-password" required></label><label>새 비밀번호 다시 입력<input name="confirmPassword" type="password" maxlength="200" autocomplete="new-password" required></label><button class="primary-action" type="submit">내 비밀번호 바꾸기</button></form></section>
      ${teacher ? `<section class="teacher-profile-controls"><h3 class="profile-sub">선생님 설정</h3><form data-account-action="teacher-code"><label>새 선생님 인증 코드<input name="newCode" type="password" maxlength="200" autocomplete="new-password" required></label><button class="primary-action" type="submit">인증 코드 바꾸기</button></form><form data-account-action="reset-student-password"><label>학생 아이디<input name="username" maxlength="200" required></label><label>새 비밀번호<input name="newPassword" type="password" maxlength="200" autocomplete="new-password" required></label><button class="primary-action" type="submit">학생 비밀번호 바꾸기</button></form></section>` : '<p class="rk-empty">계정 이름으로 내 나눔 기록을 확인할 수 있어요.</p>'}
      <p id="pf-account-message" class="garment-detail-message" role="status" aria-live="polite"></p>
    </section>
    <div class="garment-detail-buttons"><button type="button" class="rk-ghost" data-close>닫기</button></div>`;

  function renderAvatar() {
    dialog.querySelector('#pf-preview').innerHTML = avatarHtml(draft,84);
    dialog.querySelectorAll('.avatar-choices button').forEach(button => {
      const picked = draft[button.parentElement.dataset.key] === button.dataset.value;
      button.classList.toggle('is-picked',picked); button.setAttribute('aria-pressed',String(picked));
    });
    const saved = sanitizeAvatar(withMe(shownState).user.avatar) ?? defaultAvatar();
    dialog.querySelector('[data-reset]').disabled = JSON.stringify(saved) === JSON.stringify(draft);
  }

  function render(state) {
    shownState = finalizeMonths(state);
    const ensured = withMe(shownState),profile = getProfile(ensured.state,ensured.user.id);
    if (!touched) draft = sanitizeAvatar(profile.user.avatar) ?? defaultAvatar();
    dialog.querySelector('[data-profile-summary]').innerHTML = `<div class="profile-head"><span id="pf-preview">${avatarHtml(draft,84)}</span><div><p class="rk-profile-name">${escapeHtml(profile.user.name)}</p><span class="profile-role${teacher ? ' is-teacher' : ''}">${teacher ? '선생님' : '학생'}</span></div></div>${statsHtml(profile)}<p class="rk-label">받은 우승 배지</p>${badgeHtml(profile)}`;
    sharing.render(ensured.state,ensured.user.id);
    renderAvatar();
  }

  async function perform(action,okText,button,{selector='#rk-message',afterResult}={}) {
    if (busy || !alive()) return false;
    busy = true; if (button) button.disabled = true;
    watcher?.invalidate();
    try {
      const result = await mutateState(state => finalizeMonths(action(state)));
      // 저장 중 창을 닫아도 같은 계정의 헤더·순위에는 저장 결과를 알려 줘요.
      if (!alive()) {
        if (currentName() && typeof onSaved === 'function') onSaved(result.state);
        return true;
      }
      watcher?.invalidate();
      afterResult?.(result);
      render(result.state);
      message(selector,result.shared ? okText : result.saved ? '서버에 닿지 않아 이 브라우저에만 저장했어요. 다른 기기의 친구에게는 아직 보이지 않아요.' : '기록을 저장하지 못했어요. 새로고침하면 사라질 수 있어요.',!result.shared);
      if (typeof onSaved === 'function') onSaved(result.state);
      return true;
    } catch (error) {
      if (alive()) message(selector,error.message || '문제가 생겼어요. 다시 시도해 주세요.',true);
      return false;
    } finally { busy = false; if (button?.isConnected) button.disabled = false; }
  }
  sharing = createMySharing(dialog.querySelector('[data-my-sharing]'),{name,getViewerId:viewerIdIn,onAction:perform});
  render(shownState);

  dialog.addEventListener('click',event => {
    const pick = event.target.closest('.avatar-choices button');
    if (pick) {
      touched = true; draftRevision++;
      draft = {...draft,[pick.parentElement.dataset.key]:pick.dataset.value};
      renderAvatar();
    } else if (event.target.closest('[data-reset]')) {
      touched = false; draftRevision++;
      draft = sanitizeAvatar(withMe(shownState).user.avatar) ?? defaultAvatar();
      renderAvatar();
    } else if (event.target.closest('[data-save]')) {
      const chosen = {...draft},revision = draftRevision;
      perform(state => { const ensured = ensureUser(state,name); return setAvatar(ensured.state,ensured.user.id,chosen).state; },'아바타를 저장했어요!',event.target.closest('[data-save]'),{selector:'#pf-message',afterResult(){if (revision === draftRevision) { draft = chosen; touched = false; }}});
    }
  });

  dialog.addEventListener('submit',async event => {
    const form = event.target.closest('[data-account-action]');
    if (!form) return;
    event.preventDefault();
    if (!alive()) return;
    const ownPasswordChange = form.dataset.accountAction === 'change-own-password';
    if (!ownPasswordChange && getUser()?.role !== 'teacher') return;
    const button = form.querySelector('button[type="submit"]');
    if (button.disabled) return;
    const values = Object.fromEntries(new FormData(form));
    if (ownPasswordChange && values.newPassword !== values.confirmPassword) {
      message('#pf-account-message','새 비밀번호가 서로 달라요. 다시 확인해 주세요.',true);
      return;
    }
    button.disabled = true; message('#pf-account-message','저장하고 있어요.');
    try {
      if (ownPasswordChange) {
        await changeOwnPassword(values.currentPassword,values.newPassword);
        dialog.close();
        return;
      }
      await authAction(form.dataset.accountAction,values);
      if (!alive()) return;
      form.reset();
      message('#pf-account-message',form.dataset.accountAction === 'teacher-code' ? '새 인증 코드로 바꿨어요.' : '학생 비밀번호를 바꿨어요. 이전 로그인은 모두 종료됐어요.');
    } catch (error) { if (alive()) message('#pf-account-message',error.message || '저장하지 못했어요. 다시 시도해 주세요.',true); }
    finally { if (button.isConnected) button.disabled = false; }
  });
  showDialog(dialog);
  watcher = watchState(dialog,render,currentName,cleanup);
  watcher.sync();
  return dialog;
}

// 다른 사용자의 공개 정보만 보여 주며 거래·설정·아바타 수정은 포함하지 않아요.
export function openMemberProfileDialog(userId) {
  const dialog = createDialog('member-profile-dialog public-profile-dialog','공개 프로필');
  dialog.innerHTML = '<div class="mypage-header"><h2>공개 프로필</h2><button type="button" class="rk-ghost" data-close>닫기</button></div><div data-member-profile></div>';
  const render = state => {
    const container = dialog.querySelector('[data-member-profile]');
    if (!state.users.some(user => user.id === userId)) { container.innerHTML = '<p class="rk-empty">친구의 프로필을 찾지 못했어요. 순위 목록을 다시 확인해 주세요.</p>'; return; }
    const profile = getProfile(finalizeMonths(state),userId);
    container.innerHTML = `<div class="profile-head">${avatarHtml(profile.user.avatar,72)}<p class="rk-profile-name">${escapeHtml(profile.user.name)}</p></div>${statsHtml(profile)}<h3 class="profile-sub">받은 우승 배지</h3>${badgeHtml(profile)}<h3 class="profile-sub">받은 후기</h3>${reviewsHtml(state,userId)}`;
  };
  dialog.addEventListener('click',event => { if (event.target.closest('[data-close]')) dialog.close(); });
  let watcher;
  const cleanup = () => { watcher?.dispose(); dialog.remove(); if (activeDialog === dialog) activeDialog = null; };
  dialog.addEventListener('close',cleanup,{once:true});
  render(finalizeMonths(loadState())); showDialog(dialog);
  watcher = watchState(dialog,render,()=>true,cleanup); watcher.sync();
  return dialog;
}
