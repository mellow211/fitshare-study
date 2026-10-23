import { loadSession, getExperiment } from './store.js?v=legacy-session-fallback-1';
import { makeOverlayMap, summarizeRecords } from './resultSummary.js?v=result-overlays-2';

const root = document.querySelector('#results-app');
const BACKGROUNDS = [
  { id: 'M1', name: '점 배경' },
  { id: 'M2', name: '격자 배경' },
  { id: 'M3', name: 'ChArUco 테두리' },
  { id: 'M4', name: 'ChArUco 교차점' },
];
const DATASET_GARMENT_NAMES = {
  'garment-1': '상의 외투',
  'garment-2': '하얀 크롭티셔츠',
  'garment-3': '원피스',
  'garment-4': '점퍼',
  'garment-5': '바지',
};
const selectedRun = new URLSearchParams(location.search).get('run');
let session = null;
let garmentsById = new Map();
let datasetImportedRound = false;
let overlayByImageName = new Map();

const sessionPromise = selectedRun === 'local'
  ? loadSession()
  : selectedRun
    ? getExperiment(selectedRun).catch(async (error) => {
      const saved = await loadSession().catch(() => null);
      if (saved?.serverId === selectedRun) return saved;
      throw error;
    })
    : loadSession().then((saved) => saved?.serverId
      ? getExperiment(saved.serverId).catch(() => saved)
      : saved);

sessionPromise.then(async (saved) => {
  session = saved;
  if (!session?.records?.length) return renderEmpty();
  garmentsById = new Map(session.garments.map((garment) => [garment.id, garment]));
  datasetImportedRound = session.records.length === 20
    && session.garments.length === 5
    && session.records.every((record) => Boolean(record.imageName));
  if (datasetImportedRound) {
    try {
      const response = await fetch('/api/measure-dataset');
      if (response.ok) overlayByImageName = makeOverlayMap(await response.json());
    } catch {
      // 분석 그림을 못 불러와도 저장된 원본 사진과 측정값은 계속 보여줘요.
    }
  }
  renderResults();
}).catch((error) => renderEmpty(error.message));

function renderEmpty(message = '') {
  root.innerHTML = `
    <section class="measurement-panel">
      <p class="eyebrow">실험 결과</p>
      <h1>아직 사진 결과가 없어요</h1>
      <p class="kid-help">실험에서 사진을 찍고 저장하면 배경별 비교 결과를 볼 수 있어요.</p>
      ${message ? `<p class="error-message">${escapeHtml(message)}</p>` : ''}
      <div class="button-row">
        <a class="primary-action" href="../measure/index.html">실험하러 가기</a>
        <a class="secondary-action" href="sessions.html">저장된 회차 보기</a>
      </div>
    </section>`;
}

function normalizedRecords() {
  return session.records.map((record) => {
    const garment = garmentsById.get(record.garmentId);
    const measuredWidth = Number(record.measuredWidthCm);
    const measuredLength = Number(record.measuredLengthCm);
    const referenceWidth = Number(garment?.referenceWidthCm);
    const referenceLength = Number(garment?.referenceLengthCm);
    const widthError = Number.isFinite(record.errorWidthCm)
      ? Number(record.errorWidthCm) : measuredWidth - referenceWidth;
    const lengthError = Number.isFinite(record.errorLengthCm)
      ? Number(record.errorLengthCm) : measuredLength - referenceLength;

    return {
      ...record,
      status: record.status || (Number.isFinite(measuredWidth) && Number.isFinite(measuredLength) ? 'done' : 'pending'),
      measuredWidthCm: measuredWidth,
      measuredLengthCm: measuredLength,
      errorWidthCm: widthError,
      errorLengthCm: lengthError,
    };
  });
}

