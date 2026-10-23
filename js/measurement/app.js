import { loadSession, saveSession, clearSession, createExperiment, saveExperimentRecord, completeExperiment } from './store.js?v=server-storage-1';
import { findClothingBox, inspectMat } from './vision.js?v=auto-photo-1';
import { mapPointerToImagePoint, rectifyPhoto } from './homography.js?v=auto-photo-2';
import { autoProcessPhoto } from './automaticMeasurement.js?v=auto-photo-4';
import { getGarmentInstruction, getMeasurementNames, renderGarmentDiagram, renderGarmentInputGuide } from './garmentGuide.js?v=garment-guides-3';
import { addPerspectiveCorner, clearPerspectiveCorners, getAutomaticFailureMessage, moveBackgroundOrder, normalizeBackgroundOrder, removePerspectiveCorner, resolveShotDraft, shouldOpenManualCorrection } from './shotDraft.js?v=photo-capture-fix-5';
import { loadPhotoImage, releasePhotoImage } from './photoImage.js?v=photo-load-fallback-2';
import { createGarmentInputValues, isGarmentInputDraft, isResumableSession, readGarmentInputDraft } from './garmentDraft.js?v=garment-draft-1';
import { retryPhotoMeasurement } from './photoRetry.mjs?v=photo-retry-1';

const app = document.querySelector('#measurement-app');
const mats = [
  { id: 'M1', name: '동그란 점 배경', tip: '옷을 점 위에 놓고 배경 점을 둘러 보이게 찍어요.', file: '../실측배경/dot_mat_A0.pdf' },
  { id: 'M2', name: '모눈 배경', tip: '격자 선이 반듯하게 보이도록 위에서 찍어요.', file: '../실측배경/grid_mat_A0.pdf' },
  { id: 'M3', name: '테두리 표식 배경', tip: '검은 표식이 있는 네 모서리를 사진 안에 담아요.', file: '../실측배경/charuco_mat_A0_duplex.pdf' },
  { id: 'M4', name: '네 칸 표식 배경', tip: '네 칸 전체가 보이게 찍고, 옷이 칸 경계에 걸치지 않게 해요.', file: '../실측배경/charuco_mat_A0_quad.pdf' }
];
const defaultMatOrder = mats.map((mat) => mat.id);
let session = null;
let draft = null;
let activePointKind = null;
let pointCanvasPhoto = null;
let garmentSaveTimer = null;
let pendingGarmentSave = Promise.resolve();

start();

async function start() {
  try {
    session = await loadSession();
    if (isResumableSession(session)) renderResume();
    else renderStart(session ? '이전 기록은 이 브라우저에만 있어요. 새 실험부터 서버에 저장할 수 있어요.' : '');
  } catch (error) {
    renderStart(error.message);
  }
}

function renderStart(message = '') {
  app.innerHTML = `
    <section class="measurement-panel">
      <p class="eyebrow">FITSHARE 옷 크기 실험</p>
      <h1>네 가지 배경을 비교해요!</h1>
      <p class="measurement-intro">같은 옷을 네 가지 배경에서 찍고, 어떤 배경에서 크기를 더 쉽게 잴 수 있는지 살펴봐요.</p>
      <p class="kid-help"><strong>먼저 할 일</strong><br>실험할 옷의 개수를 고르고, 옷마다 이름과 줄자로 잰 기준 크기를 적어요. 사진과 기록은 FitShare를 실행하는 컴퓨터에 저장돼요.</p>
      ${message ? `<p class="error-message" role="alert">${escapeHtml(message)}</p>` : ''}
      <form id="start-form" class="measurement-form">
        <label>옷은 몇 벌인가요? (1~30벌)<input name="count" type="number" min="1" max="30" value="2" required></label>
        <button class="primary-action" type="submit">옷 정보 적기</button>
      </form>
    </section>`;
  app.querySelector('#start-form').addEventListener('submit', (event) => {
    event.preventDefault();
    const count = Number(new FormData(event.currentTarget).get('count'));
    if (!Number.isInteger(count) || count < 1 || count > 30) return renderStart('옷은 1벌부터 30벌까지 고를 수 있어요.');
    renderGarments(count);
  });
}

