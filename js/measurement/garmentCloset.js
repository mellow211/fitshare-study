// 메인 화면과 등록 화면이 같은 옷 카드, 페이지 나누기, 정보 수정 기능을 씁니다.
import { getUser } from '../auth.js?v=auth-3';
import { createGarmentMetadataFields } from '../garmentMetadata.js?v=skirt-1';
import { openVirtualTryOn } from '../virtualTryOn/index.js';
import { getGarmentExchange } from '../../modules/rankingLogic.js';
import { loadState, pullState, subscribe } from '../../modules/rankingStore.js';

const PAGE_SIZE = 12;
const GARMENT_TYPES = [
  { id: 1, name: '상의 외투' },
  { id: 2, name: '티셔츠' },
  { id: 3, name: '원피스' },
  { id: 4, name: '점퍼' },
  { id: 5, name: '바지' },
  { id: 6, name: '치마' },
  { id: 7, name: '기타' }
];
const GENDER_OPTIONS = [
  { id: 'unknown', name: '모름' },
  { id: 'male', name: '남성용' },
  { id: 'female', name: '여성용' },
  { id: 'unisex', name: '공용' }
];

// onOpen 을 주면 옷 카드를 눌렀을 때 그 옷의 상세 창을 열어요. (주지 않으면 예전처럼 동작)
export function createGarmentCloset({ list, status, pagination, filterControls = null, onOpen }) {
  let garments = [];
  let currentPage = 0;
  let activeFilter = 'all';
  let sharingState = loadState();

  const availableFilterButton = filterControls?.querySelector('[data-closet-filter="available"]');
  const allFilterButton = filterControls?.querySelector('[data-closet-filter="all"]');
  const updateFilterButtons = () => {
    availableFilterButton?.setAttribute('aria-pressed', String(activeFilter === 'available'));
    allFilterButton?.setAttribute('aria-pressed', String(activeFilter === 'all'));
  };
  availableFilterButton?.addEventListener('click', () => {
    activeFilter = 'available';
    currentPage = 0;
    updateFilterButtons();
    render();
  });
  allFilterButton?.addEventListener('click', () => {
    activeFilter = 'all';
    currentPage = 0;
    updateFilterButtons();
    render();
  });
  updateFilterButtons();

  subscribe(() => {
    sharingState = loadState();
    if (activeFilter === 'available') render();
    else updateShareStatuses();
  });

  async function reload(prioritizeId = null) {
    status.textContent = '옷 목록을 불러오고 있어요.';
    list.replaceChildren();
    pagination.replaceChildren();
    try {
      const [response] = await Promise.all([fetch('/api/garments'), pullState()]);
      const result = await response.json().catch(() => []);
      if (!response.ok) throw new Error(result.error || '옷 목록을 불러오지 못했어요.');
      garments = (Array.isArray(result) ? result : [])
        .map((garment, index) => ({ garment, index, createdAt: Date.parse(garment.createdAt) }))
        .sort((a, b) => {
          if (prioritizeId && a.garment.id === prioritizeId) return -1;
          if (prioritizeId && b.garment.id === prioritizeId) return 1;
          const aValid = Number.isFinite(a.createdAt);
          const bValid = Number.isFinite(b.createdAt);
          if (aValid && bValid && a.createdAt !== b.createdAt) return a.createdAt - b.createdAt;
          if (aValid !== bValid) return aValid ? -1 : 1;
          return a.index - b.index;
        })
        .map(({ garment }) => garment);
      sharingState = loadState();
      const visibleGarments = getVisibleGarments();
      currentPage = prioritizeId
        ? 0
        : Math.min(currentPage, Math.max(0, Math.ceil(visibleGarments.length / PAGE_SIZE) - 1));
      render();
    } catch (error) {
      status.textContent = error.message || '옷장 서버에 연결하지 못했어요.';
    }
  }

  function render() {
    list.replaceChildren();
    if (!garments.length) {
      status.textContent = '아직 등록한 옷이 없어요. 첫 번째 옷을 등록해 볼까요?';
      pagination.replaceChildren();
      return;
    }
    const visibleGarments = getVisibleGarments();
    if (!visibleGarments.length) {
      status.textContent = '현재 나눔 가능한 옷이 없어요. 모두 보기에서 전체 옷을 확인해 보세요.';
      pagination.replaceChildren();
      return;
    }
    const pageCount = Math.ceil(visibleGarments.length / PAGE_SIZE);
    const firstIndex = currentPage * PAGE_SIZE;
    const visible = visibleGarments.slice(firstIndex, firstIndex + PAGE_SIZE);
    status.textContent = activeFilter === 'available'
      ? `나눔 가능한 옷 ${visibleGarments.length}벌 중 ${firstIndex + 1}~${firstIndex + visible.length}번째를 보고 있어요.`
      : `옷 ${garments.length}벌 중 ${firstIndex + 1}~${firstIndex + visible.length}번째를 보고 있어요.`;
    for (const garment of visible) list.append(createCard(garment));
    renderPagination(pageCount);
  }

  function getVisibleGarments() {
    return activeFilter === 'available'
      ? garments.filter(garment => getShareStatus(garment).key === 'available')
      : garments;
  }

  function createCard(garment) {
    const card = document.createElement('article');
    card.className = 'school-closet-card';
    card.dataset.garmentId = garment.id;
    const openDetails = () => onOpen?.(garment, reload, {
      hasExchangeHistory: sharingState.exchanges.some(item => item.garmentId === garment.id),
      exchange: getGarmentExchange(sharingState, garment.id),
      users: sharingState.users
    });
    const image = document.createElement('img');
    image.alt = `${garment.name} 사진`;
    image.loading = 'lazy';
    image.decoding = 'async';
    image.width = 200;
    image.height = 200;
    let imageAction = null;
    if (typeof onOpen === 'function') {
      imageAction = document.createElement('button');
      imageAction.type = 'button';
      imageAction.className = 'closet-photo-open';
      imageAction.setAttribute('aria-label', `${garment.name} 상세 보기`);
      imageAction.addEventListener('click', openDetails);
      imageAction.append(image);
    }
    let showingOriginal = false;
    const togglePhoto = document.createElement('button');
    togglePhoto.type = 'button';
    togglePhoto.className = 'closet-secondary-action';
    togglePhoto.textContent = '배경 포함 사진 보기';

    function showCleanPhoto() {
      const id = encodeURIComponent(garment.id);
      const choices = [
        `/api/garments/${id}/thumbnail?v=product-yolo-priority-3&g=${encodeURIComponent(garment.productPhotoGeneratedAt || 'none')}`,
        `/api/garments/${id}/closet-photo`,
        `/api/garments/${id}/photo`
      ];
      let choice = 0;
      image.onerror = () => {
        choice += 1;
        if (choice < choices.length) image.src = choices[choice];
        else image.onerror = null;
      };
      image.src = choices[choice];
    }

    togglePhoto.addEventListener('click', () => {
      showingOriginal = !showingOriginal;
      togglePhoto.textContent = showingOriginal ? '옷만 보기' : '배경 포함 사진 보기';
      image.onerror = null;
      if (showingOriginal) image.src = `/api/garments/${encodeURIComponent(garment.id)}/photo`;
      else showCleanPhoto();
    });
    showCleanPhoto();

    const title = document.createElement('strong');
    title.textContent = garment.name;
    const itemCode = document.createElement('span');
    itemCode.className = 'closet-item-code';
    itemCode.textContent = garment.garmentTypeCode && garment.garmentNumber
      ? `${garment.garmentTypeCode}_${String(garment.garmentNumber).padStart(3, '0')}` : '번호 확인 중';
    const shareStatus = document.createElement('span');
    shareStatus.className = 'closet-share-status';
    shareStatus.dataset.garmentStatus = '';
    const kind = document.createElement('p');
    kind.textContent = garment.garmentTypeName || garment.category || '옷';
    const missing = [!garment.size, !(garment.seasons?.length||garment.season), !garment.style, !garment.condition].filter(Boolean).length;
    if(missing)kind.textContent+=` · 추천 정보 ${missing}개 미등록`;
    const size = document.createElement('p');
    size.textContent = `길이 ${garment.lengthCm}cm · 너비 ${garment.widthCm}cm`;
    size.className = 'closet-measurement-summary';
    const savedSize = String(garment.size ?? '').trim();
    const estimatedSize = document.createElement('p');
    estimatedSize.className = 'closet-estimated-size';
    estimatedSize.textContent = `추정 호수 ${/^\d{2,3}$/.test(savedSize) ? savedSize : '미등록'}`;
    const actions = document.createElement('div');
    actions.className = 'closet-card-actions';
    if (typeof onOpen === 'function') {
      const openDetail = document.createElement('button');
      openDetail.type = 'button';
      openDetail.className = 'closet-open-action';
      openDetail.dataset.closetOpen = garment.id;
      openDetail.addEventListener('click', openDetails);
      actions.append(openDetail);
    }
    if (['top', 'pants'].includes(garment.category)) {
      const tryOn = document.createElement('button');
      tryOn.type = 'button';
      tryOn.className = 'closet-secondary-action closet-tryon-action';
      tryOn.dataset.closetTryon = garment.id;
      tryOn.textContent = '가상으로 입어보기';
      tryOn.setAttribute('aria-label', `${garment.name} 가상으로 입어보기`);
      tryOn.addEventListener('click', () => openVirtualTryOn([garment], {
        owner: card,
        isActive: () => card.isConnected
      }));
      actions.append(tryOn);
    }
    actions.append(togglePhoto);
    const user = getUser();
    const mine = user && (garment.ownerUsername ? garment.ownerUsername === user.username : garment.ownerName === user.name);
    if (user && (user.role === 'teacher' || mine)) {
      const edit = document.createElement('button');
      edit.type = 'button';
      edit.className = 'closet-edit-action';
      edit.textContent = '정보 수정';
      edit.addEventListener('click', () => openEditDialog(garment));
      actions.append(edit);
    }
    card.append(imageAction || image, shareStatus);
    if (garment.isDemo) {
      const demoBadge = document.createElement('span');
      demoBadge.className = 'closet-demo-badge';
      demoBadge.textContent = '가상 옷';
      demoBadge.setAttribute('aria-label', '데모용 가상 옷');
      card.append(demoBadge);
    }
    card.append(itemCode, title, kind, size, estimatedSize, actions);
    updateShareStatus(garment, shareStatus, actions.querySelector('[data-closet-open]'));
    return card;
  }

  function getShareStatus(garment) {
    const exchange = getGarmentExchange(sharingState, garment.id);
    if (exchange?.status === 'reserved') return { key: 'reserved', label: '🟡 예약 중', action: '예약 정보 보기' };
    if (exchange?.status === 'completed') return { key: 'completed', label: '⚪ 나눔 완료', action: '나눔 기록 보기' };
    if (!garment.ownerName) return { key: 'needs-owner', label: '⚪ 정보 필요', action: '옷 정보 확인' };
    return { key: 'available', label: '🟢 나눔 가능', action: '나눔 예약하기' };
  }

  function updateShareStatus(garment, badge, action) {
    const shareStatus = getShareStatus(garment);
    badge.className = `closet-share-status is-${shareStatus.key}`;
    badge.dataset.garmentStatus = shareStatus.key;
    badge.textContent = shareStatus.label;
    badge.setAttribute('aria-label', `나눔 상태: ${shareStatus.label.replace(/^\S+\s/, '')}`);
    if (action) action.textContent = shareStatus.action;
  }

  function updateShareStatuses() {
    for (const card of list.querySelectorAll('.school-closet-card')) {
      const garment = garments.find((item) => item.id === card.dataset.garmentId);
      const badge = card.querySelector('[data-garment-status]');
      if (garment && badge) updateShareStatus(garment, badge, card.querySelector('[data-closet-open]'));
    }
  }

  function renderPagination(pageCount) {
    pagination.replaceChildren();
    if (pageCount <= 1) return;
    const previous = document.createElement('button');
    previous.type = 'button';
    previous.textContent = '이전';
    previous.disabled = currentPage === 0;
    previous.addEventListener('click', () => { currentPage -= 1; render(); });
    const label = document.createElement('span');
    label.textContent = `${currentPage + 1} / ${pageCount} 페이지`;
    const next = document.createElement('button');
    next.type = 'button';
    next.textContent = '다음';
    next.disabled = currentPage >= pageCount - 1;
    next.addEventListener('click', () => { currentPage += 1; render(); });
    pagination.append(previous, label, next);
  }

  function openEditDialog(garment) {
    const dialog = document.createElement('dialog');
    dialog.className = 'closet-edit-dialog';
    const form = document.createElement('form');
    form.className = 'closet-edit-form';
    const heading = document.createElement('h2');
    heading.textContent = '옷 정보를 고쳐요';
    const help = document.createElement('p');
    help.textContent = '모름으로 되어 있는 정보도 여기서 바꿀 수 있어요.';
    const fields = document.createElement('div');
    fields.className = 'closet-edit-fields';
    const name = addInput(fields, '옷 이름', 'name', 'text', garment.name, 60);
    const type = addSelect(fields, '옷 종류', 'garmentTypeId', GARMENT_TYPES, garment.garmentTypeId);
    const gender = addSelect(fields, '누가 입나요?', 'gender', GENDER_OPTIONS, garment.gender || 'unknown');
    const color = addInput(fields, '색깔', 'colorName', 'text', garment.colorName, 30);
    const length = addInput(fields, '길이(cm)', 'lengthCm', 'number', garment.lengthCm, null);
    const width = addInput(fields, '너비(cm)', 'widthCm', 'number', garment.widthCm, null);
    const metadataBox = document.createElement('section');
    const metadataFields = createGarmentMetadataFields(metadataBox, garment);
    fields.append(metadataBox);
    const categoryForType = id => id === 3 ? 'dress' : id === 5 ? 'pants' : id === 6 ? 'skirt' : id === 7 ? 'other' : 'top';
    const suggest = () => metadataFields.suggest({name:name.value,garmentTypeId:Number(type.value),category:categoryForType(Number(type.value))});
    name.addEventListener('input', suggest);
    type.addEventListener('change', suggest);
    suggest();
    for (const input of [length, width]) {
      input.min = '1';
      input.max = '300';
      input.step = '0.1';
    }
    const message = document.createElement('p');
    message.className = 'closet-edit-message';
    message.setAttribute('role', 'status');
    const buttons = document.createElement('div');
    buttons.className = 'closet-edit-buttons';
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.textContent = '취소';
    cancel.addEventListener('click', () => dialog.close());
    const save = document.createElement('button');
    save.type = 'submit';
    save.textContent = '저장';
    buttons.append(cancel, save);
    form.append(heading, help, fields, message, buttons);
    dialog.append(form);
    document.body.append(dialog);
    dialog.addEventListener('close', () => dialog.remove(), { once: true });
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      save.disabled = true;
      message.textContent = '수정한 정보를 저장하고 있어요.';
      const garmentTypeId = Number(type.value);
      const payload = {
        ...metadataFields.read(),
        name: name.value.trim(), garmentTypeId,
        category: categoryForType(garmentTypeId),
        gender: gender.value, colorName: color.value.trim(),
        lengthCm: Number(length.value), widthCm: Number(width.value)
      };
      try {
        const response = await fetch(`/api/garments/${encodeURIComponent(garment.id)}/update`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.error || '정보를 저장하지 못했어요.');
        dialog.close();
        await reload();
      } catch (error) {
        message.textContent = error.message || '저장 서버에 연결하지 못했어요.';
      } finally {
        save.disabled = false;
      }
    });
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.setAttribute('open', '');
  }

  return { reload };
}

function addInput(parent, labelText, name, type, value, maxLength) {
  const label = document.createElement('label');
  label.textContent = labelText;
  const input = document.createElement('input');
  input.name = name;
  input.type = type;
  input.value = value ?? '';
  input.required = true;
  if (maxLength) input.maxLength = maxLength;
  label.append(input);
  parent.append(label);
  return input;
}

function addSelect(parent, labelText, name, choices, value) {
  const label = document.createElement('label');
  label.textContent = labelText;
  const select = document.createElement('select');
  select.name = name;
  select.required = true;
  for (const choice of choices) {
    const option = document.createElement('option');
    option.value = choice.id;
    option.textContent = choice.name;
    option.selected = String(choice.id) === String(value);
    select.append(option);
  }
  label.append(select);
  parent.append(label);
  return select;
}
