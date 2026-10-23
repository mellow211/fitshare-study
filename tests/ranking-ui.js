// 모든 계정·랭킹·사진 응답은 이 탭의 메모리에만 있어요.
// 실제 /api 요청을 전달하지 않으며, 실제 localStorage와 다른 탭도 건드리지 않아요.
const checks = [];
const requests = [];
const unexpected = [];
const memory = new Map();
const storageDescriptor = Object.getOwnPropertyDescriptor(window, 'localStorage');
const originalBroadcast = window.BroadcastChannel;
const originalFetch = window.fetch;
const originalInterval = window.setInterval;
const originalClearInterval = window.clearInterval;
const imageSource = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'src');
const intervals = new Set();
const channels = new Set();
const isolatedStorage = {
  getItem(key) { return memory.get(String(key)) ?? null; },
  setItem(key, value) { memory.set(String(key), String(value)); },
  removeItem(key) { memory.delete(String(key)); },
  clear() { memory.clear(); },
  key(index) { return [...memory.keys()][index] ?? null; },
  get length() { return memory.size; }
};
Object.defineProperty(window, 'localStorage', { configurable: true, value: isolatedStorage });
window.BroadcastChannel = class {
  constructor(name) { this.name = name; channels.add(this); }
  postMessage(data) { for (const peer of channels) if (peer !== this && peer.name === this.name) queueMicrotask(() => { if (channels.has(peer)) peer.onmessage?.({ data }); }); }
  close() { channels.delete(this); }
};
window.setInterval = (callback, delay, ...args) => {
  const id = originalInterval(callback, delay, ...args); intervals.add(id); return id;
};
window.clearInterval = id => { intervals.delete(id); originalClearInterval(id); };
const samplePhoto = document.createElement('canvas'); samplePhoto.width = 4; samplePhoto.height = 4;
samplePhoto.getContext('2d').fillRect(0, 0, 4, 4);
const inlinePhoto = samplePhoto.toDataURL('image/png');
Object.defineProperty(HTMLImageElement.prototype, 'src', {
  configurable: true, get: imageSource.get,
  set(value) { imageSource.set.call(this, String(value).includes('/api/') ? inlinePhoto : value); }
});
let serverState = { version: 1, users: [], exchanges: [], badges: [], finalizedMonths: [] };
let revision = 0;
let signedIn = null;
let garmentCatalog = [];
let savedCallbacks = 0;
let accountFailure = false;
let fetchMe, renderRanking, openGarmentDetail, openProfileDialog, openMemberProfileDialog, openTodaySharingDialog;
const clone = value => JSON.parse(JSON.stringify(value));
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
window.fetch = async (input, options = {}) => {
  const url = typeof input === 'string' ? input : input.url;
  const path = new URL(url, location.href).pathname;
  const method = options.method || 'GET';
  const body = options.body ? JSON.parse(options.body) : undefined;
  requests.push({ path, method, body });
  if (path === '/api/auth/me' && method === 'GET') return json({ user: signedIn });
  if (path === '/api/ranking' && method === 'GET') return json({ rev: revision, state: clone(serverState) });
  if (path === '/api/garments' && method === 'GET') return json(clone(garmentCatalog));
  if (path === '/api/ranking' && method === 'POST') {
    if (!signedIn) return json({ error: '로그인한 뒤에 저장해 주세요.' }, 401);
    if (body.baseRev !== revision) return json({ rev: revision, state: clone(serverState) }, 409);
    serverState = clone(body.state); revision += 1; return json({ rev: revision });
  }
  if (path === '/api/ranking/teacher-handoff' && method === 'POST') {
    if (signedIn?.role !== 'teacher') return json({ error: '선생님만 전달 완료를 표시할 수 있어요.' }, 403);
    if (body.baseRev !== revision) return json({ error: '다른 친구가 먼저 기록했어요.', rev: revision, state: clone(serverState) }, 409);
    const exchange = serverState.exchanges.find(item => item.id === body.exchangeId);
    if (!exchange || exchange.status !== 'reserved') return json({ error: '전달 대기 중인 예약만 완료할 수 있어요.' }, 409);
    serverState.exchanges = serverState.exchanges.map(item => item.id === body.exchangeId ? { ...item, status: 'completed', completedAt: new Date().toISOString() } : item);
    revision += 1; return json({ rev: revision, state: clone(serverState) });
  }
  if (path === '/api/auth/change-password' && method === 'POST') {
    if (!signedIn) return json({ error: '로그인해 주세요.' }, 401);
    return accountFailure ? json({ error: '모의 설정 오류예요. 다시 시도해 주세요.' }, 500) : json({ ok: true });
  }
  if (['/api/auth/teacher-code', '/api/auth/reset-student-password'].includes(path) && method === 'POST') {
    if (signedIn?.role !== 'teacher') return json({ error: '선생님만 사용할 수 있어요.' }, 403);
    return accountFailure ? json({ error: '모의 설정 오류예요. 다시 시도해 주세요.' }, 500) : json({ ok: true });
  }
  unexpected.push({ path, method });
  throw Error('격리된 검사에서 허용하지 않은 요청: ' + method + ' ' + path);
};