function renderGarments(count, savedGarments = [], savedMatOrder = defaultMatOrder) {
  let matOrder = normalizeBackgroundOrder(savedMatOrder, defaultMatOrder);
  let inputValues;
  try { inputValues = createGarmentInputValues(count, savedGarments); }
  catch (error) { return renderStart(error.message); }
  app.innerHTML = `
    <section class="measurement-panel">
      <div class="step-topline"><span>시작 준비</span><span>1 / 3</span></div>
      <h1>옷 이름과 기준 크기를 적어요</h1>
      <p class="kid-help">줄자로 옷을 직접 재어 적어 주세요. 입력 중인 이름과 종류, 크기는 이 기기에 자동 저장돼요. 사진으로 잰 값과 나중에 비교해요.</p>
      <form id="garment-form">
        <div class="garment-list">${inputValues.map((garment, index) => `
          <article class="garment-card" data-index="${index}">
            <h2 class="garment-card-heading">옷 ${index + 1}</h2>
            <label class="garment-name">이름<input name="name-${index}" maxlength="24" value="${escapeHtml(garment.name)}" required></label>
            <label class="garment-kind">옷 종류<select name="category-${index}">${[
              ['top', '상의·외투'], ['bottom', '하의'], ['dress', '원피스'], ['pants', '바지'], ['skirt', '치마'], ['other', '기타']
            ].map(([value, label]) => `<option value="${value}"${garment.category === value ? ' selected' : ''}>${label}</option>`).join('')}</select></label>
            <label class="length-label"><span>기장(cm)</span><input name="length-${index}" type="number" min="1" max="300" step="0.1" placeholder="예: 55" value="${escapeHtml(garment.length)}" required></label>
            <label>너비(cm)<input name="width-${index}" type="number" min="1" max="300" step="0.1" placeholder="예: 42" value="${escapeHtml(garment.width)}" required></label>
            <label class="collar-length-field"${['top', 'dress'].includes(garment.category) ? '' : ' hidden'}>목점에서 밑단까지(cm)<input name="collarLength-${index}" type="number" min="1" max="300" step="0.1" placeholder="예: 50" value="${escapeHtml(garment.collarLength)}"${['top', 'dress'].includes(garment.category) ? ' required' : ''}><small>목 가장 높은 점에서 옷 밑단까지 재요.</small></label>
            <div class="garment-input-guide" data-guide-index="${index}"></div>
          </article>`).join('')}</div>
        <section class="mat-order-picker" aria-labelledby="mat-order-title">
          <h2 id="mat-order-title">배경 촬영 순서</h2>
          <p class="analysis-note">네 배경을 모두 사용해요. 위·아래 버튼으로 순서를 정해 주세요.</p>
          <ol id="mat-order-list" class="mat-order-list"></ol>
        </section>
        <p class="analysis-note">길이와 너비는 줄자로 잰 기준값이에요. 자동 사진 인식이 어려우면 촬영 뒤 직접 확인하고 고칠 수 있어요.</p>
        <p class="analysis-note">상의·원피스는 어깨와 목이 만나는 점에서 밑단까지도 재요. 바지 너비는 허리 고무줄 아래 5cm 지점에서 재요.</p>
        <p id="garment-save-status" class="analysis-note" role="status">입력 내용은 이 기기에 임시 저장돼요.</p>
        <div class="button-row"><button type="button" class="quiet-action" data-action="save-and-leave">저장하고 나중에 계속하기</button><button class="primary-action" type="submit">${matOrder[0]} 촬영 시작</button></div>
      </form>
    </section>`;
  const formElement = app.querySelector('#garment-form');
  const renderMatOrder = () => {
    const list = app.querySelector('#mat-order-list');
    list.innerHTML = matOrder.map((matId, index) => {
      const mat = mats.find((item) => item.id === matId);
      return `<li><span><strong>${index + 1}.</strong> ${mat.id} · ${mat.name}</span><span class="mat-order-actions"><button type="button" class="quiet-action" data-move-mat="up" data-index="${index}" aria-label="${mat.name} 순서를 위로 이동" ${index === 0 ? 'disabled' : ''}>위로</button><button type="button" class="quiet-action" data-move-mat="down" data-index="${index}" aria-label="${mat.name} 순서를 아래로 이동" ${index === matOrder.length - 1 ? 'disabled' : ''}>아래로</button></span></li>`;
    }).join('');
  };
  renderMatOrder();
  app.querySelector('#mat-order-list').addEventListener('click', (event) => {
    const button = event.target.closest('[data-move-mat]');
    if (!button || button.disabled) return;
    const from = Number(button.dataset.index);
    const to = from + (button.dataset.moveMat === 'up' ? -1 : 1);
    matOrder = moveBackgroundOrder(matOrder, from, to);
    renderMatOrder();
    queueGarmentDraftSave(formElement, count, true, matOrder).catch(() => {});
  });
  const updateGuide = (card, index) => {
    const category = card.querySelector('.garment-kind select').value;
    card.querySelector('.garment-input-guide').innerHTML = renderGarmentInputGuide(category, index);
    const collarField = card.querySelector('.collar-length-field');
    const collarInput = collarField.querySelector('input');
    const needsCollarLength = category === 'top' || category === 'dress';
    card.querySelector('.length-label span').textContent = needsCollarLength ? '어깨·목 만나는 점에서 밑단(cm)' : '기장(cm)';
    collarField.hidden = !needsCollarLength;
    collarInput.required = needsCollarLength;
  };
  app.querySelectorAll('.garment-card').forEach((card, index) => updateGuide(card, index));
  formElement.addEventListener('input', () => queueGarmentDraftSave(formElement, count, false, matOrder));
  formElement.addEventListener('change', (event) => {
    const card = event.target.closest('.garment-card');
    if (card && event.target.matches('.garment-kind select')) updateGuide(card, Number(card.dataset.index));
    queueGarmentDraftSave(formElement, count, true, matOrder).catch(() => {});
  });
  formElement.addEventListener('submit', async (event) => {
    event.preventDefault();
    let inputDraft;
    try { inputDraft = await saveGarmentDraftNow(formElement, count, matOrder); }
    catch (error) { setGarmentSaveStatus(error.message, true); return; }
    const garments = inputDraft.garments.map((garment, index) => ({
      id: `garment-${index + 1}`,
      name: garment.name.trim(),
      category: garment.category,
      referenceLengthCm: Number(garment.length),
      referenceWidthCm: Number(garment.width),
      ...(garment.category === 'top' || garment.category === 'dress'
        ? { referenceCollarToHemCm: Number(garment.collarLength) } : {})
    }));
    if (garments.some((garment) => !garment.name || !Number.isFinite(garment.referenceLengthCm) || garment.referenceLengthCm <= 0 || !Number.isFinite(garment.referenceWidthCm) || garment.referenceWidthCm <= 0 || ('referenceCollarToHemCm' in garment && (!Number.isFinite(garment.referenceCollarToHemCm) || garment.referenceCollarToHemCm <= 0)))) {
      return renderGarmentsWithError(count, '이름과 기준 크기를 모두 적어 주세요. 길이와 너비는 0보다 커야 해요.', inputDraft.garments, inputDraft.matOrder);
    }
    try {
      const saved = await createExperiment(garments);
      session = { id: 'current', serverId: saved.id, round: saved.round, garments, matOrder: inputDraft.matOrder, matIndex: 0, garmentIndex: 0, records: [], createdAt: saved.createdAt };
      await saveSession(session);
      renderShot();
    } catch (error) {
      const note = app.querySelector('.analysis-note'); note.classList.add('error-message'); note.textContent = error.message;
    }
  });
  app.querySelector('[data-action="save-and-leave"]').addEventListener('click', async () => {
    try {
      await saveGarmentDraftNow(formElement, count, matOrder);
      renderResume();
    } catch (error) {
      setGarmentSaveStatus(error.message, true);
    }
  });
  queueGarmentDraftSave(formElement, count, true, matOrder).catch(() => {});
}

