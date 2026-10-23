// 옷장의 옷을 누르면 뜨는 상세 창입니다. 옷 정보를 보여 주고 "예약하기"로 나눔을 시작해요.
// 예약 기록은 공통 나눔 기록(rankingStore)에 저장돼서, 내페이지에서 이어집니다.
import { cancelReservation, getGarmentExchange, isRevealed, reserveGarment } from './rankingLogic.js';
import { loadState, loadViewerName, mutateState, pullState } from './rankingStore.js';
import { getUser, requestApi } from '../js/auth.js?v=auth-3';
import { openVirtualTryOn } from '../js/virtualTryOn/index.js';

const CSS_ID = 'garment-detail-style';
const GENDER_NAMES = { male: '남성용', female: '여성용', unisex: '공용', unknown: '모름' };

function ensureStyle() {
  if (document.getElementById(CSS_ID)) return;
  const link = document.createElement('link');
  link.id = CSS_ID; link.rel = 'stylesheet'; link.href = new URL('../css/ranking.css?v=ranking-17', import.meta.url).href;
  document.head.append(link);
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export function openGarmentDetail(garment, onClosetUpdated, { hasExchangeHistory = false, exchange = null, users = [] } = {}) {
  ensureStyle();
  const dialog = el('dialog', 'garment-detail');
  const id = encodeURIComponent(garment.id);
  const image = el('img', 'garment-detail-photo');
  image.alt = `${garment.name} 사진`;
  const choices = [`/api/garments/${id}/thumbnail?v=product-yolo-priority-3`, `/api/garments/${id}/closet-thumbnail?v=yolo-sam-v3`, `/api/garments/${id}/closet-photo?v=yolo-sam-v3`, `/api/garments/${id}/photo`];
  let choice = 0;
  image.onerror = () => { choice += 1; if (choice < choices.length) image.src = choices[choice]; else image.onerror = null; };
  image.src = choices[0];

  const photoControls = el('div', 'garment-detail-photo-controls');
  const originalPhoto = el('button', 'rk-ghost', '배경 포함 사진 보기');
  originalPhoto.type = 'button';
  originalPhoto.addEventListener('click', () => { image.onerror = null; image.classList.remove('is-product-photo'); image.src = `/api/garments/${id}/photo`; });
  const closetPhoto = el('button', 'rk-ghost', '옷만 보기');
  closetPhoto.type = 'button';
  closetPhoto.addEventListener('click', () => {
    choice = 1;
    image.onerror = () => { choice += 1; if (choice < choices.length) image.src = choices[choice]; else image.onerror = null; };
    image.classList.remove('is-product-photo');
    image.src = choices[choice];
  });
  photoControls.append(originalPhoto, closetPhoto);
  const generationProgress = el('div', 'garment-detail-generation-progress');
  generationProgress.hidden = true;
  generationProgress.setAttribute('role', 'progressbar');
  generationProgress.setAttribute('aria-label', 'AI 상품 사진 생성 진행 상황');
  generationProgress.setAttribute('aria-valuetext', '생성 중');
  generationProgress.append(el('span', 'garment-detail-generation-progress-bar'));
  let productPhotoUrl = garment.productPhotoUrl || null;
  let productPhotoVersion = garment.productPhotoGeneratedAt || '';
  const currentUser = getUser();
  const isOwner = Boolean(currentUser && (garment.ownerUsername
    ? garment.ownerUsername === currentUser.username
    : garment.ownerName && garment.ownerName === currentUser.name));
  if (currentUser?.role === 'teacher') {
    const productPhoto = el('button', 'rk-ghost', productPhotoUrl ? 'AI 상품 사진 보기' : 'AI 상품 사진 만들기');
    productPhoto.type = 'button';
    const regeneratePhoto = el('button', 'rk-ghost', 'AI 사진 다시 만들기');
    regeneratePhoto.type = 'button';
    regeneratePhoto.hidden = !productPhotoUrl;
    const deletePhoto = el('button', 'rk-ghost', 'AI 사진 삭제');
    deletePhoto.type = 'button';
    deletePhoto.hidden = !productPhotoUrl;
    let generating = false;

    function showProductPhoto() {
      image.onerror = null;
      image.classList.add('is-product-photo');
      image.src = `${productPhotoUrl}${productPhotoVersion ? `?v=${encodeURIComponent(productPhotoVersion)}` : ''}`;
    }

    async function generatePhoto(regenerate = false) {
      if (generating) return;
      if (regenerate && !window.confirm('AI 사진을 다시 만들면 비용이 발생할 수 있어요. 성공하면 기존 AI 사진을 새 사진으로 바꿔요. 계속할까요?')) return;
      generating = true;
      productPhoto.disabled = true;
      regeneratePhoto.disabled = true;
      generationProgress.hidden = false;
      generationProgress.setAttribute('aria-valuetext', regenerate ? 'AI 사진을 다시 만들고 있어요' : 'AI 상품 사진을 만들고 있어요');
      photoControls.classList.add('is-generating');
      message.textContent = regenerate ? 'AI 사진을 다시 만들고 있어요. 기존 사진은 성공할 때까지 유지돼요.' : 'AI 상품 사진을 만들고 있어요. 잠시 기다려 주세요.';
      message.classList.remove('is-error');
      try {
        const result = await requestApi(`/api/garments/${id}/product-photo`, regenerate ? { regenerate: true } : {});
        productPhotoUrl = result.productPhotoUrl;
        productPhotoVersion = result.generatedAt;
        garment.productPhotoUrl = productPhotoUrl;
        garment.productPhotoGeneratedAt = productPhotoVersion;
        productPhoto.textContent = 'AI 상품 사진 보기';
        regeneratePhoto.hidden = false;
        deletePhoto.hidden = false;
        showProductPhoto();
        message.textContent = regenerate ? '새 AI 사진으로 바꿨어요. 원본 사진은 그대로 있어요.' : 'AI 상품 사진을 저장했어요. 원본 사진은 그대로 있어요.';
        if (typeof onClosetUpdated === 'function') onClosetUpdated();
      } catch (error) {
        message.classList.add('is-error');
        message.textContent = error.message || 'AI 상품 사진을 만들지 못했어요.';
      } finally {
        generating = false;
        productPhoto.disabled = false;
        regeneratePhoto.disabled = false;
        generationProgress.hidden = true;
        photoControls.classList.remove('is-generating');
      }
    }
    productPhoto.addEventListener('click', () => {
      if (productPhotoUrl) showProductPhoto();
      else generatePhoto();
    });
    regeneratePhoto.addEventListener('click', () => generatePhoto(true));
    deletePhoto.addEventListener('click', async () => {
      if (generating) return;
      if (!window.confirm('AI 상품 사진을 삭제할까요? 원본 사진과 옷 정보는 그대로 있고, 나중에 다시 만들 수 있어요.')) return;
      generating = true;
      deletePhoto.disabled = true;
      message.classList.remove('is-error');
      try {
        const response = await fetch(`/api/garments/${id}/product-photo`, { method: 'DELETE' });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.error || 'AI 상품 사진을 삭제하지 못했어요.');
        productPhotoUrl = null;
        productPhotoVersion = '';
        delete garment.productPhotoUrl;
        delete garment.productPhotoGeneratedAt;
        productPhoto.textContent = 'AI 상품 사진 만들기';
        regeneratePhoto.hidden = true;
        deletePhoto.hidden = true;
        image.classList.remove('is-product-photo');
        choice = 1;
        image.src = `/api/garments/${id}/closet-thumbnail?v=yolo-sam-v3&t=${Date.now()}`;
        message.textContent = 'AI 상품 사진을 삭제했어요. 원본 사진은 그대로 있어요.';
        if (typeof onClosetUpdated === 'function') onClosetUpdated();
      } catch (error) {
        message.classList.add('is-error');
        message.textContent = error.message || 'AI 상품 사진을 삭제하지 못했어요.';
      } finally {
        generating = false;
        deletePhoto.disabled = false;
      }
    });
    photoControls.append(productPhoto, regeneratePhoto, deletePhoto);
  }

  const info = el('dl', 'garment-detail-info');
  const addProperty = (parent, label, value, measurement = false) => {
    const card = el('div', `garment-detail-property${measurement ? ' garment-detail-measurement' : ''}${value === '미등록' ? ' is-missing' : ''}`);
    card.append(el('span', 'garment-detail-star', '☆'));
    const copy = el('div', 'garment-detail-property-copy');
    copy.append(el('dt', '', label), el('dd', '', value));
    card.append(copy);
    parent.append(card);
  };
  const dimension = value => value !== null && value !== undefined && String(value).trim() !== '' && Number.isFinite(Number(value)) ? `${value}cm` : '미등록';
  const measurements = el('div', 'garment-detail-measurements');
  addProperty(measurements, '길이', dimension(garment.lengthCm), true);
  addProperty(measurements, '너비', dimension(garment.widthCm), true);
  info.append(measurements);
  const seasons = (Array.isArray(garment.seasons) ? garment.seasons : [garment.season]).filter(Boolean).join(' · ');
  const rows = [
    ['종류', garment.garmentTypeName || garment.category || '옷'],
    ['누가 입나요?', GENDER_NAMES[garment.gender] || '모름'],
    ['색깔', garment.colorName || '미등록'],
    ['올린 사람', garment.ownerName || '미등록'],
    [garment.metadataSources?.size === 'measurement-rule' ? '추정 호수' : '사이즈', garment.size || '미등록'],
    ['계절', seasons || '미등록'],
    ...Object.entries({ style: '스타일', condition: '옷 상태', garmentKind: '세부 종류', sleeveLength: '소매', bottomLength: '바지 길이', thickness: '두께', stretch: '신축성', pattern: '무늬' })
      .map(([key, label]) => [label, garment[key] || '미등록'])
  ];
  for (const [label, value] of rows) addProperty(info, label, value);
  if (garment.metadataSources?.size === 'measurement-rule') {
    info.append(el('p', 'garment-detail-size-note', '호수는 실측값 기반 참고 추정이에요. 제품 라벨과 다를 수 있어요.'));
  }
  if (garment.isDemo) info.append(el('p', '', '체험용 가상 등록 · 치수는 예시 값이에요.'));

  const status = el('p', 'garment-detail-status');
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  const message = el('p', 'garment-detail-message');
  message.setAttribute('role', 'status');
  const reserve = el('button', 'primary-action garment-detail-reserve', '나눔 예약하기');
  reserve.type = 'button';
  const cancel = el('button', 'rk-ghost', '예약 취소');
  cancel.type = 'button';
  const close = el('button', 'rk-ghost', '닫기');
  close.type = 'button';
  close.addEventListener('click', () => dialog.close());
  const buttons = el('div', 'garment-detail-buttons');
  if (['top', 'pants'].includes(garment.category)) {
    const tryOn = el('button', 'rk-ghost garment-detail-tryon', '가상으로 입어보기');
    tryOn.type = 'button';
    tryOn.addEventListener('click', () => openVirtualTryOn([garment], {
      owner: dialog,
      isActive: () => dialog.isConnected && dialog.open
    }));
    buttons.append(tryOn);
  }
  buttons.append(reserve, cancel);
  if (currentUser?.role === 'teacher' || (isOwner && !hasExchangeHistory)) {
    const remove = el('button', 'garment-detail-delete', '옷 삭제');
    remove.type = 'button';
    remove.addEventListener('click', async () => {
      if (!window.confirm(`“${garment.name}”과 원본·AI 사진을 모두 삭제합니다. 되돌릴 수 없어요. 삭제할까요?`)) return;
      remove.disabled = true;
      message.classList.remove('is-error');
      message.textContent = '옷과 사진을 삭제하고 있어요.';
      try {
        const response = await fetch(`/api/garments/${id}`, { method: 'DELETE', cache: 'no-store' });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.error || '옷을 삭제하지 못했어요.');
        message.textContent = '옷과 사진을 삭제했어요.';
        dialog.close();
        if (typeof onClosetUpdated === 'function') onClosetUpdated();
      } catch (error) {
        remove.disabled = false;
        message.classList.add('is-error');
        message.textContent = error.message || '옷을 삭제하지 못했어요. 다시 시도해 주세요.';
      }
    });
    buttons.append(remove);
  }
  buttons.append(close);
  const exchangeHistory = exchange ? createExchangeHistory(garment, exchange, users) : null;
  if (garment.isDemo) {
    const demoBadge = el('span', 'garment-demo-badge', '가상 옷');
    demoBadge.setAttribute('aria-label', '데모용 가상 옷');
    dialog.append(demoBadge);
  }
  dialog.append(el('h2', '', garment.name));
  if (exchangeHistory) dialog.append(exchangeHistory);
  dialog.append(image, photoControls, generationProgress, info, status, message, buttons);

  // 지금 이 옷이 예약 가능한지 서버 기록을 보고 화면을 맞춥니다.
  let current = null;
  function show(state) {
    current = getGarmentExchange(state, garment.id);
    const nameOf = (userId) => state.users.find((user) => user.id === userId)?.name ?? '?';
    const me = loadViewerName().toLowerCase();
    const mine = current && me && [nameOf(current.giverId), nameOf(current.receiverId)].some((name) => name.toLowerCase() === me);
    if (!current) {
      status.className = `garment-detail-status ${garment.ownerName ? 'is-available' : 'is-unavailable'}`;
      status.textContent = garment.ownerName ? '🟢 나눔 가능 · 이 옷을 예약할 수 있어요' : '⚪ 정보 필요 · 올린 사람을 확인해 주세요';
      reserve.textContent = garment.ownerName ? '나눔 예약하기' : '올린 사람 정보 필요';
    } else if (current.status === 'reserved') {
      status.className = 'garment-detail-status is-reserved';
      status.textContent = `🟡 예약 중 · ${nameOf(current.receiverId)} 님이 예약했어요`;
      reserve.textContent = '예약 중';
    } else {
      status.className = 'garment-detail-status is-completed';
      status.textContent = `⚪ 나눔 완료 · ${nameOf(current.receiverId)} 님에게 전달했어요`;
      reserve.textContent = '나눔 완료';
    }
    reserve.disabled = Boolean(current) || !garment.ownerName;
    cancel.hidden = !(current && current.status === 'reserved' && mine);
  }
  show(loadState());
  pullState().then((state) => { if (state) show(state); });

  async function run(action, okText) {
    reserve.disabled = true; cancel.disabled = true;
    try {
      const result = await mutateState(action);
      show(result.state);
      if (typeof onClosetUpdated === 'function') onClosetUpdated();
      message.classList.remove('is-error');
      message.textContent = result.shared ? okText : '서버에 닿지 않아 이 브라우저에만 저장했어요. 다른 기기의 친구에게는 아직 보이지 않아요.';
      if (!result.shared) message.classList.add('is-error');
    } catch (error) {
      message.classList.add('is-error');
      message.textContent = error.message || '문제가 생겼어요. 다시 시도해 주세요.';
      show(loadState());
    } finally {
      cancel.disabled = false;
    }
  }
  reserve.addEventListener('click', async () => {
    const reserverName = loadViewerName();
    if (!reserverName) { message.classList.add('is-error'); message.textContent = '로그인한 뒤에 예약할 수 있어요.'; return; }
    await run((state) => reserveGarment(state, {
      garmentId: garment.id, itemName: String(garment.name).slice(0, 30), ownerName: garment.ownerName, reserverName
    }).state, '예약했어요! 내페이지의 예약한 나눔에서 확인하고, 올린 사람과 직접 만나 옷을 받아 보세요.');
  });
  cancel.addEventListener('click', () => {
    const meName = loadViewerName();
    run((state) => {
      const me = state.users.find((user) => user.name.toLowerCase() === meName.toLowerCase());
      return cancelReservation(state, getGarmentExchange(state, garment.id)?.id, me?.id).state;
    }, '예약을 취소했어요.');
  });

  document.body.append(dialog);
  dialog.addEventListener('close', () => dialog.remove(), { once: true });
  if (typeof dialog.showModal === 'function') dialog.showModal();
  else dialog.setAttribute('open', '');
}

