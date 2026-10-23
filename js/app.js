import { state, markTabVisited } from './state.js';
import { renderMeasurement } from '../modules/measurement.js?v=garment-registration-1';
import { renderRecommendation } from '../modules/recommendation.js';
import { renderRanking } from '../modules/ranking.js?v=ranking-9';
import { fetchMe, getUser, login, logout, signup } from './auth.js?v=auth-3';
import { openProfileDialog } from '../modules/profile.js?v=profile-4';
import { createSchoolEntryResult } from './schoolEntry.js';
import { createGarmentCloset } from './measurement/garmentCloset.js?v=closet-tools-6';
import { openGarmentDetail } from '../modules/garmentDetail.js?v=detail-6';

const root = document.querySelector('#module-root');
const tabs = document.querySelectorAll('.tab');
const schoolInput = document.querySelector('#school-code');
const schoolCloset = document.querySelector('#school-closet');
const schoolClosetStatus = document.querySelector('#school-closet-status');
const schoolClosetList = document.querySelector('#school-closet-list');
const schoolClosetView = createGarmentCloset({
  list: schoolClosetList,
  status: schoolClosetStatus,
  pagination: document.querySelector('#school-closet-pages'),
  onOpen: openGarmentDetail
});
const renderers = { measurement: renderMeasurement, recommendation: renderRecommendation, ranking: renderRanking };
let disposeModule=null;

function selectTab(tabName) {
  if(typeof disposeModule==='function')disposeModule();
  disposeModule=null;
  markTabVisited(tabName);
  tabs.forEach((tab) => tab.classList.toggle('is-active', tab.dataset.tab === tabName));
  const cleanup=renderers[tabName](root, state);
  if(typeof cleanup==='function')disposeModule=cleanup;
  document.querySelector('.progress-pill').textContent = `오늘의 탐험 ${state.visitedTabs.size}/3`;
}

tabs.forEach((tab) => tab.addEventListener('click', () => selectTab(tab.dataset.tab)));
function enterSchool() {
  const input = document.querySelector('#school-code');
  const message = document.querySelector('#entry-message');
  const action = document.querySelector('#entry-action');
  const result = createSchoolEntryResult(input.value);
  state.schoolCode = result.code;
  message.textContent = result.message;
  message.classList.toggle('is-error', !result.ok);
  action.hidden = !result.ok;
  schoolCloset.hidden = !result.ok;
  document.querySelector('.app-shell').classList.toggle('is-entered', result.ok);
  if (result.ok) schoolClosetView.reload();
  else input.focus();
}

// 칸에 들어오거나 클릭하면 학교 이름 전체를 선택해 바로 바꿀 수 있어요.
schoolInput.addEventListener('focus', () => schoolInput.select());
schoolInput.addEventListener('click', () => schoolInput.select());
document.querySelector('#enter-button').addEventListener('click', enterSchool);
schoolInput.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') enterSchool();
});

// 맨 처음에는 로그인부터 합니다. 로그인하기 전에는 옷장과 기능 탭을 보여 주지 않아요.
const authPanel = document.querySelector('#auth-panel');
const authForm = document.querySelector('#auth-form');
const authMessage = document.querySelector('#auth-message');
const appMain = document.querySelector('#app-main');
const userBox = document.querySelector('#user-box');
let signupMode = false;

function setAuthMode(isSignup) {
  signupMode = isSignup;
  document.querySelector('#auth-tab-login').setAttribute('aria-selected', String(!isSignup));
  document.querySelector('#auth-tab-signup').setAttribute('aria-selected', String(isSignup));
  document.querySelector('#auth-name-row').hidden = !isSignup;
  document.querySelector('#auth-name').required = isSignup;
  document.querySelector('#auth-role-row').hidden = !isSignup;
  document.querySelector('#auth-teacher-code-row').hidden = !isSignup || document.querySelector('#auth-role').value !== 'teacher';
  document.querySelector('#auth-teacher-code').required = isSignup && document.querySelector('#auth-role').value === 'teacher';
  document.querySelector('#auth-submit').textContent = isSignup ? '회원가입하고 시작하기' : '로그인';
  document.querySelector('#auth-password').autocomplete = isSignup ? 'new-password' : 'current-password';
  authMessage.textContent = '';
}

function showApp() {
  const user = getUser();
  authPanel.hidden = Boolean(user);
  appMain.hidden = !user;
  userBox.hidden = !user;
  document.querySelector('#user-name').textContent = user ? `🙂 ${user.name} 님 · 내페이지` : '';
  document.querySelector('#user-name').hidden = !user;
  if (user) selectTab(state.activeTab);
  else {
    if(typeof disposeModule==='function')disposeModule();
    disposeModule=null;
    root.replaceChildren();root.removeAttribute('data-module');
    document.querySelector('#auth-username').focus();
  }
}

document.querySelector('#auth-tab-login').addEventListener('click', () => setAuthMode(false));
document.querySelector('#auth-tab-signup').addEventListener('click', () => setAuthMode(true));
document.querySelector('#auth-role').addEventListener('change', () => {
  const teacher = signupMode && document.querySelector('#auth-role').value === 'teacher';
  document.querySelector('#auth-teacher-code-row').hidden = !teacher;
  document.querySelector('#auth-teacher-code').required = teacher;
});
authForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const submitButton = document.querySelector('#auth-submit');
  submitButton.disabled = true;
  authMessage.classList.remove('is-error');
  authMessage.textContent = '확인하고 있어요.';
  try {
    const username = document.querySelector('#auth-username').value;
    const password = document.querySelector('#auth-password').value;
    if (signupMode) {
      const teacherCode = document.querySelector('#auth-role').value === 'teacher'
        ? document.querySelector('#auth-teacher-code').value : '';
      await signup(username, password, document.querySelector('#auth-name').value, teacherCode);
    }
    else await login(username, password);
    authForm.reset();
    authMessage.textContent = '';
    showApp();
  } catch (error) {
    authMessage.classList.add('is-error');
    authMessage.textContent = error.message;
  } finally {
    submitButton.disabled = false;
  }
});
document.querySelector('#user-name').addEventListener('click', () => openProfileDialog());
document.querySelector('#logout-button').addEventListener('click', async () => {
  await logout();
  schoolCloset.hidden = true;
  document.querySelector('.app-shell').classList.remove('is-entered');
  showApp();
});
window.addEventListener('fitshare:auth-changed', () => {
  schoolCloset.hidden = true;
  document.querySelector('.app-shell').classList.remove('is-entered');
  authMessage.classList.remove('is-error');
  authMessage.textContent = '비밀번호가 바뀌었어요. 새 비밀번호로 다시 로그인해 주세요.';
  showApp();
});

appMain.hidden = true;
fetchMe().then(showApp);