function renderGarmentsWithError(count, message, savedGarments = [], savedMatOrder = defaultMatOrder) {
  renderGarments(count, savedGarments, savedMatOrder);
  const note = app.querySelector('.analysis-note');
  note.classList.add('error-message'); note.textContent = message;
}

function queueGarmentDraftSave(formElement, count, saveNow = false, matOrder = defaultMatOrder) {
  const snapshot = readGarmentInputDraft(new FormData(formElement), count, matOrder);
  session = snapshot;
  if (garmentSaveTimer) clearTimeout(garmentSaveTimer);
  const save = () => {
    const saving = pendingGarmentSave.catch(() => {}).then(() => saveSession(snapshot));
    pendingGarmentSave = saving;
    saving.then(() => {
      if (session === snapshot) setGarmentSaveStatus('옷 정보가 이 기기에 저장됐어요.');
    }).catch((error) => {
      if (session === snapshot) setGarmentSaveStatus(error.message, true);
    });
    return saving;
  };
  if (saveNow) return save();
  setGarmentSaveStatus('옷 정보를 저장하고 있어요…');
  garmentSaveTimer = setTimeout(() => { garmentSaveTimer = null; save().catch(() => {}); }, 300);
  return Promise.resolve();
}

async function saveGarmentDraftNow(formElement, count, matOrder = defaultMatOrder) {
  if (garmentSaveTimer) { clearTimeout(garmentSaveTimer); garmentSaveTimer = null; }
  await queueGarmentDraftSave(formElement, count, true, matOrder);
  return session;
}

function setGarmentSaveStatus(message, isError = false) {
  const status = app.querySelector('#garment-save-status');
  if (!status) return;
  status.textContent = message;
  status.classList.toggle('error-message', isError);
}

