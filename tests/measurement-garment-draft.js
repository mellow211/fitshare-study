import { createGarmentInputValues, readGarmentInputDraft, isGarmentInputDraft, isResumableSession } from '../js/measurement/garmentDraft.js';
import { renderGarmentInputGuide } from '../js/measurement/garmentGuide.js';

const results = [];
function check(name, action) {
  try { action(); results.push({ name, passed: true }); }
  catch (error) { results.push({ name, passed: false, error: error.message }); }
}

check('정상: 저장한 두 벌의 이름·종류·기준값을 다시 폼에 채워요', () => {
  const draft = { id: 'current', stage: 'garment-input', garments: [
    { name: '체육복', category: 'pants', referenceLengthCm: '72', referenceWidthCm: '33' },
    { name: '겉옷', category: 'top', referenceLengthCm: '55', referenceWidthCm: '44', referenceCollarToHemCm: '49' }
  ] };
  const values = createGarmentInputValues(2, draft.garments);
  if (values[0].name !== '체육복' || values[0].category !== 'pants' || values[0].length !== '72' || values[1].width !== '44' || values[1].collarLength !== '49') {
    throw new Error('저장된 옷 입력값이 이어지지 않았어요.');
  }
});

check('경계: 숫자를 다 입력하지 않은 중간 상태도 그대로 저장해요', () => {
  const form = { get: (name) => ({ 'name-0': '원피스', 'category-0': 'dress', 'length-0': '8', 'width-0': '', 'collarLength-0': '7' })[name] ?? null };
  const draft = readGarmentInputDraft(form, 1);
  if (draft.stage !== 'garment-input' || draft.garments[0].length !== '8' || draft.garments[0].width !== '' || draft.garments[0].collarLength !== '7') {
    throw new Error('입력 중인 값을 그대로 임시 저장하지 않았어요.');
  }
});

check('오류: 잘못된 옷 개수는 입력 폼으로 만들지 않아요', () => {
  try { createGarmentInputValues(0); }
  catch (error) { if (error instanceof RangeError) return; throw error; }
  throw new Error('0벌 입력을 거부하지 않았어요.');
});

check('정상: 입력 초안과 촬영 중 회차를 구분해요', () => {
  if (!isGarmentInputDraft({ stage: 'garment-input', garments: [{ name: '상의' }] })) throw new Error('옷 입력 초안을 찾지 못했어요.');
  if (isGarmentInputDraft({ serverId: 'run-1', garments: [] })) throw new Error('촬영 중인 회차를 옷 입력 초안으로 잘못 판단했어요.');
});

check('정상: 기기에 저장된 지난 미완료 회차를 이어하기 대상으로 찾아요', () => {
  if (!isResumableSession({ id: 'current', serverId: 'run-12', round: 12, matIndex: 2 })) throw new Error('저장된 지난 회차를 이어하기 대상으로 찾지 못했어요.');
  if (isResumableSession(null)) throw new Error('저장된 내용이 없는데 이어하기로 판단했어요.');
});

check('정상: 폼마다 선택한 옷 종류의 그림과 측정 안내를 만들어요', () => {
  const pants = renderGarmentInputGuide('pants', 0);
  const dress = renderGarmentInputGuide('dress', 1);
  if (!pants.includes('바지 옆길이') || !dress.includes('원피스 길이')) throw new Error('옷 종류에 맞는 안내 그림이 없어요.');
  if (!pants.includes('length-arrow-form-0') || !dress.includes('length-arrow-form-1')) throw new Error('각 폼 그림의 화살표 ID가 서로 구분되지 않아요.');
});

check('정상: 옷 입력을 이어 할 때 선택한 배경 순서도 보존해요', () => {
  const order = ['M2', 'M1', 'M4', 'M3'];
  const result = readGarmentInputDraft({ get: (name) => name.startsWith('name-') ? '상의' : name.startsWith('length-') || name.startsWith('width-') ? '40' : 'top' }, 1, order);
  if (result.matOrder.join(',') !== order.join(',')) throw new Error('옷 입력 초안을 다시 열 때 배경 순서가 사라졌어요.');
});

const list = document.querySelector('#results');
results.forEach(({ name, passed, error }) => {
  const item = document.createElement('li'); item.textContent = `${passed ? '통과' : '실패'} · ${name}${error ? ` · ${error}` : ''}`;
  item.style.color = passed ? 'green' : 'red'; list.append(item);
});