function renderResults() {
  const records = normalizedRecords();
  const matSummary = summarizeRecords(records, 'matId', BACKGROUNDS.map((item) => item.id));
  const garmentIds = session.garments.map((garment) => garment.id);
  const garmentSummary = summarizeRecords(records, 'garmentId', garmentIds);
  const completeCount = records.filter((record) => record.status === 'done').length;
  const widthMae = overallMae(records, 'errorWidthCm');
  const lengthMae = overallMae(records, 'errorLengthCm');

  root.innerHTML = `
    <section class="measurement-panel dataset-panel">
      <p class="eyebrow">FITSHARE 실험 결과</p>
      <h1>${session.round ? `${session.round}회차 결과` : '사진과 측정값 비교'}</h1>
      <p class="measurement-intro">배경마다 같은 옷의 사진과 측정 결과를 모아봤어요. 오차가 작을수록 기준 크기와 가까워요.</p>
      ${datasetImportedRound ? '<p class="kid-help">이 회차는 실측 데이터셋 비교와 같은 20개 사진에서 나온 결과예요.</p>' : ''}
      <div class="button-row">
        <button class="primary-action" id="download-csv" type="button">표 파일(CSV) 저장</button>
        <a class="secondary-action" href="sessions.html">다른 회차 보기</a>
        <a class="secondary-action" href="../measure-dataset.html">배경 비교 보고서</a>
      </div>

      <section class="dataset-section" aria-labelledby="result-summary-heading">
        <h2 id="result-summary-heading">전체 결과</h2>
        <div class="dataset-summary">
          ${statCard('사진 결과', `${completeCount}/${records.length}장`)}
          ${statCard('너비 평균 오차', displayCm(widthMae))}
          ${statCard('기장 평균 오차', displayCm(lengthMae))}
          ${statCard('비교한 옷', `${session.garments.length}벌`)}
        </div>
      </section>

      <section class="dataset-section" aria-labelledby="background-summary-heading">
        <h2 id="background-summary-heading">배경별 결과</h2>
        <p>너비와 기장을 따로 비교해요. 두 숫자가 모두 작을수록 기준 크기와 가까워요.</p>
        ${backgroundChart(matSummary)}
        <div class="dataset-table-wrap">${summaryTable(matSummary, (id) => backgroundName(id), session.garments.length)}</div>
      </section>

      <section class="dataset-section" aria-labelledby="garment-summary-heading">
        <h2 id="garment-summary-heading">옷 종류별 결과</h2>
        <p>옷마다 어떤 배경에서 측정값이 기준에 가까웠는지 볼 수 있어요.</p>
        <div class="dataset-table-wrap">${summaryTable(garmentSummary, (id) => garmentName(id), 4)}</div>
      </section>

      <section class="dataset-section" aria-labelledby="result-photos-heading">
        <h2 id="result-photos-heading">사진과 측정값 자세히 보기</h2>
        <div class="dataset-filters">
          <label>배경
            <select id="filter-background"><option value="all">모든 배경</option>${BACKGROUNDS.map((item) => `<option value="${item.id}">${item.name}</option>`).join('')}</select>
          </label>
          <label>옷
            <select id="filter-garment"><option value="all">모든 옷</option>${session.garments.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(garmentName(item.id))}</option>`).join('')}</select>
          </label>
        </div>
        <div id="result-photos" class="dataset-photo-list result-photo-list"></div>
      </section>
      <p class="dataset-footnote">너비 오차와 기장 오차는 각각 줄자 기준값과 사진 측정값의 차이예요. 옷의 주름이나 사진을 놓은 방법도 결과에 영향을 줄 수 있어요.</p>
    </section>`;

  root.querySelector('#download-csv').addEventListener('click', downloadCsv);
  root.querySelector('#filter-background').addEventListener('change', renderPhotoRows);
  root.querySelector('#filter-garment').addEventListener('change', renderPhotoRows);
  renderPhotoRows();
}

function summaryTable(summaries, labelFor, expectedCount) {
  const rows = summaries.map((item) => `
    <tr>
      <th scope="row">${escapeHtml(labelFor(item.key))}</th>
      <td>${item.count}/${Number.isFinite(expectedCount) ? expectedCount : '—'}장</td>
      <td>${displayCm(item.widthMaeCm)}</td>
      <td>${displayCm(item.lengthMaeCm)}</td>
      <td>${displayCm(item.overallMaeCm)}</td>
    </tr>`).join('');
  return `<table class="dataset-table"><thead><tr><th scope="col">구분</th><th scope="col">완료</th><th scope="col">너비 평균 오차</th><th scope="col">기장 평균 오차</th><th scope="col">두 값 평균</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function backgroundChart(summaries) {
  const available = summaries.filter((item) => item.count && item.widthMaeCm !== null && item.lengthMaeCm !== null);
  if (!available.length) return '<p class="analysis-note">비교할 배경 결과가 아직 없어요.</p>';
  const maxValue = Math.max(1, ...available.flatMap((item) => [item.widthMaeCm, item.lengthMaeCm]));
  const best = [...available].sort((a, b) => a.overallMaeCm - b.overallMaeCm)[0];
  const ordered = [...available].sort((a, b) => a.overallMaeCm - b.overallMaeCm);

  return `<div class="background-chart" role="img" aria-label="배경마다 너비와 기장 오차를 비교한 막대 그래프. 막대가 짧을수록 기준 크기에 가까워요.">
    <div class="background-chart-legend"><span><i class="width-key"></i>너비 오차</span><span><i class="length-key"></i>기장 오차</span><small>막대가 짧을수록 오차가 작아요</small></div>
    ${ordered.map((item) => `<article class="background-chart-row ${item.key === best.key ? 'is-best' : ''}">
      <div class="background-chart-name"><strong>${escapeHtml(backgroundName(item.key))}</strong>${item.key === best.key ? '<span>두 값 평균이 가장 낮아요</span>' : ''}</div>
      <div class="background-chart-bars">
        <div class="background-chart-bar"><span>너비</span><div><i class="width-bar" style="width:${item.widthMaeCm / maxValue * 100}%"></i></div><b>${displayCm(item.widthMaeCm)}</b></div>
        <div class="background-chart-bar"><span>기장</span><div><i class="length-bar" style="width:${item.lengthMaeCm / maxValue * 100}%"></i></div><b>${displayCm(item.lengthMaeCm)}</b></div>
      </div>
      <small class="background-chart-overall">너비·기장 평균 오차 ${displayCm(item.overallMaeCm)}</small>
    </article>`).join('')}
  </div>`;
}

