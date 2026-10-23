const DATABASE_NAME = 'fitshare-measurement-local';
const DATABASE_VERSION = 1;
const STORE_NAME = 'sessions';

function openDatabase() {
  if (!('indexedDB' in window)) return Promise.reject(new Error('이 브라우저는 사진 저장 기능을 지원하지 않아요. 다른 최신 브라우저를 사용해 주세요.'));
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE_NAME, { keyPath: 'id' });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(new Error('기기에 저장 공간을 열지 못했어요. 브라우저 저장 공간을 확인해 주세요.'));
  });
}

export async function saveSession(session) {
  const db = await openDatabase();
  // 사진 원본은 서버에 보냈으니 브라우저에는 이어하기 정보만 남깁니다.
  const snapshot = {
    ...session,
    records: (session.records || []).map(({ photo, rawPhoto, fullPhoto, ...record }) => record)
  };
  return new Promise((resolve, reject) => {
    const request = db.transaction(STORE_NAME, 'readwrite').objectStore(STORE_NAME).put(snapshot);
    request.onsuccess = () => { db.close(); resolve(snapshot); };
    request.onerror = () => { db.close(); reject(new Error('저장하지 못했어요. 사진 크기를 줄이거나 다시 시도해 주세요.')); };
  });
}

export async function loadSession() {
  const db = await openDatabase();
  return new Promise((resolve, reject) => {
    const request = db.transaction(STORE_NAME, 'readonly').objectStore(STORE_NAME).get('current');
    request.onsuccess = () => { db.close(); resolve(request.result || null); };
    request.onerror = () => { db.close(); reject(new Error('저장된 실험을 읽지 못했어요.')); };
  });
}

export async function clearSession() {
  const db = await openDatabase();
  return new Promise((resolve, reject) => {
    const request = db.transaction(STORE_NAME, 'readwrite').objectStore(STORE_NAME).delete('current');
    request.onsuccess = () => { db.close(); resolve(); };
    request.onerror = () => { db.close(); reject(new Error('실험 기록을 지우지 못했어요.')); };
  });
}

// 아래 함수들은 Python 서버에 실험 회차와 사진을 보냅니다.
export async function createExperiment(garments) {
  return requestJson('/api/experiments', { method: 'POST', body: JSON.stringify({ garments }) });
}

export async function saveExperimentRecord(runId, record) {
  const photoDataUrl = await blobToDataUrl(record.photo);
  const { photo, rawPhoto, fullPhoto, ...recordDetails } = record;
  return requestJson(`/api/experiments/${encodeURIComponent(runId)}/records`, {
    method: 'POST', body: JSON.stringify({ record: recordDetails, photoDataUrl })
  });
}

export async function completeExperiment(runId) {
  return requestJson(`/api/experiments/${encodeURIComponent(runId)}/complete`, { method: 'POST', body: '{}' });
}

export async function listExperiments() {
  const experiments = await requestJson('/api/experiments');
  let localSession = null;
  try { localSession = await loadSession(); } catch { /* Older or restricted browsers may not provide IndexedDB. */ }
  if (!localSession?.records?.length || experiments.some((item) => item.id === localSession.serverId)) return experiments;

  const localExperiment = {
    id: 'local',
    round: localSession.round || 1,
    createdAt: localSession.createdAt || localSession.updatedAt,
    completed: Number(localSession.matIndex) >= 4,
    garmentCount: localSession.garments?.length || 0,
    recordCount: localSession.records.length,
    localOnly: true
  };
  return [...experiments, localExperiment].sort((a, b) => Number(b.round || 0) - Number(a.round || 0));
}

export async function getExperiment(runId) {
  return requestJson(`/api/experiments/${encodeURIComponent(runId)}`);
}

async function requestJson(url, options = {}) {
  let response;
  try {
    response = await fetch(url, {
      ...options,
      headers: { 'Content-Type': 'application/json', ...options.headers }
    });
  } catch {
    throw new Error('FitShare 저장 서버에 연결할 수 없어요. PC 서버가 켜져 있는지 확인해 주세요.');
  }
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.error || '서버에 저장하지 못했어요. 잠시 뒤 다시 시도해 주세요.');
  return result;
}

function blobToDataUrl(blob) {
  if (!(blob instanceof Blob)) return Promise.reject(new Error('저장할 사진을 찾지 못했어요. 다시 촬영해 주세요.'));
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error('사진을 서버로 보낼 준비를 하지 못했어요.'));
    reader.readAsDataURL(blob);
  });
}