const root = document.createElement('div');
root.id = 'ranking-test-fixture';
document.body.append(root);
const $ = selector => root.querySelector(selector);
const settle = (ms = 0) => new Promise(resolve => setTimeout(resolve, ms));
const assert = (condition, message) => { if (!condition) throw Error(message); };
async function waitFor(condition, message) {
  for (let attempt = 0; attempt < 1000; attempt += 1) {
    if (condition()) return;
    await settle(10);
  }
  throw Error(message || '화면 대기 시간 초과');
}
async function check(name, action) {
  try { await action(); checks.push({ name, passed: true }); }
  catch (error) { checks.push({ name, passed: false, error: error.message }); }
  document.querySelector('#result').textContent = 'RUNNING ' + checks.length + ' checks';
}
const submit = form => form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
const myDialog = () => document.querySelector('.my-page-dialog');
const publicDialog = () => document.querySelector('.member-profile-dialog');
const visibleText = node => { const copy = node.cloneNode(true); copy.querySelectorAll('select').forEach(select => select.remove()); return copy.textContent; };
const rankGets = () => requests.filter(item => item.path === '/api/ranking' && item.method === 'GET').length;
async function signIn(name, role = 'student') {
  signedIn = name ? { username: 'isolated-' + (name === '지수' ? 'giver' : name === '민수' ? 'receiver' : 'other'), name, role } : null;
  await fetchMe();
}
async function closeDialog(dialog) {
  if (!dialog) return;
  dialog.close();
  await waitFor(() => !dialog.open, '창이 닫히지 않았어요.');
  dialog.remove();
  await settle();
}
async function closeAllDialogs() {
  for (const dialog of [...document.querySelectorAll('dialog')]) await closeDialog(dialog);
}
async function openMine(name, role = 'student') {
  await closeAllDialogs();
  await signIn(name, role);
  const dialog = openProfileDialog(() => { savedCallbacks += 1; });
  await waitFor(() => dialog.querySelector('#rk-account') || !name, '내 페이지가 그려지지 않았어요.');
  await settle();
  return dialog;
}
async function showRanking(name, role = 'student') {
  await signIn(name, role);
  if (!root.isConnected) document.body.append(root);
  renderRanking(root);
  await waitFor(() => Boolean($('#rk-board')), '순위 화면이 그려지지 않았어요.');
  await settle();
}
async function reserveAs(name, garment) {
  await closeAllDialogs();
  await signIn(name);
  openGarmentDetail(garment);
  const dialog = [...document.querySelectorAll('dialog.garment-detail')].at(-1);
  await settle();
  const button = dialog.querySelector('.primary-action');
  if (!button.disabled) {
    button.click();
    await waitFor(() => dialog.querySelector('.garment-detail-message').textContent.trim(), '예약 결과 안내가 없어요.');
  }
  const result = {
    status: dialog.querySelector('.garment-detail-status').textContent,
    message: dialog.querySelector('.garment-detail-message').textContent,
    disabled: button.disabled,
    error: dialog.querySelector('.garment-detail-message').classList.contains('is-error')
  };
  await closeDialog(dialog);
  return result;
}
async function sendScore(dialog, score) {
  const form = dialog.querySelector('.rk-rate');
  assert(form, '점수 보내기 칸이 없어요.');
  form.querySelector('select').value = String(score);
  const before = revision;
  submit(form);
  await waitFor(() => revision > before || dialog.querySelector('#rk-message')?.classList.contains('is-error'), '점수 처리 결과가 없어요.');
  await settle();
}
const jacket = { id: 'isolated-ranking-jacket', name: '파란 점퍼', garmentTypeId: 4, category: 'top', garmentTypeName: '점퍼',
  gender: 'unisex', colorName: '파랑', lengthCm: 60, widthCm: 50, ownerName: '지수', ownerUsername: 'isolated-giver' };

