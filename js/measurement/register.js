import { readRegistrationPhoto } from './registrationPhoto.js?v=garment-registration-2';
import { createGarmentCloset } from './garmentCloset.js?v=closet-tools-19';
import { fetchMe } from '../auth.js?v=auth-3';
import { createRegistrationCamera } from './registrationCamera.js?v=registration-camera-1';
import { createGarmentMetadataFields } from '../garmentMetadata.js?v=skirt-1';

const photoInputs = [document.querySelector('#camera-photo'), document.querySelector('#gallery-photo')];
const galleryInput = document.querySelector('#gallery-photo');
const photoPreview = document.querySelector('#garment-preview');
const analyzeButton = document.querySelector('#analyze-garment');
const manualButton = document.querySelector('#manual-registration');
const analysisStatus = document.querySelector('#analysis-status');
const progressBox = document.querySelector('#registration-progress');
const progressStep = document.querySelector('#registration-progress-step');
const progressTime = document.querySelector('#registration-progress-time');
const progressBar = document.querySelector('#registration-progress-bar');
const progressTrack = progressBar.parentElement;
let progressTimer = null;

// 서버는 한 번에 결과만 돌려주므로, 단계는 걸리는 시간을 어림해 보여 주는 안내예요. 끝나면 100%로 채워요.
function startProgress(steps) {
  stopProgress();
  const startedAt = Date.now();
  progressBox.hidden = false;
  const tick = () => {
    const elapsed = (Date.now() - startedAt) / 1000;
    let passed = 0;
    let current = steps[steps.length - 1];
    for (const step of steps) {
      if (elapsed < passed + step.seconds) { current = step; break; }
      passed += step.seconds;
    }
    const total = steps.reduce((sum, step) => sum + step.seconds, 0);
    const percent = Math.min(95, Math.round((elapsed / total) * 100));
    setProgress(percent, current.label, `${Math.floor(elapsed)}초`);
  };
  tick();
  progressTimer = setInterval(tick, 500);
}

function setProgress(percent, label, time = '') {
  progressStep.textContent = label;
  progressTime.textContent = time;
  progressBar.style.width = `${percent}%`;
  progressTrack.setAttribute('aria-valuenow', String(percent));
}

function stopProgress(finalLabel = '') {
  clearInterval(progressTimer);
  progressTimer = null;
  if (finalLabel) setProgress(100, finalLabel);
  else progressBox.hidden = true;
}
const form = document.querySelector('#garment-form');
const saveStatus = document.querySelector('#save-status');
const listStatus = document.querySelector('#garment-list-status');
const garmentList = document.querySelector('#garment-list');
const garmentClosetView = createGarmentCloset({
  list: garmentList,
  status: listStatus,
  pagination: document.querySelector('#garment-list-pages')
});
const garmentTypeInput = document.querySelector('#garment-type');
const backgroundInput = document.querySelector('#background-id');
const schoolCode = (() => {
  try { return (localStorage.getItem('fitshare.schoolCode') || '나눔초등학교').slice(0, 30); }
  catch { return '나눔초등학교'; }
})();
let photoDataUrl = '';
let closetImageDataUrl = '';
let generatedName = '';
let analysisVersion = 0;
let manualMode = false;
const metadataFields = createGarmentMetadataFields(document.querySelector('#registration-metadata'));
const suggestMetadata = () => metadataFields.suggest({name:form.elements.name.value,garmentTypeId:Number(garmentTypeInput.value),category:categoryForId(Number(garmentTypeInput.value))});
form.elements.name.addEventListener('input', suggestMetadata);
for(const key of ['lengthCm','widthCm'])form.elements[key].addEventListener('input',()=>{manualMode=true;});
const camera = createRegistrationCamera({
  openButton: document.querySelector('#open-camera'),
  panel: document.querySelector('#camera-panel'),
  video: document.querySelector('#camera-video'),
  captureButton: document.querySelector('#capture-camera'),
  cancelButton: document.querySelector('#close-camera'),
  status: document.querySelector('#camera-status'),
  onPhoto: (file) => handlePhotoSelection({ files: [file] })
});
// HTTP로 접속한 휴대폰은 기존 기본 카메라 선택도 사용할 수 있습니다.
document.querySelector('#native-camera-action').hidden = globalThis.isSecureContext ||
  !/Android|iPhone|iPad/i.test(navigator.userAgent);

