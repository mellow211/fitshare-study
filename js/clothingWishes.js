const LABELS = { gender: '입을 사람', size: '사이즈', season: '계절', category: '종류', style: '스타일', color: '색상' };
const escape = (value) => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));

export function mountClothingWishes(host, readFilters, isActive) {
  host.innerHTML = `<h4>원하는 옷이 들어오면 알려주세요 <span data-wish-count></span></h4>
    <p>찾는 옷이 없나요? 위에서 원하는 조건을 고르고 알림을 등록해 보세요.</p>
    <p data-wish-preview></p>
    <button type="button" class="save-button" data-wish-save>현재 조건으로 알림 받기</button>
    <button type="button" class="save-button" data-wish-refresh>새 알림 확인</button>
    <p>저장 이후 조건에 맞는 옷이 새로 기부되면 이곳에서 알려드려요. 추천 탭을 열어 두면 30초마다 확인해요.</p>
    <p data-wish-status role="status"></p>
    <div data-wish-list></div>`;
  const list = host.querySelector('[data-wish-list]');
  const status = host.querySelector('[data-wish-status]');
  const save = host.querySelector('[data-wish-save]');
  let disposed = false;
  let busy = false;
  let version = 0;
  let timer;
  const active = () => !disposed && host.isConnected && isActive();
  const summary = (filters) => Object.entries(LABELS).filter(([key]) => filters[key] && filters[key] !== '전체').map(([key, label]) => `${label}: ${filters[key]}`).join(' · ') || '조건을 하나 이상 선택해 주세요.';
  function updatePreview() {
    host.querySelector('[data-wish-preview]').textContent = summary(readFilters());
  }
  function render(wishes) {
    const count = wishes.reduce((total, wish) => total + wish.matches.filter(item => item.unread).length, 0);
    host.querySelector('[data-wish-count]').textContent = count ? `새 알림 ${count}개` : '';
    list.innerHTML = wishes.length ? wishes.map(wish => `<article class="clothing-wish">
      <div class="clothing-wish-heading"><strong>${escape(summary(wish.filters))}</strong><button type="button" class="save-button" data-wish-delete="${escape(wish.id)}">알림 해제</button></div>
      ${wish.matches.length ? `<ul>${wish.matches.map(item => `<li>${item.unread ? '<span class="module-tag">새 기부</span> ' : ''}<button type="button" class="clothing-wish-link" data-wish-open="${escape(item.id)}">${escape(item.name)}</button> ${item.size ? `(사이즈 ${escape(item.size)})` : ''}</li>`).join('')}</ul>${wish.matches.some(item => item.unread) ? `<button type="button" class="save-button" data-wish-read="${escape(wish.id)}">알림 확인했어요</button>` : ''}` : '<p>조건에 맞는 새 기부 옷을 기다리고 있어요.</p>'}
    </article>`).join('') : '<p>아직 저장한 알림 조건이 없어요.</p>';
  }
  async function request(path = '', method = 'GET', body) {
    let response;
    try { response = await fetch('/api/clothing-wishes' + path, {
      method, cache: 'no-store', headers: { 'Content-Type': 'application/json' },
      ...(body ? { body: JSON.stringify(body) } : {})
    }); } catch { throw new Error('알림 서버에 연결하지 못했어요. 새 알림 확인을 눌러 다시 시도해 주세요.'); }
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '알림을 불러오지 못했어요. 다시 시도해 주세요.');
    if (!Array.isArray(data.wishes)) throw new Error('알림을 불러오지 못했어요. 다시 시도해 주세요.');
    return data.wishes;
  }
  async function refresh() {
    if (!active() || busy) return;
    const current = ++version;
    try {
      const wishes = await request();
      if (!active() || current !== version) return;
      render(wishes);
      status.textContent = '';
    } catch (error) {
      if (active() && current === version) status.textContent = error.message;
    }
  }
  async function mutate(path, method, body, message) {
    if (busy) return;
    busy = true;
    version++;
    save.disabled = true;
    status.textContent = '알림을 처리하고 있어요.';
    try {
      const wishes = await request(path, method, body);
      if (active()) { render(wishes); status.textContent = message; }
    } catch (error) { if (active()) status.textContent = error.message; }
    finally { busy = false; save.disabled = false; }
  }
  save.addEventListener('click', () => {
    const filters = Object.fromEntries(Object.keys(LABELS).map(key => [key, readFilters()[key] || '전체']));
    mutate('', 'POST', { filters }, '알림을 등록했어요. 조건에 맞는 새 기부 옷이 들어오면 여기에 표시해요.');
  });
  host.querySelector('[data-wish-refresh]').addEventListener('click', refresh);
  list.addEventListener('click', async event => {
    const button = event.target.closest('button');
    if (!button) return;
    if (button.dataset.wishDelete) return mutate('/' + encodeURIComponent(button.dataset.wishDelete), 'DELETE', null, '알림을 해제했어요.');
    if (button.dataset.wishRead) return mutate('/' + encodeURIComponent(button.dataset.wishRead) + '/read', 'POST', {}, '새 알림을 확인했어요.');
    if (button.dataset.wishOpen) {
      try {
        const response = await fetch('/api/garments/' + encodeURIComponent(button.dataset.wishOpen), { cache: 'no-store' });
        if (!response.ok) throw new Error('옷이 삭제되었거나 정보를 불러올 수 없어요.');
        const garment = await response.json();
        const { openGarmentDetail } = await import('../modules/garmentDetail.js?v=detail-16');
        if (active()) openGarmentDetail(garment, refresh);
      } catch (error) { if (active()) status.textContent = error.message; }
    }
  });
  async function poll() {
    await refresh();
    if (active()) timer = setTimeout(poll, 30000);
  }
  updatePreview();
  poll();
  return { updatePreview, dispose() { disposed = true; version++; clearTimeout(timer); } };
}