function createExchangeHistory(garment, exchange, users) {
  const nameOf = id => users.find(user => user.id === id)?.name || '이름을 확인할 수 없는 친구';
  const giverName = nameOf(exchange.giverId);
  const receiverName = nameOf(exchange.receiverId);
  const summary = el('section', 'garment-exchange-history');
  summary.setAttribute('aria-label', '나눔 기록');
  summary.append(el('h3', '', '나눔 기록'));
  summary.append(el('p', 'garment-exchange-history-summary',
    `${receiverName} 학생이 ${giverName} 학생의 ${exchange.itemName || garment.name} 옷을 가져갔어요.`));
  if (exchange.status !== 'completed') {
    summary.append(el('p', 'garment-exchange-history-wait', '아직 옷을 주고받는 중이에요.'));
  } else if (isRevealed(exchange)) {
    const ratings = el('ul', 'garment-exchange-ratings');
    const addRating = (label, score) => {
      const item = el('li', 'garment-exchange-rating');
      const stars = Math.max(0, Math.min(5, Math.round(Number(score) / 2)));
      item.append(el('span', 'garment-exchange-rating-label', label));
      item.append(el('span', 'garment-exchange-stars', `${'★'.repeat(stars)}${'☆'.repeat(5 - stars)}`));
      item.append(el('b', 'garment-exchange-score', `${score}/10`));
      ratings.append(item);
    };
    addRating(`${giverName} → ${receiverName}`, exchange.giverToReceiver);
    addRating(`${receiverName} → ${giverName}`, exchange.receiverToGiver);
    summary.append(el('h4', '', '서로의 만족도'));
    summary.append(ratings);
    if (exchange.review?.text) {
      const review = el('blockquote', 'garment-exchange-review');
      review.append(el('strong', 'garment-exchange-review-label', exchange.isDemoReview ? '🧪 체험용 가상 후기' : '💬 받은 친구 후기'));
      review.append(el('p', 'garment-exchange-review-text', exchange.review.text));
      summary.append(review);
    }
  } else {
    summary.append(el('p', 'garment-exchange-history-wait', '둘 다 점수를 보내면 만족도 점수를 공개해요.'));
  }
  return summary;
}