async function handlePhotoSelection(input) {
  const file = input.files?.[0];
  if (!file) return;
  analysisVersion++;
  manualMode=false;
  camera.close();
  if (photoPreview.src.startsWith('blob:')) URL.revokeObjectURL(photoPreview.src);
  form.hidden = true;
  form.reset();
  metadataFields.reset();
  generatedName = '';
  photoDataUrl = '';
  closetImageDataUrl = '';
  analyzeButton.disabled = true;
  manualButton.disabled = true;
  manualButton.hidden = true;
  analysisStatus.textContent = '';
  photoPreview.hidden = true;
  if (!file.type?.startsWith('image/')) {
    showStatus(analysisStatus, '사진 파일을 골라 주세요.', true);
    return;
  }
  try {
    photoDataUrl = await readRegistrationPhoto(file);
    photoPreview.src = URL.createObjectURL(file);
    photoPreview.hidden = false;
    analyzeButton.disabled = !garmentTypeInput.value || Number(garmentTypeInput.value) === 7;
    manualButton.hidden = false;
    manualButton.disabled = !garmentTypeInput.value;
    showStatus(analysisStatus, '사진을 골랐어요. 다음 버튼을 눌러 옷을 찾아봐요.');
  } catch (error) {
    showStatus(analysisStatus, error.message, true);
  }
}

// 태블릿·휴대폰 갤러리의 파일은 입력칸을 비우면 읽을 수 없게 되는 일이 있어요.
// 그래서 사진 내용을 먼저 메모리로 복사한 뒤에 입력칸을 비웁니다.
async function snapshotPhoto(file) {
  const buffer = await file.arrayBuffer();
  const guessedType = /\.png$/i.test(file.name || '') ? 'image/png' : /\.(jpe?g)$/i.test(file.name || '') ? 'image/jpeg' : '';
  return new File([buffer], file.name || 'photo.jpg', { type: file.type || guessedType, lastModified: file.lastModified });
}

photoInputs.forEach((input) => input.addEventListener('change', async () => {
  const selectedFile = input.files?.[0];
  if (!selectedFile) return;
  showStatus(analysisStatus, '사진을 불러오고 있어요.');
  let copy;
  try {
    copy = await snapshotPhoto(selectedFile);
  } catch {
    input.value = '';
    showStatus(analysisStatus, '사진을 읽지 못했어요. 구글 포토 같은 클라우드 사진이면 먼저 태블릿에 저장한 뒤 다시 골라 주세요.', true);
    return;
  }
  input.value = '';
  void handlePhotoSelection({ files: [copy] });
}));
document.querySelector('#choose-gallery-photo').addEventListener('click', () => galleryInput.click());

analyzeButton.addEventListener('click', runAnalysis);

backgroundInput.addEventListener('change', () => {
  analysisVersion++;
  analyzeButton.disabled = !photoDataUrl || !garmentTypeInput.value || Number(garmentTypeInput.value) === 7;
  if(manualMode)return;
  if (Number(garmentTypeInput.value) === 7) return;
  if (!form.hidden && photoDataUrl) runAnalysis();
  else if(photoDataUrl)showStatus(analysisStatus,'분석 조건이 바뀌었어요. 사진 등록하기를 다시 눌러 주세요.');
});

garmentTypeInput.addEventListener('change', () => {
  analysisVersion++;
  const garmentTypeId = Number(garmentTypeInput.value);
  analyzeButton.disabled = !photoDataUrl || !garmentTypeInput.value || garmentTypeId === 7;
  manualButton.disabled = analyzeButton.disabled;
  if (garmentTypeId === 7) {
    manualButton.hidden = false;
    manualButton.disabled = !photoDataUrl;
    if (!form.hidden) {
      manualMode = true;
      closetImageDataUrl = '';
      document.querySelector('#analysis-preview').hidden = true;
      document.querySelector('#review-note').textContent = '기타 옷은 자동 실측값을 사용하지 않아요. 실제 자로 길이와 너비를 다시 확인해 주세요.';
    }
    suggestMetadata();
    if (photoDataUrl) showStatus(analysisStatus, '기타 옷은 자동 실측 기준이 없어요. 실제 자로 길이와 너비를 재어 직접 등록해 주세요.');
    return;
  }
  if(manualMode){suggestMetadata();manualButton.click();return;}
  if (!form.hidden && photoDataUrl) runAnalysis();
  else if(photoDataUrl)showStatus(analysisStatus,'옷 종류가 바뀌었어요. 사진 등록하기를 다시 눌러 주세요.');
});