try {
  [{ fetchMe }, { renderRanking }, { openGarmentDetail }, { openProfileDialog, openMemberProfileDialog }, { openTodaySharingDialog }] = await Promise.all([
    import('../js/auth.js?v=auth-3'), import('../modules/ranking.js'), import('../modules/garmentDetail.js'), import('../modules/profile.js?v=profile-4'), import('../modules/todaySharing.js?v=garment-codes-1')
  ]);
  await check('빈 참여 랭킹은 공개 비교만 보여 주고 계정·서버 기록을 만들지 않아요', async () => {
    await showRanking(null);
    assert($('#rk-board').textContent.includes('점수가 아직 없어요'), '빈 순위 안내가 없어요.');
    assert(!root.querySelector('#rk-account,#rk-profile,#rk-reservations,#rk-pending,#rk-reviewable,.rk-rate,.rk-review-form,[data-key],[data-account-action]'), '개인 기능이 순위 탭에 남아 있어요.');
    assert(revision === 0 && serverState.users.length === 0, '순위 읽기만으로 기록을 만들면 안 돼요.');
    assert(!root.textContent.includes('체험용'), '가짜 참여자가 보이면 안 돼요.');
  });
  await check('로그인한 순위 화면에서 내 페이지를 열 수 있어요', async () => {
    await showRanking('민수');
    assert($('#rk-open-mypage'), '내 페이지로 가는 버튼이 없어요.');
    $('#rk-open-mypage').click();
    await waitFor(() => myDialog()?.querySelector('#rk-account'), '내 페이지가 열리지 않았어요.');
    assert(myDialog().textContent.includes('민수') && myDialog().querySelector('#rk-reservations'), '계정과 예약이 내 페이지에 있어야 해요.');
    assert(myDialog().querySelector('[data-account-action="change-own-password"]'), '학생 본인 비밀번호 변경이 보여야 해요.');
    assert(!myDialog().querySelector('[data-account-action="teacher-code"],[data-account-action="reset-student-password"]'), '학생에게 선생님 설정이 보여요.');
    await closeAllDialogs();
  });
  await check('옷 상세에서 예약하면 예약 중으로 바뀌고 중복 예약을 막아요', async () => {
    const result = await reserveAs('민수', jacket);
    assert(result.status.includes('민수') && result.status.includes('예약 중') && result.disabled, '예약 상태가 바뀌지 않았어요.');
    assert(serverState.exchanges.length === 1 && serverState.exchanges[0].status === 'reserved', '메모리에 예약 한 개가 저장돼야 해요.');
    const again = await reserveAs('민수', jacket);
    assert(again.disabled && serverState.exchanges.length === 1, '같은 옷의 중복 예약을 막지 못했어요.');
  });
  await check('내 옷·올린 사람 없는 옷의 잘못된 예약은 거부해요', async () => {
    const own = await reserveAs('지수', { ...jacket, id: 'isolated-own' });
    assert(own.error && own.message.includes('내가 올린 옷'), '내 옷 예약 오류 안내가 없어요.');
    const nobody = await reserveAs('민수', { ...jacket, id: 'isolated-nobody', ownerName: '' });
    assert(nobody.disabled && nobody.status.includes('예약할 수 없어요'), '올린 사람 없는 옷을 예약할 수 있어요.');
    assert(serverState.exchanges.length === 1, '잘못된 예약이 추가됐어요.');
  });
  await check('받은 친구만 내 페이지에서 수령을 확인하고 받기 전에는 평가할 수 없어요', async () => {
    let dialog = await openMine('지수');
    assert(dialog.querySelector('#rk-reservations').textContent.includes(jacket.name), '준 친구 예약 목록이 없어요.');
    assert(!dialog.querySelector('[data-confirm]') && !dialog.querySelector('.rk-rate'), '준 친구가 받았다고 확인하거나 미리 평가할 수 있어요.');
    dialog = await openMine('민수');
    assert(dialog.querySelector('[data-confirm]') && !dialog.querySelector('.rk-rate'), '받은 친구의 확인 버튼이 없거나 받기 전에 평가가 보여요.');
    dialog.querySelector('[data-confirm]').click();
    await waitFor(() => Boolean(dialog.querySelector('.rk-rate')), '수령 확인 뒤 평가 칸이 없어요.');
    assert(serverState.exchanges[0].status === 'completed', '수령 상태가 저장되지 않았어요.');
  });
  await check('범위를 벗어난 점수는 오류로 안내하고 유효한 점수는 비공개로 보내요', async () => {
    const dialog = myDialog();
    const select = dialog.querySelector('.rk-rate select');
    const invalid = document.createElement('option'); invalid.value = '11'; invalid.textContent = '잘못된 11점'; select.append(invalid);
    const before = revision;
    await sendScore(dialog, 11);
    assert(revision === before && dialog.querySelector('#rk-message').classList.contains('is-error'), '범위를 벗어난 점수를 저장했어요.');
    await sendScore(dialog, 8);
    assert(dialog.textContent.includes('내 점수 8점을 보냈어요'), '내가 보낸 점수 안내가 없어요.');
    assert(serverState.exchanges[0].revealedAt === null, '상대가 보내기 전에 점수가 공개됐어요.');
  });
  await check('상대 점수는 숨기고 둘 다 보낸 뒤에만 순위·내 나눔 내역에 공개해요', async () => {
    const dialog = await openMine('지수');
    assert(!visibleText(dialog).includes('8점') && !visibleText(dialog).includes('+8'), '비공개 상대 점수가 보여요.');
    await sendScore(dialog, 6);
    assert(Boolean(serverState.exchanges[0].revealedAt), '둘 다 점수를 보낸 뒤 공개되지 않았어요.');
    assert(dialog.querySelector('#rk-ledger').textContent.includes('6점') && dialog.querySelector('#rk-ledger').textContent.includes('8점'), '내역에 양쪽 공개 점수가 없어요.');
    assert(!dialog.querySelector('.rk-review-form'), '준 친구에게 후기 쓰기가 보여요.');
    await showRanking('지수');
    await waitFor(() => root.querySelectorAll('.rk-rank').length === 2, '공개 순위가 갱신되지 않았어요.');
    const rows = [...root.querySelectorAll('.rk-rank')];
    assert(rows[0].textContent.includes('지수') && rows[0].textContent.includes('8점') && rows[1].textContent.includes('6점'), '순위와 받은 점수가 다르게 표시돼요.');
    assert(rows.some(row => row.classList.contains('is-self') || row.querySelector('.is-self')), '로그인한 내 순위가 구분되지 않아요.');
    assert($('#rk-recent').textContent.includes('지수 +8점') && $('#rk-recent').textContent.includes('민수 +6점'), '공개된 점수 목록이 없어요.');
    assert(!root.querySelector('#rk-ledger,#rk-reservations,.rk-rate,.rk-review-form'), '개인 거래 칸이 랭킹에 남아 있어요.');
  });
  await check('내 페이지 새로 확인 중에도 입력하던 후기와 아바타 초안을 지켜요', async () => {
    const dialog = await openMine('민수');
    const editor = dialog.querySelector('[data-avatar-editor]'); editor.open = true;
    dialog.querySelector('[data-key="face"] [data-value="🐶"]').click();
    const review = dialog.querySelector('.rk-review-form input');
    review.value = '아직 보내지 않은 후기';
    const avatar = dialog.querySelector('#pf-preview').textContent;
    root.remove();
    const before = rankGets();
    await waitFor(() => rankGets() > before, '내 페이지 주기 확인이 실행되지 않았어요.');
    await settle();
    assert(review.isConnected && review.value === '아직 보내지 않은 후기', '주기 확인이 입력하던 후기를 지웠어요.');
    assert(dialog.querySelector('#pf-preview').textContent === avatar && avatar.includes('🐶'), '주기 확인이 아바타 초안을 덮어썼어요.');
    document.body.append(root);
  });
  await check('받은 친구만 후기를 한 번 쓰고 글에 들어간 HTML은 실행하지 않아요', async () => {
    const dialog = myDialog();
    let form = dialog.querySelector('.rk-review-form');
    form.querySelector('input').value = '   '; submit(form);
    await waitFor(() => dialog.querySelector('#rk-message').classList.contains('is-error'), '빈 후기 오류가 없어요.');
    assert(dialog.querySelector('#rk-message').textContent.includes('후기 내용'), '빈 후기 안내가 이해하기 어려워요.');
    form = dialog.querySelector('.rk-review-form'); form.querySelector('input').value = '<i>깨끗해요</i>'; submit(form);
    await waitFor(() => !dialog.querySelector('.rk-review-form'), '후기 제출 뒤 입력 칸이 남아 있어요.');
    assert(serverState.exchanges[0].review.text === '<i>깨끗해요</i>', '후기가 저장되지 않았어요.');
    const id = serverState.users.find(user => user.name === '지수').id;
    await closeAllDialogs(); openMemberProfileDialog(id);
    await waitFor(() => publicDialog()?.querySelector('.rk-reviews'), '친구의 받은 후기가 없어요.');
    assert(publicDialog().querySelector('.rk-reviews').textContent.includes('<i>깨끗해요</i>') && !publicDialog().querySelector('.rk-reviews i'), '후기의 HTML 태그가 실행됐어요.');
  });
  await check('친구 공개 프로필에는 요약·받은 후기만 있고 개인 거래·편집·설정은 없어요', async () => {
    const dialog = publicDialog();
    assert(dialog.querySelector('[data-member-profile]') && dialog.querySelector('.rk-stats'), '공개 프로필 요약이 없어요.');
    assert(!dialog.querySelector('#rk-ledger,#rk-reservations,#rk-pending,#rk-reviewable,[data-avatar-editor],[data-save],[data-account-action],input,select'), '친구 프로필에서 개인 기능이 보여요.');
    assert(dialog.textContent.includes('지수') && dialog.textContent.includes('깨끗해요'), '친구 공개 정보가 없어요.');
    await closeAllDialogs(); await showRanking('민수');
    const friend = [...root.querySelectorAll('[data-profile]')].find(button => button.textContent.includes('지수'));
    assert(friend, '순위에서 친구 프로필을 여는 버튼이 없어요.');
    friend.click(); await waitFor(() => Boolean(publicDialog()), '순위의 친구 이름을 눌러도 프로필이 안 열려요.');
    assert(!myDialog(), '친구 이름을 누르면 내 페이지 대신 공개 프로필이 열려야 해요.');
  });
  await check('내 페이지의 아바타를 저장하면 공개 순위에도 반영돼요', async () => {
    const dialog = await openMine('지수', 'teacher');
    assert(dialog.querySelector('.rk-stats').textContent.includes('나눔한 횟수') && dialog.querySelector('.rk-stats').textContent.includes('1번'), '나눔 횟수 요약이 없어요.');
    assert(dialog.textContent.includes('배지'), '배지 정보가 없어요.');
    dialog.querySelector('[data-avatar-editor]').open = true;
    dialog.querySelector('[data-key="face"] [data-value="🐶"]').click();
    dialog.querySelector('[data-key="hat"] [data-value="👑"]').click();
    assert(dialog.querySelector('#pf-preview').textContent.includes('🐶') && dialog.querySelector('#pf-preview').textContent.includes('👑'), '아바타 미리보기가 안 바뀌어요.');
    const before = savedCallbacks;
    dialog.querySelector('[data-save]').click();
    await waitFor(() => savedCallbacks > before, '아바타 저장 콜백이 실행되지 않았어요.');
    assert(dialog.querySelector('#pf-message').textContent.includes('저장했어요') && savedCallbacks === before + 1, '저장 안내 또는 콜백 횟수가 잘못됐어요.');
    assert(serverState.users.find(user => user.name === '지수').avatar.face === '🐶', '아바타가 메모리 저장소에 저장되지 않았어요.');
    await showRanking('지수', 'teacher');
    await waitFor(() => [...root.querySelectorAll('.rk-name .avatar')].some(avatar => avatar.textContent.includes('🐶') && avatar.textContent.includes('👑')), '저장한 아바타가 순위에 반영되지 않았어요.');
  });
  await check('내 페이지 주기 확인이 선생님 설정의 작성 중인 값을 지우지 않아요', async () => {
    const dialog = myDialog();
    const field = dialog.querySelector('[data-account-action="teacher-code"] input');
    assert(field, '선생님 설정이 내 페이지에 없어요.');
    field.value = 'isolated-unsaved-example';
    root.remove();
    const before = rankGets();
    await waitFor(() => rankGets() > before, '선생님 내 페이지 주기 확인이 실행되지 않았어요.');
    await settle();
    assert(field.isConnected && field.value === 'isolated-unsaved-example', '설정 작성 중인 값이 주기 확인으로 지워졌어요.');
    document.body.append(root);
  });
  await check('선생님 설정은 내 페이지에서 성공·실패를 안내하고 학생에게는 숨겨요', async () => {
    let dialog = myDialog();
    const ownPasswordForm = dialog.querySelector('[data-account-action="change-own-password"]');
    assert(ownPasswordForm, '선생님 본인 비밀번호 변경이 보여야 해요.');
    ownPasswordForm.elements.currentPassword.value = 'isolated-current';
    ownPasswordForm.elements.newPassword.value = 'isolated-new';
    ownPasswordForm.elements.confirmPassword.value = 'different';
    submit(ownPasswordForm);
    assert(dialog.querySelector('#pf-account-message').classList.contains('is-error'), '새 비밀번호가 다르면 오류를 알려야 해요.');
    assert(!requests.some(item => item.path === '/api/auth/change-password'), '확인값이 다르면 요청하면 안 돼요.');
    ownPasswordForm.elements.confirmPassword.value = 'isolated-new';
    submit(ownPasswordForm);
    await waitFor(() => !dialog.isConnected, '비밀번호 변경 뒤 프로필을 닫아야 해요.');
    assert(requests.some(item => item.path === '/api/auth/change-password' && item.body.currentPassword === 'isolated-current' && item.body.newPassword === 'isolated-new'), '본인 비밀번호 요청 값이 잘못됐어요.');
    dialog = await openMine('지수', 'teacher');
    let form = dialog.querySelector('[data-account-action="teacher-code"]');
    submit(form);
    await waitFor(() => dialog.querySelector('#pf-account-message').textContent.includes('바꿨어요'), '인증 코드 저장 안내가 없어요.');
    assert(requests.some(item => item.path === '/api/auth/teacher-code' && item.body.newCode === 'isolated-unsaved-example'), '설정 요청 값이 잘못됐어요.');
    accountFailure = true;
    form = dialog.querySelector('[data-account-action="reset-student-password"]');
    form.elements.username.value = 'isolated-student'; form.elements.newPassword.value = 'isolated-example-password'; submit(form);
    await waitFor(() => dialog.querySelector('#pf-account-message').classList.contains('is-error'), '설정 실패 안내가 없어요.');
    assert(!form.querySelector('[type="submit"]').disabled, '오류 뒤 다시 시도할 수 없어요.');
    accountFailure = false;
    submit(form); await waitFor(() => dialog.querySelector('#pf-account-message').textContent.includes('바꿨어요'), '설정 재시도가 안 됐어요.');
    dialog = await openMine('민수');
    assert(dialog.querySelector('[data-account-action="change-own-password"]'), '학생 본인 비밀번호 변경이 있어야 해요.');
    assert(!dialog.querySelector('[data-account-action="teacher-code"],[data-account-action="reset-student-password"]'), '학생에게 선생님 설정이 남아 있어요.');
    assert(!root.querySelector('[data-account-action]'), '설정이 랭킹 탭에 남아 있어요.');
  });
  await check('예약 취소도 내 페이지에서 하고 같은 옷을 다시 예약할 수 있어요', async () => {
    const pants = { ...jacket, id: 'isolated-ranking-pants', name: '검정 바지', category: 'pants', garmentTypeId: 5 };
    await reserveAs('하준', pants);
    const dialog = await openMine('하준');
    dialog.querySelector('#rk-reservations [data-cancel]').click();
    await waitFor(() => !dialog.querySelector('#rk-reservations [data-cancel]'), '취소한 예약이 목록에 남아 있어요.');
    const again = await reserveAs('민수', pants);
    assert(again.status.includes('민수') && !again.error, '취소 뒤 다시 예약할 수 없어요.');
  });
  await check('캐시를 비운 새 화면에서도 메모리 서버의 공개 순위를 읽어요', async () => {
    localStorage.removeItem('fitshare.ranking.v1'); localStorage.removeItem('fitshare.ranking.rev');
    await closeAllDialogs(); await showRanking('민수');
    await waitFor(() => root.querySelectorAll('.rk-rank').length === 2, '빈 캐시에서 공개 순위를 읽지 못했어요.');
    assert(revision > 0 && serverState.exchanges.some(item => item.review), '모의 저장소의 기록이 사라졌어요.');
  });
  await check('선생님 오늘의 나눔에서 예약 대상과 옷을 보고 전달 완료를 표시해요', async () => {
    const reservedGarment = { ...jacket, id: 'isolated-teacher-handoff', name: '전달 확인용 가디건' };
    garmentCatalog = [{ ...reservedGarment, garmentTypeCode: 'j', garmentNumber: 2,
      donorId: 'isolated-giver', garmentCode: '나눔초등학교_j_002_isolated-giver' }];
    const reservation = await reserveAs('민수', reservedGarment);
    assert(reservation.status.includes('민수') && !reservation.error, '전달 확인용 예약을 만들지 못했어요.');
    await signIn('지수', 'teacher');
    const dialog = openTodaySharingDialog();
    await waitFor(() => dialog?.textContent.includes('나눔초등학교_j_002_isolated-giver'), '종류별 옷 번호와 기부자 아이디를 표시하지 않아요.');
    assert(dialog?.textContent.includes('전달 확인용 가디건') && dialog.textContent.includes('isolated-giver') && dialog.textContent.includes('지수') && dialog.textContent.includes('민수'), '옷 번호·이름·기부자 아이디·받는 학생을 표시하지 않아요.');
    const button = dialog.querySelector('[data-complete]');
    assert(button?.textContent.includes('나눔 완료'), '완료 표시 버튼이 없어요.');
    const before = revision; button.click();
    await waitFor(() => revision > before && serverState.exchanges.some(item => item.garmentId === reservedGarment.id && item.status === 'completed'), '완료 상태가 저장되지 않았어요.');
    assert(dialog.querySelector('[data-completed-list]').textContent.includes('전달 확인용 가디건') && dialog.querySelector('[data-completed-list]').textContent.includes('나눔초등학교_j_002_isolated-giver'), '완료한 옷의 번호와 이름이 오늘 완료 목록에 보이지 않아요.');
    await closeDialog(dialog);
    await signIn('민수');
    assert(openTodaySharingDialog() === null, '학생 계정에서 선생님 전달 화면이 열려요.');
  });
  await check('로그아웃·손상된 캐시·HTML 같은 이름에도 화면이 깨지지 않아요', async () => {
    let dialog = await openMine('<b>x</b>');
    assert(dialog.querySelector('.rk-profile-name').textContent === '<b>x</b>' && !dialog.querySelector('.rk-profile-name b'), '이름의 HTML이 실행됐어요.');
    await closeAllDialogs(); await signIn(null);
    localStorage.setItem('fitshare.ranking.v1', '{broken-json');
    renderRanking(root); await settle();
    assert(root.textContent.trim(), '손상된 캐시에서 랭킹이 그려지지 않았어요.');
    dialog = openProfileDialog(); await settle();
    assert(dialog.textContent.includes('로그인') && !dialog.querySelector('.rk-rate,.rk-review-form,[data-save],[data-account-action]'), '로그아웃했는데 개인 입력 기능이 보여요.');
  });
  await check('모든 계정·랭킹·사진 동작은 격리되어 실제 API로 나가지 않아요', async () => {
    assert(unexpected.length === 0, '예상하지 못한 API 요청: ' + JSON.stringify(unexpected));
    assert(window.localStorage === isolatedStorage, '실제 localStorage를 사용했어요.');
    assert(requests.every(item => ['/api/auth/me','/api/ranking','/api/ranking/teacher-handoff','/api/auth/teacher-code','/api/auth/reset-student-password','/api/auth/change-password','/api/garments'].includes(item.path)), '실제 계정이나 재고 API를 불렀어요.');
    assert(!requests.some(item => /signup|login|logout/.test(item.path)), '실제 계정 생성·로그인 요청을 시도했어요.');
  });
} catch (error) {
  checks.push({ name: '격리된 검사 준비', passed: false, error: error.stack || error.message });
} finally {
  await closeAllDialogs(); root.remove();
  for (const id of intervals) originalClearInterval(id);
  channels.clear();
  window.setInterval = originalInterval; window.clearInterval = originalClearInterval;
  window.BroadcastChannel = originalBroadcast;
  Object.defineProperty(window, 'localStorage', storageDescriptor);
  Object.defineProperty(HTMLImageElement.prototype, 'src', imageSource);
  // 검사 뒤 도착하는 비동기 작업도 실제 API로 보내지 않아요.
  window.fetch = (input, options) => {
    const path = new URL(typeof input === 'string' ? input : input.url, location.href).pathname;
    if (path.startsWith('/api/')) return Promise.reject(Error('격리된 UI 검사가 끝났어요.'));
    return originalFetch(input, options);
  };
}
const list = document.querySelector('#results');
for (const item of checks) {
  const line = document.createElement('li'); line.style.color = item.passed ? 'green' : 'red';
  line.textContent = (item.passed ? '통과: ' : '실패: ') + item.name + (item.error ? ' — ' + item.error : ''); list.append(line);
}
const passed = checks.filter(item => item.passed).length;
const summary = { passed, failed: checks.length - passed, checks, requests: requests.length, memoryRevision: revision, unexpected };
window.rankingUIResult = summary;
document.querySelector('#result').textContent = (summary.failed ? 'FAIL ' : 'PASS ') + passed + '/' + checks.length + ' checks; isolated API requests=' + requests.length;
document.title = passed + '/' + checks.length + ' 통과';