function renderResume() {
  const inputDraft = isGarmentInputDraft(session);
  const complete = !inputDraft && session.matIndex >= mats.length;
  const currentMat = !inputDraft && !complete ? getCurrentMat() : null;
  app.innerHTML = `
    <section class="measurement-panel">
      <p class="eyebrow">${inputDraft ? '옷 정보 임시 저장' : `${session.round ? `${session.round}회차` : '저장된'} 실험 내용`}</p>
      <h1>${inputDraft ? '옷 정보 입력을 이어서 해요' : complete ? '네 배경 촬영을 마쳤어요!' : `${currentMat.id} 촬영 차례예요`}</h1>
      <p class="kid-help">${inputDraft ? '이 기기에 저장된 옷 이름과 기준 크기를 찾았어요. 이어서 적거나 새 실험을 시작할 수 있어요.' : '이 기기에 저장된 사진과 진행 상황을 찾았어요. 이어서 하거나 결과를 살펴보세요.'}</p>
      <div class="button-row">
        ${inputDraft ? '<button class="primary-action" data-action="resume-garments">옷 정보 입력 이어 하기</button>' : complete ? `<a class="primary-action" href="../results/index.html?run=${encodeURIComponent(session.serverId)}">사진과 결과 보기</a>` : '<button class="primary-action" data-action="resume">지난 회차 촬영 이어 하기</button>'}
        <a class="secondary-action" href="../results/sessions.html">저장된 실험 회차 보기</a>
        <button class="quiet-action" data-action="reset">${inputDraft ? '저장한 입력 지우고 새로 시작' : '새 실험 시작'}</button>
      </div>
      <p class="analysis-note">옷 입력은 이 기기에 임시 저장되고, 촬영을 시작하면 실험 회차와 사진이 서버 PC에 저장돼요. 새 실험은 새 회차로 시작합니다.</p>
    </section>`;
  app.querySelector('[data-action="resume-garments"]')?.addEventListener('click', () => renderGarments(session.garments.length, session.garments, session.matOrder));
  app.querySelector('[data-action="resume"]')?.addEventListener('click', renderShot);
  app.querySelector('[data-action="reset"]').addEventListener('click', async () => {
    if (!confirm(inputDraft ? '저장된 옷 입력을 지우고 새 실험을 시작할까요?' : '이 기기의 이어하기 내용을 지우고 새 실험을 시작할까요?')) return;
    await clearSession(); session = null; draft = null; renderStart();
  });
}