manualButton.addEventListener('click', () => {
  if(!photoDataUrl||!garmentTypeInput.value)return;
  analysisVersion++;
  manualMode=true;
  analyzeButton.disabled = Number(garmentTypeInput.value) === 7;
  if(form.hidden){
    form.elements.lengthCm.value='';form.elements.widthCm.value='';form.elements.colorName.value='';
    closetImageDataUrl='';
    if(!form.elements.name.value)form.elements.name.value=garmentTypeInput.selectedOptions[0].textContent.replace(/^\d+\.\s*/, '');
    suggestMetadata();
  }
  const pants=Number(garmentTypeInput.value)===5;
  document.querySelector('#width-label').textContent=pants?'허리 단면 너비(cm)':'가슴 단면 너비(cm)';
  document.querySelector('#analysis-preview').hidden=true;
  document.querySelector('#chosen-garment-type').textContent=`선택한 종류: ${garmentTypeInput.selectedOptions[0].textContent}`;
  document.querySelector('#review-note').textContent='자동 실측을 사용하지 않았어요. 실제 옷을 자로 재어 길이·단면 너비를 입력하고 색깔과 추천 정보를 확인해 주세요.';
  form.hidden=false;
  showStatus(analysisStatus,'직접 입력 모드예요. 확실하지 않은 추천 정보는 미등록으로 둘 수 있어요.');
  form.scrollIntoView({behavior:'smooth',block:'start'});
});

async function runAnalysis() {
  const version=++analysisVersion;
  manualMode=false;
  analyzeButton.disabled = true;
  form.hidden = true;
  showStatus(analysisStatus, '고른 배경의 표시와 옷 모양을 찾고 있어요. 잠시 기다려 주세요.');
  startProgress([
    { label: '1/4 사진을 서버로 보내고 있어요', seconds: 2 },
    { label: '2/4 매트 표시를 찾아 사진을 반듯하게 펴고 있어요', seconds: 3 },
    { label: '3/4 옷의 윤곽을 찾고 있어요', seconds: 5 },
    { label: '4/4 길이와 너비를 재고 있어요', seconds: 4 }
  ]);
  try {
    const result = await requestJson('/api/garments/analyze', {
      method: 'POST', body: JSON.stringify({ backgroundId: backgroundInput.value, garmentId: Number(garmentTypeInput.value), photoDataUrl })
    });
    if(version!==analysisVersion)return;
    stopProgress('분석이 끝났어요!');
    fillForm(result);
    form.hidden = false;
    showStatus(analysisStatus, result.message);
    form.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (error) {
    if(version===analysisVersion){ stopProgress(); showStatus(analysisStatus, error.message, true); }
  } finally {
    if(version===analysisVersion)analyzeButton.disabled = !photoDataUrl || !garmentTypeInput.value || Number(garmentTypeInput.value) === 7;
  }
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!photoDataUrl) return showStatus(saveStatus, '저장할 사진을 먼저 골라 주세요.', true);
  const values = new FormData(form);
  const garmentTypeId = Number(garmentTypeInput.value);
  const payload = {
    ...metadataFields.read(),
    measurementMethod: manualMode ? 'manual' : 'photo-analysis',
    name: values.get('name'), category: categoryForId(garmentTypeId), garmentTypeId, gender: values.get('gender'),
    schoolCode,
    lengthCm: Number(values.get('lengthCm')), widthCm: Number(values.get('widthCm')),
    colorName: values.get('colorName'), photoDataUrl, closetImageDataUrl, backgroundId: backgroundInput.value
  };
  const saveButton = form.querySelector('button[type="submit"]');
  saveButton.disabled = true;
  showStatus(saveStatus, '옷과 사진을 서버에 저장하고 있어요.');
  startProgress([{ label: '1/2 옷과 사진을 서버에 저장하고 있어요', seconds: 4 }]);
  try {
    const saved = await requestJson('/api/garments', { method: 'POST', body: JSON.stringify(payload) });
    startProgress([{ label: '2/2 AI 상품 사진을 만들고 있어요 (옷 분리 후 이미지 생성)', seconds: 40 }]);
    showStatus(analysisStatus, `${saved.name}을(를) 저장했어요. AI 상품 사진을 자동으로 만들고 있어요.`);
    await loadGarments(saved.id);
    form.reset();
    metadataFields.reset();
    generatedName = '';
    form.hidden = true;
    photoInputs.forEach((input) => { input.value = ''; });
    photoDataUrl = '';
    closetImageDataUrl = '';
    backgroundInput.value = 'M4';
    garmentTypeInput.value = '';
    photoPreview.hidden = true;
    analyzeButton.disabled = true;
    manualButton.disabled = true;
    manualButton.hidden = true;
    try {
      await requestJson(`/api/garments/${encodeURIComponent(saved.id)}/product-photo`, { method: 'POST', body: '{}' });
      stopProgress('등록과 AI 상품 사진 생성이 끝났어요!');
      showStatus(analysisStatus, `${saved.name}의 등록과 AI 상품 사진 생성이 끝났어요!`);
    } catch (error) {
      stopProgress();
      showStatus(analysisStatus, `옷과 실측값은 저장했지만 AI 상품 사진을 만들지 못했어요. ${error.message} 선생님에게 사진 생성을 요청해 주세요.`, true);
    }
    await loadGarments(saved.id);
  } catch (error) {
    stopProgress();
    showStatus(saveStatus, error.message, true);
  } finally {
    saveButton.disabled = false;
  }
});

