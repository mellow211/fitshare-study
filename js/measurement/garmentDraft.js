const categories = new Set(['top', 'bottom', 'dress', 'pants', 'skirt', 'other']);

// 저장한 값을 입력 상자에 다시 넣을 때 빠진 값은 안전한 기본값으로 채웁니다.
export function createGarmentInputValues(count, savedGarments = []) {
  if (!Number.isInteger(count) || count < 1 || count > 30) {
    throw new RangeError('옷은 1벌부터 30벌까지 입력할 수 있어요.');
  }
  return Array.from({ length: count }, (_, index) => {
    const saved = savedGarments[index] || {};
    return {
      name: String(saved.name ?? `옷 ${index + 1}`),
      category: categories.has(saved.category) ? saved.category : 'top',
      length: String(saved.length ?? saved.referenceLengthCm ?? ''),
      width: String(saved.width ?? saved.referenceWidthCm ?? ''),
      collarLength: String(saved.collarLength ?? saved.referenceCollarToHemCm ?? '')
    };
  });
}

// 폼에 적는 도중에도 입력 중인 글자 그대로 임시 저장합니다.
export function readGarmentInputDraft(form, count, matOrder = ['M1', 'M2', 'M3', 'M4']) {
  if (!Number.isInteger(count) || count < 1 || count > 30) {
    throw new RangeError('옷은 1벌부터 30벌까지 입력할 수 있어요.');
  }
  const garments = Array.from({ length: count }, (_, index) => {
    const category = String(form.get(`category-${index}`) || 'top');
    return {
      name: String(form.get(`name-${index}`) ?? ''),
      category: categories.has(category) ? category : 'top',
      length: String(form.get(`length-${index}`) ?? ''),
      width: String(form.get(`width-${index}`) ?? ''),
      collarLength: String(form.get(`collarLength-${index}`) ?? '')
    };
  });
  const defaultOrder = ['M1', 'M2', 'M3', 'M4'];
  const hasValidOrder = Array.isArray(matOrder) && matOrder.length === defaultOrder.length
    && new Set(matOrder).size === defaultOrder.length
    && defaultOrder.every((id) => matOrder.includes(id));
  return { id: 'current', stage: 'garment-input', garments, matOrder: hasValidOrder ? [...matOrder] : defaultOrder, updatedAt: new Date().toISOString() };
}

export function isGarmentInputDraft(session) {
  return session?.stage === 'garment-input' && Array.isArray(session.garments);
}

// 옷 입력 초안이나 서버에 연결된 회차가 있으면 다시 이어갈 수 있습니다.
export function isResumableSession(session) {
  return isGarmentInputDraft(session) || Boolean(session?.serverId);
}