async function renderShot({ preserveCurrentDraft = false } = {}) {
  activePointKind = null;
  pointCanvasPhoto = null;
  const mat = getCurrentMat();
  const garment = session.garments[session.garmentIndex];
  const totalSteps = mats.length * session.garments.length;
  const doneSteps = session.matIndex * session.garments.length + session.garmentIndex;
  const percent = Math.round((doneSteps / totalSteps) * 100);
  draft = resolveShotDraft(draft, session.records, mat.id, garment.id, preserveCurrentDraft);
  if (draft && !(draft.photo instanceof Blob) && draft.photoUrl) {
    try {
      const response = await fetch(draft.photoUrl);
      if (!response.ok) throw new Error();
      draft.photo = await response.blob();
    } catch {
      draft = null;
    }
  }
  app.innerHTML = `
    <section class="measurement-panel">
      <div class="step-topline"><span>${mat.id} · ${mat.name}</span><span>옷 ${session.garmentIndex + 1} / ${session.garments.length}</span></div>
      <div class="progress-track" aria-label="전체 ${percent}% 완료"><span style="width:${percent}%"></span></div>
      <h1>${escapeHtml(garment.name)} 사진을 찍어요</h1>
      <p class="kid-help"><strong>이렇게 해요</strong><br>${mat.tip} 휴대폰을 최대한 위에 들고, 옷과 배경의 네 모서리가 모두 보이게 찍어요. 사진을 고르면 기울기와 옷 길이를 먼저 자동으로 찾아요. 자동 측정 숫자는 사진을 보고 고칠 수 있어요.</p>
      <p class="measurement-guide"><strong>줄자 기준점</strong><br>${getGarmentInstruction(garment.category)}</p>
      ${renderGarmentDiagram(garment.category)}
      <p class="analysis-note">사진 펴기는 매트와 옷이 한 바닥에 평평하게 놓였을 때 도움이 돼요. 옷이 접히거나 들떠 있으면 카메라 각도만으로 바로잡을 수 없으니 옷을 펴고 다시 찍어요.</p>
      <p class="analysis-note">자동 길이 계산은 A0 배경 PDF를 원본 크기 100%로 인쇄했을 때 가장 알맞아요. 배경 네 모서리가 잘 보이게 찍어 주세요.</p>
      <article class="mat-card"><div class="mat-preview" data-mat="${mat.id}" aria-label="${mat.name} 그림"><span>${mat.id}</span></div><div><strong>${mat.name}</strong><p class="analysis-note">같은 옷을 네 배경에서 같은 방향으로 찍으면 비교하기 쉬워요.</p><a class="secondary-action" href="${mat.file}" target="_blank" rel="noopener">배경 인쇄 파일 보기</a></div></article>
      ${draft ? `<canvas id="photo-canvas" class="photo-preview" aria-label="사진 위에 측정점을 표시하는 그림"></canvas>
        <details class="manual-correction" ${shouldOpenManualCorrection(draft) ? 'open' : ''}>
          <summary>직접 고치거나 길이를 다시 재고 싶어요</summary>
          <div class="manual-correction-content">
            <section class="point-picker"><strong>사진에서 직접 길이를 재요</strong><p class="analysis-note">사진 속에 이미 아는 길이가 있을 때만 아래 점 찍기를 사용하세요. 자동으로 잰 값이 있으면 확인하고 바로 다음으로 가면 돼요.</p>
            <label class="reference-length">배경에서 아는 길이(cm)<input id="reference-length" type="number" min="0.1" step="0.1" value="${mat.id === 'M1' ? '10' : mat.id === 'M2' ? '5' : ''}" placeholder="예: 10"></label>
            <div class="button-row"><button type="button" class="quiet-action" data-points="scale">① 배경 기준 두 점</button><button type="button" class="quiet-action" data-points="length">② 옷 길이 양끝</button><button type="button" class="quiet-action" data-points="width">③ 옷 너비 양끝</button></div><p id="point-help" class="analysis-note" aria-live="polite">직접 재려면 버튼을 누르고 사진에서 선의 양끝을 차례로 눌러 주세요.</p></section>
          </div>
        </details>` : ''}
      <div class="photo-source-options">
        <label class="photo-picker">사진첩에서 고르기<input id="photo-input-gallery" type="file" accept="image/*,.heic,.heif"></label>
        <label class="photo-picker">카메라로 촬영<input id="photo-input-camera" type="file" accept="image/*" capture="environment"></label>
      </div>
      <div id="photo-analysis">${draft ? analysisHtml(draft) : '<p class="analysis-note">사진을 고르면 옷 테두리를 찾아 볼게요.</p>'}</div>
      ${draft ? `<form id="measurement-form"><div class="dimension-fields">
        <label>사진에서 잰 ${getMeasurementNames(garment.category).length}(cm)<input class="measure-input" name="length" type="number" min="0.1" max="300" step="0.1" value="${draft.measuredLengthCm ?? ''}" required></label>
        <label>사진에서 잰 ${getMeasurementNames(garment.category).width}(cm)<input class="measure-input" name="width" type="number" min="0.1" max="300" step="0.1" value="${draft.measuredWidthCm ?? ''}" required></label>
      </div><p class="analysis-note">처음 입력한 값은 줄자로 잰 기준값이고, 아래 두 칸은 사진에서 자동으로 잰 값이에요. 두 값을 비교해 사진 측정이 얼마나 가까운지 확인해요. 자동 측정이 비어 있으면 사진을 보고 값을 적어 주세요.</p></form>` : ''}
      <div class="button-row">
        <button class="quiet-action" data-action="previous">이전 옷</button>
        ${draft ? '<button class="primary-action" data-action="save-next">사진 저장하고 다음으로</button>' : ''}
        <button class="quiet-action" data-action="exit">나중에 계속하기</button>
      </div>
      <div id="message" aria-live="polite"></div>
    </section>`;
  app.querySelector('#photo-input-gallery').addEventListener('change', handlePhoto);
  app.querySelector('#photo-input-camera').addEventListener('change', handlePhoto);
  app.querySelector('[data-action="retry-measurement"]')?.addEventListener('click', retryMeasurement);
  app.querySelector('[data-action="perspective"]')?.addEventListener('click', openPerspectiveTool);
  app.querySelector('[data-action="reset-perspective"]')?.addEventListener('click', () => {
    draft.photo = draft.rawPhoto;
    draft.fullPhoto = null;
    draft.fullOutline = null;
    draft.outline = null;
    draft.corrected = false;
    draft.points = { scale: [], length: [], width: [] };
    draft.measuredLengthCm = null;
    draft.measuredWidthCm = null;
    draft.autoMeasurement = { ok: false, message: '사진 기울기 보정을 초기화했어요. 다시 사진을 골라 주세요.' };
    renderShot({ preserveCurrentDraft: true });
  });
  app.querySelectorAll('[data-points]').forEach((button) => button.addEventListener('click', () => beginPointSelection(button.dataset.points)));
  app.querySelector('#reference-length')?.addEventListener('input', updateMeasuredFromPoints);
  app.querySelector('[data-action="previous"]').addEventListener('click', movePrevious);
  app.querySelector('[data-action="save-next"]')?.addEventListener('click', saveAndMove);
  app.querySelector('[data-action="exit"]').addEventListener('click', renderResume);
  if (draft) drawPhotoCanvas(draft.photo);
}

