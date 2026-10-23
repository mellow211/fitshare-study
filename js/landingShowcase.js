import { computeRanking, getMonthKey } from '../modules/rankingLogic.js';
import { pullState } from '../modules/rankingStore.js';
import { getRecommendationResults } from '../modules/recommendation.js';

const rankList = document.querySelector('#hero-rank-list');
const garmentList = document.querySelector('#hero-garment-list');
const avatarFaces = ['🌟', '😊', '🌱'];

function setMessage(target, message) {
  target.replaceChildren();
  const item = document.createElement('li');
  item.textContent = message;
  target.append(item);
  target.setAttribute('aria-busy', 'false');
}

async function renderRanking() {
  if (!rankList) return;
  const state = await pullState();
  if (!state) {
    setMessage(rankList, '나눔 랭킹을 불러오지 못했어요. 잠시 뒤 다시 확인해 주세요.');
    return;
  }
  const rows = computeRanking(state, getMonthKey(new Date())).slice(0, 3);
  if (!rows.length) {
    setMessage(rankList, '이번 달에 공개된 나눔 점수가 아직 없어요.');
    return;
  }
  rankList.replaceChildren(...rows.map((row, index) => {
    const item = document.createElement('li');
    if (index === 0) item.classList.add('is-first');
    const place = document.createElement('span');
    place.className = 'hero-rank-place';
    place.textContent = String(row.rank);
    const face = document.createElement('span');
    face.className = 'hero-rank-face';
    face.setAttribute('aria-hidden', 'true');
    face.textContent = avatarFaces[index] || '💛';
    const details = document.createElement('span');
    const name = document.createElement('strong');
    name.textContent = row.name;
    const activity = document.createElement('small');
    activity.textContent = `나눔 ${row.donations}회 · 받은 옷 ${row.receipts}회`;
    details.append(name, activity);
    const points = document.createElement('b');
    points.textContent = `${row.points}점`;
    item.append(place, face, details, points);
    return item;
  }));
  rankList.setAttribute('aria-busy', 'false');
}

function renderGarmentCard(result, index) {
  const { item, score } = result;
  const card = document.createElement('article');
  card.className = 'hero-garment-item';
  const art = document.createElement('span');
  art.className = `hero-garment-art ${['is-blue', 'is-lilac', 'is-peach'][index % 3]}`;
  const image = document.createElement('img');
  image.src = `/api/garments/${encodeURIComponent(item.id)}/photo`;
  image.alt = '';
  image.loading = 'lazy';
  image.addEventListener('error', () => {
    art.textContent = '👕';
    image.remove();
  }, { once: true });
  art.append(image);
  const copy = document.createElement('span');
  copy.className = 'hero-garment-copy';
  const name = document.createElement('strong');
  name.textContent = item.name || '이름 없는 옷';
  const details = document.createElement('small');
  const seasons = item.seasons?.length ? item.seasons.join(' · ') : '계절 미등록';
  details.textContent = `사이즈 ${item.size || '미등록'} · ${seasons}`;
  copy.append(name, details);
  const mark = document.createElement('span');
  mark.className = 'hero-pick-mark';
  mark.textContent = `${score}점 추천`;
  card.append(art, copy, mark);
  return card;
}

async function renderRecommendations() {
  if (!garmentList) return;
  try {
    const response = await fetch('/api/garments', { cache: 'no-store' });
    if (!response.ok) throw new Error('catalog unavailable');
    const records = await response.json();
    const filters = {
      gender: '전체', size: '130', sizeTolerance: '0', season: '봄', category: '전체',
      style: '전체', styleMode: '필수', color: '전체', colorName: '전체', colorMode: '필수',
      condition: '전체', garmentKind: '전체', sleeveLength: '전체', bottomLength: '전체',
      thickness: '전체', stretch: '전체', pattern: '전체', lengthMin: '', lengthMax: '',
      widthMin: '', widthMax: '', referenceLength: '', referenceWidth: '',
      fitPreference: '딱 맞게', harmony: '비슷하게 맞추기'
    };
    let results = getRecommendationResults(records, filters).slice(0, 3);
    if (!results.length) {
      results = getRecommendationResults(records, { ...filters, size: '전체', season: '전체' }).slice(0, 3);
    }
    if (!results.length) {
      garmentList.replaceChildren();
      const empty = document.createElement('p');
      empty.textContent = '추천 조건에 맞는 등록 옷이 아직 없어요.';
      garmentList.append(empty);
    } else {
      garmentList.replaceChildren(...results.map(renderGarmentCard));
    }
    garmentList.setAttribute('aria-busy', 'false');
  } catch {
    garmentList.replaceChildren();
    const error = document.createElement('p');
    error.textContent = '등록된 옷을 불러오지 못했어요. 잠시 뒤 다시 확인해 주세요.';
    garmentList.append(error);
    garmentList.setAttribute('aria-busy', 'false');
  }
}

export function initializeLandingShowcase() {
  void renderRanking();
  void renderRecommendations();
}
