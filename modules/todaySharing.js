// 선생님이 예약된 옷의 전달 대상을 확인하고 전달 완료를 기록하는 화면입니다.
import { getUser } from '../js/auth.js?v=auth-3';
import { loadState, markTeacherHandoffShared, pullState, subscribe } from './rankingStore.js';

const STYLE_ID = 'today-sharing-style';
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[char]));

function ensureStyle() {
  if (document.getElementById(STYLE_ID)) return;
  const link = document.createElement('link');
  link.id = STYLE_ID;
  link.rel = 'stylesheet';
  link.href = new URL('../css/ranking.css?v=teacher-handoff-1', import.meta.url).href;
  document.head.append(link);
}

function displayDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '시간 정보 없음' : new Intl.DateTimeFormat('ko-KR', { dateStyle: 'medium', timeStyle: 'short' }).format(date);
}

function localDateKey(value = new Date()) {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`;
}

export function openTodaySharingDialog() {
  if (getUser()?.role !== 'teacher') return null;
  ensureStyle();
  const previousDialog = document.querySelector('#today-sharing-dialog');
  if (previousDialog?.open) previousDialog.close();
  else previousDialog?.remove();
  const dialog = document.createElement('dialog');
  dialog.id = 'today-sharing-dialog';
  dialog.className = 'profile-dialog today-sharing-dialog';
  dialog.setAttribute('aria-label', '오늘의 나눔');
  dialog.innerHTML = `<div class="mypage-header"><div><p class="today-sharing-eyebrow">선생님 전달 확인</p><h2>📦 오늘의 나눔</h2></div><button type="button" class="rk-ghost" data-close>닫기</button></div>
    <p class="today-sharing-intro">예약된 옷과 받을 친구를 확인하고, 직접 전달한 뒤 완료로 표시해 주세요.</p>
    <p class="rk-message" data-message role="status" aria-live="polite"></p>
    <section aria-labelledby="today-pending-title"><h3 id="today-pending-title" class="today-sharing-section-title">전달할 나눔</h3><div data-pending-list></div></section>
    <section aria-labelledby="today-completed-title"><h3 id="today-completed-title" class="today-sharing-section-title">오늘 전달 완료</h3><div data-completed-list></div></section>`;
  document.body.append(dialog);

  let garmentsById = new Map();
  const render = state => {
    const users = new Map(state.users.map(user => [user.id, user.name]));
    const pending = state.exchanges.filter(item => item.status === 'reserved').sort((a,b) => String(a.createdAt).localeCompare(String(b.createdAt)));
    const today = localDateKey();
    const completed = state.exchanges.filter(item => item.status === 'completed' && item.completedAt && localDateKey(new Date(item.completedAt)) === today)
      .sort((a,b) => String(b.completedAt).localeCompare(String(a.completedAt)));
    const garmentInfo = item => {
      const garment = garmentsById.get(item.garmentId);
      return {
        code: garment?.garmentCode || item.itemName,
        donor: garment?.donorId || users.get(item.giverId) || '아이디를 확인할 수 없음'
      };
    };
    dialog.querySelector('[data-pending-list]').innerHTML = pending.length ? pending.map(item => {
      const garment = garmentInfo(item);
      return `<article class="today-sharing-card">
      <div class="today-sharing-card-top"><span class="today-sharing-status">전달 대기</span><time>${escapeHtml(displayDate(item.createdAt))} 예약</time></div>
      <h4>👕 ${escapeHtml(garment.code)}</h4><p class="today-sharing-garment-name">옷 이름: ${escapeHtml(item.itemName)}</p><p>기부자 <b>${escapeHtml(garment.donor)}</b> 학생의 옷을 <b>${escapeHtml(users.get(item.receiverId) || '이름을 확인할 수 없음')}</b> 학생에게 전달해요.</p>
      <button type="button" class="today-sharing-complete" data-complete="${escapeHtml(item.id)}">나눔 완료</button>
    </article>`;
    }).join('') : '<p class="rk-empty">새 나눔 예약이 생기면 이곳에 표시돼요.</p>';
    dialog.querySelector('[data-completed-list]').innerHTML = completed.length ? completed.map(item => {
      const garment = garmentInfo(item);
      return `<article class="today-sharing-card is-done">
      <div class="today-sharing-card-top"><span class="today-sharing-status">✅ 전달 완료</span><time>${escapeHtml(displayDate(item.completedAt))}</time></div>
      <h4>👕 ${escapeHtml(garment.code)}</h4><p class="today-sharing-garment-name">옷 이름: ${escapeHtml(item.itemName)}</p><p>기부자 <b>${escapeHtml(garment.donor)}</b> 학생 → 받는 학생 <b>${escapeHtml(users.get(item.receiverId) || '이름을 확인할 수 없음')}</b></p>
    </article>`;
    }).join('') : '<p class="rk-empty">오늘 전달을 완료한 옷이 아직 없어요.</p>';
  };
  render(loadState());
  const unsubscribe = subscribe(() => render(loadState()));
  let syncing = false;
  const sync = async () => {
    if (syncing || document.hidden || !dialog.open) return;
    syncing = true;
    try {
      const [state, response] = await Promise.all([
        pullState(), fetch('/api/garments', { cache: 'no-store' }).catch(() => null)
      ]);
      if (response?.ok) {
        const items = await response.json().catch(() => []);
        if (Array.isArray(items)) garmentsById = new Map(items.filter(item => item?.id).map(item => [item.id, item]));
      }
      if (dialog.open) render(state || loadState());
    } finally { syncing = false; }
  };
  const timer = setInterval(sync, 5000);
  const onVisible = () => { if (!document.hidden) sync(); };
  document.addEventListener('visibilitychange', onVisible);
  const cleanup = () => { clearInterval(timer); unsubscribe(); document.removeEventListener('visibilitychange', onVisible); dialog.remove(); };
  dialog.addEventListener('close', cleanup, { once: true });
  dialog.addEventListener('click', async event => {
    if (event.target.closest('[data-close]')) { dialog.close(); return; }
    const button = event.target.closest('[data-complete]');
    if (!button) return;
    if (getUser()?.role !== 'teacher') { dialog.querySelector('[data-message]').textContent = '선생님 계정에서만 완료로 표시할 수 있어요.'; return; }
    button.disabled = true;
    dialog.querySelector('[data-message]').textContent = '전달 기록을 저장하고 있어요.';
    try {
      const result = await markTeacherHandoffShared(button.dataset.complete);
      render(result.state);
      const message = dialog.querySelector('[data-message]');
      message.textContent = result.shared ? '전달 완료를 저장했어요.' : '서버와 연결되지 않아 이 브라우저에만 저장했어요.';
      message.classList.toggle('is-error', !result.shared);
    } catch (error) {
      if (dialog.open) {
        const message = dialog.querySelector('[data-message]');
        message.textContent = error.message || '완료 기록을 저장하지 못했어요. 다시 시도해 주세요.';
        message.classList.add('is-error');
      }
    } finally { if (button.isConnected) button.disabled = false; }
  });
  if (typeof dialog.showModal === 'function') dialog.showModal(); else dialog.setAttribute('open', '');
  sync();
  return dialog;
}