async function handlePhoto(event) {
  const picker = event.currentTarget;
  const file = event.target.files?.[0];
  picker.value = '';
  if (!file) return;
  if (!file.type.startsWith('image/') && !/\.(jpe?g|png|webp|heic|heif)$/i.test(file.name)) return showMessage('JPG, PNG 같은 사진 파일을 골라 주세요.');
  if (file.size > 15 * 1024 * 1024) return showMessage('사진이 너무 커요. 15MB보다 작은 사진을 골라 주세요.');
  app.querySelector('#photo-analysis').innerHTML = '<p class="analysis-note" role="status">사진을 읽고 매트 기울기와 옷 길이를 살펴보고 있어요…</p>';
  try {
    const bitmap = await loadPhotoImage(file);
    const canvas = document.createElement('canvas');
    try {
      const ratio = Math.min(1, 1200 / Math.max(bitmap.width, bitmap.height));
      canvas.width = Math.round(bitmap.width * ratio); canvas.height = Math.round(bitmap.height * ratio);
      canvas.getContext('2d', { willReadFrequently: true }).drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    } finally {
      releasePhotoImage(bitmap);
    }
    const context = canvas.getContext('2d', { willReadFrequently: true });
    const imageData = context.getImageData(0, 0, canvas.width, canvas.height);
    const mat = getCurrentMat();
    const matCheck = inspectMat(imageData, mat.id);
    const outline = findClothingBox(imageData);
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.82));
    if (!blob) return showMessage('사진을 읽지 못했어요. 다른 사진으로 다시 해 주세요.');
    const garment = session.garments[session.garmentIndex];
    let automatic;
    try { automatic = await autoProcessPhoto(blob, garment.category, mat.id); }
    catch (error) { automatic = { ok: false, photo: blob, corrected: false, message: getAutomaticFailureMessage(error) }; }
    draft = {
      id: `${mat.id}-${garment.id}`, matId: mat.id, garmentId: garment.id,
      photo: automatic.photo || blob, rawPhoto: blob, corrected: Boolean(automatic.corrected),
      fullPhoto: automatic.fullPhoto || null, fullOutline: automatic.fullOutline || null,
      points: { scale: [], length: [], width: [] }, matCheck,
      outline: automatic.corrected ? automatic.outline : (automatic.outline?.ok ? automatic.outline : outline),
      autoMeasurement: { ok: automatic.ok, lengthCm: automatic.lengthCm, widthCm: automatic.widthCm, confidence: automatic.confidence, message: automatic.message },
      measuredLengthCm: automatic.ok ? automatic.lengthCm : null, measuredWidthCm: automatic.ok ? automatic.widthCm : null,
      edited: false, status: 'done', createdAt: new Date().toISOString()
    };
    renderShot({ preserveCurrentDraft: true });
  } catch (error) {
    showMessage(error.message || '사진을 열지 못했어요. JPG나 PNG 사진을 다시 골라 주세요.');
  }
}

async function retryMeasurement(event) {
  const button = event.currentTarget;
  if (!draft?.photo) return;
  button.disabled = true;
  button.textContent = '사진을 다시 살펴보고 있어요…';
  const garment = session.garments[session.garmentIndex];
  draft = await retryPhotoMeasurement(draft, autoProcessPhoto, garment.category, getCurrentMat().id);
  renderShot({ preserveCurrentDraft: true });
}

async function drawPhotoCanvas(photo) {
  const canvas = app.querySelector('#photo-canvas');
  pointCanvasPhoto = photo;
  const bitmap = await loadPhotoImage(photo);
  try {
    canvas.width = bitmap.width; canvas.height = bitmap.height;
    canvas.getContext('2d').drawImage(bitmap, 0, 0);
  } finally {
    releasePhotoImage(bitmap);
  }
  drawSavedPoints();
  if (canvas.dataset.pointClickBound === 'true') return;
  canvas.dataset.pointClickBound = 'true';
  canvas.addEventListener('click', (event) => {
    if (!activePointKind) return;
    const point = mapCanvasClick(event, canvas);
    if (!point) return;
    const points = draft.points[activePointKind];
    if (points.length >= 2) points.length = 0;
    points.push(point);
    drawSavedPoints();
    if (points.length === 2) {
      activePointKind = null;
      app.querySelector('#point-help').textContent = '두 점을 골랐어요. 다른 선도 잴 수 있어요.';
      updateMeasuredFromPoints();
    } else app.querySelector('#point-help').textContent = '좋아요! 이제 같은 선의 반대쪽 끝을 눌러 주세요.';
  });
}

function beginPointSelection(kind) {
  activePointKind = kind;
  draft.points ||= { scale: [], length: [], width: [] };
  if (draft.fullPhoto) drawPhotoCanvas(draft.fullPhoto);
  app.querySelector('#point-help').textContent = '사진에서 첫 번째 점을 눌러 주세요. 사진이 화면에 맞춰 줄어들어도 점 위치를 맞춰 표시해요.';
}

function drawSavedPoints() {
  const canvas = app.querySelector('#photo-canvas');
  const context = canvas.getContext('2d');
  const image = new Image();
  const imageUrl = URL.createObjectURL(pointCanvasPhoto || draft.photo);
  image.onload = () => {
    context.clearRect(0, 0, canvas.width, canvas.height); context.drawImage(image, 0, 0, canvas.width, canvas.height);
    const colors = { scale: '#f4a62a', length: '#e94f7b', width: '#3976d9' };
    for (const [kind, points] of Object.entries(draft.points || {})) {
      context.strokeStyle = colors[kind]; context.fillStyle = colors[kind]; context.lineWidth = Math.max(3, canvas.width / 300);
      if (points.length === 2) { context.beginPath(); context.moveTo(points[0].x, points[0].y); context.lineTo(points[1].x, points[1].y); context.stroke(); }
      points.forEach((point, index) => { context.beginPath(); context.arc(point.x, point.y, Math.max(7, canvas.width / 150), 0, Math.PI * 2); context.fill(); context.fillStyle = '#fff'; context.font = `bold ${Math.max(12, canvas.width / 50)}px sans-serif`; context.fillText(`${kind[0].toUpperCase()}${index + 1}`, point.x + 5, point.y - 5); context.fillStyle = colors[kind]; });
    }
    const outline = pointCanvasPhoto === draft.fullPhoto ? draft.fullOutline : draft.outline;
    if (outline?.ok && outline.box) {
      const { left, top, right, bottom } = outline.box;
      context.strokeStyle = '#45a982'; context.lineWidth = Math.max(4, canvas.width / 180); context.setLineDash([12, 7]);
      context.strokeRect(left, top, right - left, bottom - top); context.setLineDash([]);
    }
    URL.revokeObjectURL(imageUrl);
  };
  image.onerror = () => URL.revokeObjectURL(imageUrl);
  image.src = imageUrl;
}