function fillForm(result) {
  form.elements.lengthCm.value = result.lengthCm;
  form.elements.widthCm.value = result.widthCm;
  form.elements.colorName.value = result.colorName;
  if (!form.elements.name.value || form.elements.name.value === generatedName) {
    generatedName = `${result.colorName || ''} ${result.garmentName || '옷'}`.trim();
    form.elements.name.value = generatedName;
  }
  suggestMetadata();
  document.querySelector('#width-label').textContent = `${result.widthLabel}(cm)`;
  document.querySelector('#chosen-garment-type').textContent = `선택한 종류: ${result.garmentName}`;
  document.querySelector('#analysis-preview').src = result.analysisPhotoDataUrl;
  document.querySelector('#analysis-preview').hidden = false;
  closetImageDataUrl = result.closetImageDataUrl;
  document.querySelector('#review-note').textContent = '초록 선은 옷 테두리, 주황 선은 길이, 파란 선은 너비예요. 사진과 숫자가 맞는지 살펴보고 필요하면 고쳐 주세요.';
}

async function loadGarments(prioritizeId) {
  return garmentClosetView.reload(prioritizeId);
}

function categoryForId(garmentId) {
  if (garmentId === 3) return 'dress';
  if (garmentId === 5) return 'pants';
  if (garmentId === 6) return 'skirt';
  if (garmentId === 7) return 'other';
  return 'top';
}

async function requestJson(url, options = {}) {
  let response;
  try {
    response = await fetch(url, {
      ...options,
      headers: { 'Content-Type': 'application/json', ...options.headers }
    });
  } catch {
    throw new Error('저장 서버에 연결할 수 없어요. FitShare 서버가 켜져 있는지 확인해 주세요.');
  }
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.error || '요청을 마치지 못했어요. 사진을 확인하고 다시 해 주세요.');
  return result;
}

function showStatus(target, message, isError = false) {
  target.textContent = message;
  target.classList.toggle('is-error', isError);
}

// 옷을 올리려면 로그인해야 해요. 로그인하지 않았으면 등록 화면 대신 안내를 보여 줍니다.
fetchMe().then((user) => {
  const main = document.querySelector('#garment-registration');
  if (user) {
    document.querySelector('#owner-note').textContent = `올린 사람은 로그인한 ${user.name} 님으로 자동으로 적혀요.`;
    loadGarments();
    return;
  }
  main.hidden = true;
  const notice = document.createElement('section');
  notice.className = 'measurement-panel registration-panel';
  notice.innerHTML = '<h1>먼저 로그인해 주세요</h1><p class="measurement-intro">옷을 등록하려면 로그인이 필요해요.</p><a class="primary-action" href="../index.html">로그인하러 가기 →</a>';
  main.after(notice);
});