function renderPhotoRows() {
  if (!session) return;
  const records = normalizedRecords();
  const backgroundFilter = root.querySelector('#filter-background').value;
  const garmentFilter = root.querySelector('#filter-garment').value;
  const backgroundOrder = new Map(BACKGROUNDS.map((item, index) => [item.id, index]));
  const garmentOrder = new Map(session.garments.map((item, index) => [item.id, index]));
  const filtered = records
    .filter((record) => backgroundFilter === 'all' || record.matId === backgroundFilter)
    .filter((record) => garmentFilter === 'all' || record.garmentId === garmentFilter)
    .sort((a, b) => (garmentOrder.get(a.garmentId) - garmentOrder.get(b.garmentId))
      || (backgroundOrder.get(a.matId) - backgroundOrder.get(b.matId)));

  root.querySelector('#result-photos').innerHTML = filtered.map(renderPhotoCard).join('')
    || '<p class="analysis-note">이 조건에 맞는 사진이 없어요.</p>';
}

function renderPhotoCard(record) {
  const garment = garmentsById.get(record.garmentId);
  const name = garmentName(record.garmentId);
  const widthName = garment?.category === 'pants' || garment?.category === 'skirt' ? '허리 너비' : '가슴 너비';
  const originalImage = record.photoUrl
    ? `<img src="${escapeHtml(record.photoUrl)}" alt="${escapeHtml(name)} ${escapeHtml(backgroundName(record.matId))} 원본 사진" loading="lazy" />`
    : '<div class="dataset-no-photo">저장된 사진이 없어요</div>';
  const overlay = overlayByImageName.get(record.imageName);
  const analyzedImage = overlay
    ? `<figure><img src="${escapeHtml(overlay)}" alt="${escapeHtml(name)}의 윤곽과 너비·기장 측정선" loading="lazy" /><figcaption>옷 영역과 측정선</figcaption></figure>`
    : '';

  return `<article class="dataset-photo-card">
    <div class="dataset-photo-heading"><h3>${escapeHtml(backgroundName(record.matId))} · ${escapeHtml(name)}</h3><span class="${record.status === 'done' ? '' : 'needs-review'}">${record.status === 'done' ? '분석 완료' : '측정 대기'}</span></div>
    <div class="result-photo-layout"><div class="result-picture-pair ${overlay ? '' : 'single'}"><figure>${originalImage}<figcaption>원본 사진</figcaption></figure>${analyzedImage}</div>
      <div class="dataset-values">
        <p><strong>${widthName}</strong><br />기준 ${displayCm(garment?.referenceWidthCm)} <span>→</span> 측정 ${displayCm(record.measuredWidthCm)}</p>
        <p><strong>기장</strong><br />기준 ${displayCm(garment?.referenceLengthCm)} <span>→</span> 측정 ${displayCm(record.measuredLengthCm)}</p>
        ${Number.isFinite(Number(garment?.referenceCollarToHemCm)) ? `<p><strong>목점에서 밑단</strong><br />기준 ${displayCm(Number(garment.referenceCollarToHemCm))}</p>` : ''}
      </div>
    </div>
    <p class="dataset-error">너비 오차 ${signed(record.errorWidthCm)}cm · 기장 오차 ${signed(record.errorLengthCm)}cm</p>
  </article>`;
}