function updateMeasuredFromPoints() {
  const scalePoints = draft.points.scale;
  const realCm = Number(app.querySelector('#reference-length').value);
  if (scalePoints.length !== 2 || !Number.isFinite(realCm) || realCm <= 0) return;
  const pixelLength = Math.hypot(scalePoints[1].x - scalePoints[0].x, scalePoints[1].y - scalePoints[0].y);
  if (pixelLength <= 0) return showMessage('배경 기준 두 점이 겹쳐 있어요. 다시 골라 주세요.');
  const cmPerPixel = realCm / pixelLength;
  const form = app.querySelector('#measurement-form');
  for (const [kind, inputName] of [['length', 'length'], ['width', 'width']]) {
    const points = draft.points[kind];
    if (points?.length === 2) form.elements[inputName].value = (Math.hypot(points[1].x - points[0].x, points[1].y - points[0].y) * cmPerPixel).toFixed(1);
  }
}

function openPerspectiveTool() {
  const holder = app.querySelector('#perspective-tool');
  const sourcePhoto = draft.fullPhoto || draft.rawPhoto || draft.photo;
  const image = new Image();
  image.onload = () => {
    const scale = Math.min(1, 850 / image.width);
    const canvas = document.createElement('canvas'); canvas.width = Math.round(image.width * scale); canvas.height = Math.round(image.height * scale);
    canvas.className = 'corner-canvas'; canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height);
    let marks = []; const context = canvas.getContext('2d');
    holder.innerHTML = '<p class="kid-help">자동 보정이 안 될 때만 사용해요. 네 모서리를 왼쪽 위부터 시계 방향으로 누르세요. 잘못 찍은 점은 번호를 눌러 하나씩 지울 수 있어요.</p><p id="corner-count" class="analysis-note" aria-live="polite">점 0 / 4</p><div id="corner-point-actions" class="corner-point-actions" aria-label="찍은 점 지우기"></div>';
    holder.append(canvas);
    const actions = document.createElement('div'); actions.className = 'button-row'; holder.append(actions);
    const apply = document.createElement('button'); apply.type = 'button'; apply.className = 'primary-action'; apply.textContent = '네 점으로 사진 펴기'; apply.disabled = true; actions.append(apply);
    const reset = document.createElement('button'); reset.type = 'button'; reset.className = 'quiet-action'; reset.textContent = '점 지우고 다시 찍기'; reset.disabled = true; actions.append(reset);
    const help = holder.querySelector('#corner-count');
    const pointActions = holder.querySelector('#corner-point-actions');
    const renderPointActions = () => {
      pointActions.replaceChildren();
      marks.forEach((_, index) => {
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'quiet-action';
        remove.textContent = `${index + 1}번 점 지우기`;
        remove.setAttribute('aria-label', `${index + 1}번 모서리 점 지우기`);
        remove.addEventListener('click', () => {
          marks = removePerspectiveCorner(marks, index);
          drawMarks();
        });
        pointActions.append(remove);
      });
    };
    const drawMarks = () => {
      context.clearRect(0, 0, canvas.width, canvas.height);
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
      marks.forEach((point, index) => {
        const x = point.x * scale; const y = point.y * scale;
        context.fillStyle = '#e94f7b'; context.beginPath(); context.arc(x, y, 9, 0, Math.PI * 2); context.fill();
        context.fillStyle = '#fff'; context.font = 'bold 12px sans-serif'; context.fillText(String(index + 1), x - 4, y + 4);
      });
      help.textContent = `점 ${marks.length} / 4`;
      apply.disabled = marks.length !== 4;
      reset.disabled = marks.length === 0;
      renderPointActions();
    };
    canvas.addEventListener('click', (event) => {
      if (marks.length >= 4) return;
      const point = mapCanvasClick(event, canvas);
      if (!point) return;
      marks = addPerspectiveCorner(marks, { x: point.x / scale, y: point.y / scale });
      drawMarks();
    });
    reset.addEventListener('click', () => { marks = clearPerspectiveCorners(); drawMarks(); });
    apply.addEventListener('click', async () => {
      try { draft.photo = await rectifyPhoto(sourcePhoto, marks); draft.fullPhoto = draft.photo; draft.fullOutline = null; draft.outline = null; draft.corrected = true; draft.points = { scale: [], length: [], width: [] }; draft.measuredLengthCm = null; draft.measuredWidthCm = null; renderShot({ preserveCurrentDraft: true }); }
      catch (error) { showMessage(error.message || '네 모서리를 다시 골라 주세요.'); }
    });
  };
  image.onerror = () => showMessage('사진을 열지 못했어요. 다시 찍어 주세요.');
  image.src = URL.createObjectURL(sourcePhoto);
}

