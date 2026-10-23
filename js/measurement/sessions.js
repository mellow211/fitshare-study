import { listExperiments } from './store.js?v=legacy-session-fallback-1';

const root = document.querySelector('#sessions-app');

listExperiments().then(renderSessions).catch((error) => renderError(error.message));

function renderSessions(sessions) {
  if (!sessions.length) {
    root.innerHTML = `<section class="measurement-panel"><p class="eyebrow">저장된 실험 기록</p><h1>아직 실험이 없어요</h1><p class="kid-help">새 실험을 시작하면 서버에 회차 번호와 사진이 저장돼요.</p><a class="primary-action" href="../measure/index.html">첫 실험 시작하기</a></section>`;
    return;
  }
  root.innerHTML = `
    <section class="measurement-panel">
      <p class="eyebrow">FITSHARE 저장 기록</p>
      <h1>어느 실험을 볼까요?</h1>
      <p class="kid-help">실험할 때마다 회차가 하나씩 늘어요. 보고 싶은 회차를 누르면 그때 찍은 사진과 결과가 열려요.</p>
      <div class="session-list">${sessions.map((session) => `
        <a class="session-row" href="index.html?run=${encodeURIComponent(session.id)}">
          <span class="session-round">${session.round}회차</span>
          <span class="session-info"><strong>${formatDate(session.createdAt)}</strong><small>옷 ${session.garmentCount}벌 · 저장 사진 ${session.recordCount}장</small></span>
          <span class="session-status ${session.completed ? 'is-complete' : ''}">${session.completed ? '완료' : '진행 중'}</span>
          <span class="session-arrow" aria-hidden="true">→</span>
        </a>`).join('')}</div>
      <div class="button-row"><a class="primary-action" href="../measure/index.html">새 실험 시작하기</a></div>
    </section>`;
}

function renderError(message) {
  root.innerHTML = `<section class="measurement-panel"><p class="eyebrow">저장된 실험 기록</p><h1>서버에 연결할 수 없어요</h1><p class="kid-help">PC에서 FitShare 서버가 실행 중인지 확인한 뒤 다시 열어 주세요.</p><p class="error-message">${escapeHtml(message)}</p><button type="button" class="primary-action" onclick="location.reload()">다시 불러오기</button></section>`;
}

function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '날짜를 알 수 없어요';
  return new Intl.DateTimeFormat('ko-KR', { dateStyle: 'medium', timeStyle: 'short' }).format(date);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);
}