function statCard(label, value) {
  return `<article class="dataset-stat"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></article>`;
}

function overallMae(records, errorKey) {
  const values = records.filter((record) => record.status === 'done')
    .map((record) => Math.abs(record[errorKey])).filter(Number.isFinite);
  if (!values.length) return null;
  return Math.round((values.reduce((sum, value) => sum + value, 0) / values.length + Number.EPSILON) * 10) / 10;
}

function backgroundName(id) { return BACKGROUNDS.find((item) => item.id === id)?.name || id; }
function garmentName(id) { return datasetImportedRound ? DATASET_GARMENT_NAMES[id] : garmentsById.get(id)?.name || id; }
function displayCm(value) { return Number.isFinite(value) ? `${value.toFixed(1)}cm` : '결과 없음'; }
function signed(value) { return Number.isFinite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(1)}` : '—'; }
function escapeHtml(value) { return String(value ?? '').replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]); }

function downloadCsv() {
  const rows = [['옷 이름', '배경', '사진 파일', '너비 기준(cm)', '너비 측정(cm)', '너비 오차(cm)', '기장 기준(cm)', '기장 측정(cm)', '기장 오차(cm)', '목점-밑단 기준(cm)']];
  for (const record of normalizedRecords()) {
    const garment = garmentsById.get(record.garmentId);
    rows.push([
      garmentName(record.garmentId), backgroundName(record.matId), record.imageName || '',
      garment?.referenceWidthCm, record.measuredWidthCm, record.errorWidthCm,
      garment?.referenceLengthCm, record.measuredLengthCm, record.errorLengthCm,
      garment?.referenceCollarToHemCm,
    ]);
  }
  const csv = `\uFEFF${rows.map((row) => row.map((cell) => `"${String(cell ?? '').replaceAll('"', '""')}"`).join(',')).join('\r\n')}`;
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  link.download = `fitshare-${session.round || 'result'}-result.csv`;
  link.click();
  URL.revokeObjectURL(link.href);
}