function analysisHtml(record) {
  const outline = record.outline?.ok ? `옷으로 보이는 부분을 네모로 찾았어요. 테두리 크기는 약 ${Math.round(record.outline.confidence * 100)}% 확신이에요.` : record.outline?.message || '옷 테두리를 아직 찾지 못했어요.';
  const mat = record.matCheck?.message || '사진 속 밝고 어두운 부분을 살펴봤어요.';
  const automatic = record.autoMeasurement?.message || '기울기와 길이를 자동으로 찾지 못했어요. 아래 숫자를 직접 입력해 주세요.';
  const success = Boolean(record.corrected && record.autoMeasurement?.ok);
  return `<p class="${success ? 'automatic-success' : 'analysis-note'}" role="status"><strong>${success ? '자동 보정과 측정 완료' : '사진 자동 확인'}</strong><br>${escapeHtml(automatic)}<br>${escapeHtml(mat)}<br>${escapeHtml(outline)}<br>${success ? '숫자를 확인하고 사진 저장하고 다음으로를 누르면 돼요.' : '같은 사진을 다시 분석하거나 위의 직접 보정 도구에서 고칠 수 있어요.'}</p>${success ? '' : '<button type="button" class="quiet-action retry-measurement" data-action="retry-measurement">같은 사진 다시 측정</button>'}`;
}

async function saveAndMove() {
  const form = new FormData(app.querySelector('#measurement-form'));
  const length = Number(form.get('length')); const width = Number(form.get('width'));
  if (!Number.isFinite(length) || length <= 0 || !Number.isFinite(width) || width <= 0) return showMessage('길이와 너비는 0보다 큰 숫자로 적어 주세요.');
  const garment = session.garments[session.garmentIndex];
  draft.measuredLengthCm = length; draft.measuredWidthCm = width;
  draft.errorLengthCm = length - garment.referenceLengthCm;
  draft.errorWidthCm = width - garment.referenceWidthCm;
  draft.edited = length !== garment.referenceLengthCm || width !== garment.referenceWidthCm;
  try {
    const savedRecord = await saveExperimentRecord(session.serverId, draft);
    draft.photoUrl = savedRecord.photoUrl;
  } catch (error) {
    return showMessage(error.message);
  }
  const at = session.records.findIndex((item) => item.id === draft.id);
  if (at >= 0) session.records[at] = draft; else session.records.push(draft);
  if (session.matIndex === mats.length - 1 && session.garmentIndex === session.garments.length - 1) {
    try {
      await completeExperiment(session.serverId);
      session.matIndex = mats.length;
      await saveSession(session);
      location.href = `../results/index.html?run=${encodeURIComponent(session.serverId)}`;
    } catch (error) { showMessage(error.message); }
    return;
  }
  if (session.garmentIndex + 1 < session.garments.length) session.garmentIndex++;
  else { session.matIndex++; session.garmentIndex = 0; }
  try { await persistAndRender(); }
  catch (error) { showMessage(error.message); }
}

function movePrevious() {
  if (session.garmentIndex > 0) session.garmentIndex--;
  else if (session.matIndex > 0) { session.matIndex--; session.garmentIndex = session.garments.length - 1; }
  renderShot();
}

async function persistAndRender() {
  try {
    await saveSession(session);
    if (session.matIndex >= mats.length) { location.href = '../results/index.html'; return; }
    renderShot();
  } catch (error) { renderStart(error.message); }
}

function showMessage(message) {
  const target = app.querySelector('#message');
  if (target) target.innerHTML = `<p class="error-message" role="alert">${escapeHtml(message)}</p>`;
}

function getCurrentMat() {
  const order = normalizeBackgroundOrder(session.matOrder, defaultMatOrder);
  session.matOrder = order;
  return mats.find((mat) => mat.id === order[session.matIndex]) || mats[0];
}

function mapCanvasClick(event, canvas) {
  const rect = canvas.getBoundingClientRect();
  const style = getComputedStyle(canvas);
  const borderLeft = Number.parseFloat(style.borderLeftWidth) || 0;
  const borderRight = Number.parseFloat(style.borderRightWidth) || 0;
  const borderTop = Number.parseFloat(style.borderTopWidth) || 0;
  const borderBottom = Number.parseFloat(style.borderBottomWidth) || 0;
  return mapPointerToImagePoint(
    event.clientX,
    event.clientY,
    {
      left: rect.left + borderLeft,
      top: rect.top + borderTop,
      width: rect.width - borderLeft - borderRight,
      height: rect.height - borderTop - borderBottom
    },
    canvas.width,
    canvas.height
  );
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);
}

