import { fetchMe } from '../js/auth.js?v=auth-3';
import { openGarmentDetail } from '../modules/garmentDetail.js';
import { createGarmentCloset } from '../js/measurement/garmentCloset.js?v=closet-tools-6';

const originalFetch = window.fetch;
const originalConfirm = window.confirm;
const keys = ['fitshare.ranking.viewerName', 'fitshare.ranking.rev'];
const previousStorage = keys.map((key) => localStorage.getItem(key));
const checks = [];
let role = 'teacher';
let confirmed = true;
let confirmations = 0;
let requests = [];
let deleteRequests = [];
let generate;
let updates = 0;
const photoUrl = '/api/garments/test-product/product-photo';
const garment = { id: 'test-product', name: 'Test shirt', garmentTypeId: 2, garmentTypeName: 'Shirt',
  category: 'top', gender: 'unisex', colorName: 'White', lengthCm: 45, widthCm: 32,
  ownerName: 'Student', ownerUsername: 'student', productPhotoUrl: photoUrl };
const registrationHtml = await originalFetch('/measure/register.html').then((response) => response.text());
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
async function waitFor(condition, message) {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    if (condition()) return;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error(message);
}
const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' }
});
function assert(value, message) { if (!value) throw new Error(message); }
function button(text) {
  return [...document.querySelectorAll('dialog.garment-detail button')].find((node) => node.textContent === text);
}
async function check(name, action) {
  try { await action(); checks.push({ name, passed: true }); }
  catch (error) { checks.push({ name, passed: false, error: error.message }); }
  document.querySelectorAll('dialog.garment-detail').forEach((dialog) => { dialog.close(); dialog.remove(); });
}

window.confirm = (text) => {
  confirmations += 1;
  if (text.includes('되돌릴 수 없어요')) assert(text.includes(garment.name), 'Deletion must name the garment');
  else assert(text.includes('비용'), 'Confirmation must explain generation cost');
  return confirmed;
};
window.fetch = async (url, options) => {
  if (url === '/api/auth/me') return json({ user: { name: 'Photo tester', username: 'photo-tester', role } });
  if (url === '/api/ranking') return json({ rev: 0, state: null });
  if (url === photoUrl && options?.method === 'POST') {
    requests.push(JSON.parse(options.body));
    return generate();
  }
  if (url === `/api/garments/${encodeURIComponent(garment.id)}` && options?.method === 'DELETE') {
    deleteRequests.push(options.method);
    return json({ ok: true });
  }
  throw new Error(`Unexpected request: ${url}`);
};

try {
  await fetchMe();
  await check('Teacher sees viewing and regeneration as separate actions', async () => {
    openGarmentDetail({ ...garment });
    assert(button('AI 상품 사진 보기'), 'Existing image must remain viewable');
    assert(button('AI 사진 다시 만들기'), 'Teacher regeneration button is missing');
  });
  await check('Canceling the cost confirmation makes no generation request', async () => {
    confirmed = false;
    requests = [];
    openGarmentDetail({ ...garment });
    const regenerate = button('AI 사진 다시 만들기');
    assert(regenerate, 'Regeneration button is missing');
    regenerate.click();
    await settle();
    assert(confirmations > 0, 'Cost confirmation was skipped');
    assert(requests.length === 0, 'Canceling must not generate a photo');
    assert(!regenerate.disabled, 'Canceling must keep the control usable');
  });
  await check('Successful regeneration blocks double clicks and refreshes photo and closet', async () => {
    confirmed = true;
    requests = [];
    updates = 0;
    let finish;
    generate = () => new Promise((resolve) => { finish = resolve; });
    const item = { ...garment };
    openGarmentDetail(item, () => { updates += 1; });
    const regenerate = button('AI 사진 다시 만들기');
    assert(regenerate, 'Regeneration button is missing');
    regenerate.click();
    assert(regenerate.disabled && button('AI 상품 사진 보기').disabled, 'Controls must be disabled while generating');
    regenerate.click();
    assert(requests.length === 1 && requests[0].regenerate === true, 'One explicit regeneration request is required');
    finish(json({ productPhotoUrl: photoUrl, generatedAt: '2026-10-17T06:00:00+00:00' }, 201));
    await waitFor(() => !regenerate.disabled, 'Regeneration did not finish');
    assert(!regenerate.disabled && updates === 1, 'Success must refresh closet and re-enable controls');
    const image = document.querySelector('.garment-detail-photo');
    assert(image.src.includes('v=2026-10-17'), 'New photo must bypass previous image cache');
    assert(item.productPhotoGeneratedAt === '2026-10-17T06:00:00+00:00', 'New photo version must be remembered');
  });
  await check('Provider failure keeps existing displayed image and allows retry', async () => {
    requests = [];
    updates = 0;
    generate = async () => json({ error: 'Generation failed; previous image kept' }, 502);
    openGarmentDetail({ ...garment }, () => { updates += 1; });
    button('AI 상품 사진 보기').click();
    const image = document.querySelector('.garment-detail-photo');
    const oldSource = image.src;
    const regenerate = button('AI 사진 다시 만들기');
    assert(regenerate, 'Regeneration button is missing');
    regenerate.click();
    await waitFor(() => !regenerate.disabled, 'Failed regeneration did not finish');
    assert(image.src === oldSource && updates === 0, 'Failure must preserve previous image and closet');
    assert(!regenerate.disabled, 'Failure must allow retry');
    assert(document.querySelector('.garment-detail-message.is-error').textContent.includes('Generation failed'),
      'Failure must be explained');
  });
  await check('First generation reveals regeneration only after saving a photo', async () => {
    requests = [];
    generate = async () => json({ productPhotoUrl: photoUrl, generatedAt: 'new-photo' }, 201);
    openGarmentDetail({ ...garment, productPhotoUrl: null });
    assert(!button('AI 사진 다시 만들기') || button('AI 사진 다시 만들기').hidden,
      'No regeneration action is needed before first photo');
    button('AI 상품 사진 만들기').click();
    await waitFor(() => Boolean(button('AI 상품 사진 보기')), 'First generation did not finish');
    assert(requests.length === 1 && !requests[0].regenerate, 'First generation must keep its existing request contract');
    assert(button('AI 사진 다시 만들기') && !button('AI 사진 다시 만들기').hidden,
      'Saved photo must become regeneratable');
  });
  await check('Students do not receive generation or regeneration controls', async () => {
    role = 'student';
    await fetchMe();
    openGarmentDetail({ ...garment });
    assert(!button('AI 사진 다시 만들기') && !button('AI 상품 사진 만들기') && !button('AI 상품 사진 보기'),
      'Teacher controls must not be shown to students');
    assert(!button('옷 삭제'), 'Students must not receive a delete control');
  });
  await check('Teacher delete requires confirmation and refreshes the closet after success', async () => {
    role = 'teacher';
    await fetchMe();
    confirmed = false;
    deleteRequests = [];
    updates = 0;
    openGarmentDetail({ ...garment }, () => { updates += 1; });
    const remove = button('옷 삭제');
    assert(remove, 'Teacher delete control is missing');
    remove.click();
    await settle();
    assert(deleteRequests.length === 0 && !remove.disabled, 'Canceling must not send a delete request');
    confirmed = true;
    remove.click();
    await waitFor(() => deleteRequests.length === 1 && updates === 1, 'Confirmed deletion did not finish and refresh the closet');
    assert(deleteRequests[0] === 'DELETE' && updates === 1, 'Success must delete and refresh the closet');
    await waitFor(() => !document.querySelector('dialog.garment-detail[open]'), 'Successful deletion must close the detail dialog');
  });
  await check('Students edit only their own cards and teachers edit all cards', async () => {
    role = 'student';
    await fetchMe();
    const root = document.createElement('section');
    const list = document.createElement('div');
    const status = document.createElement('p');
    const pagination = document.createElement('nav');
    root.append(status, list, pagination);
    document.body.append(root);
    const cards = [
      { id: 'mine', name: 'My shirt', ownerName: 'Photo tester', ownerUsername: 'photo-tester' },
      { id: 'other', name: 'Other shirt', ownerName: 'Another', ownerUsername: 'another' }
    ];
    const previousFetch = window.fetch;
    window.fetch = (url, options) => url === '/api/garments' ? Promise.resolve(json(cards)) : previousFetch(url, options);
    try {
      const closet = createGarmentCloset({ list, status, pagination });
      await closet.reload();
      assert(list.children[0].querySelector('.closet-edit-action'), 'Own clothes must be editable');
      assert(!list.children[1].querySelector('.closet-edit-action'), 'Students must not edit others clothes');
      role = 'teacher';
      await fetchMe();
      await closet.reload();
      assert(list.querySelectorAll('.closet-edit-action').length === 2, 'Teachers must edit all clothes');
    } finally { root.remove(); window.fetch = previousFetch; }
  });
  for (const fails of [false, true]) {
    await check(`Registration automatically generates AI after saving${fails ? ' and preserves registration on AI failure' : ''}`, async () => {
      const frame = document.createElement('iframe');
      frame.title = 'Registration flow under test';
      frame.srcdoc = registrationHtml.replace('<head>', '<head><base href="/measure/">')
        .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '');
      const loaded = new Promise((resolve) => frame.addEventListener('load', resolve, { once: true }));
      document.body.append(frame);
      await loaded;
      const win = frame.contentWindow;
      const doc = frame.contentDocument;
      const calls = [];
      const saved = { ...garment, id: 'saved-product', ownerName: 'Student', ownerUsername: 'student' };
      let stored = false;
      let submittedMetadata;
      const waitFor = async (condition, message) => {
        for (let attempt = 0; attempt < 200; attempt += 1) {
          if (condition()) return;
          await new Promise((resolve) => setTimeout(resolve, 10));
        }
        throw new Error(message);
      };
      win.fetch = async (url, options = {}) => {
        if (url === '/api/auth/me') return json({ user: { username: 'student', name: 'Student', role: 'student' } });
        if (url === '/api/garments/analyze') {
          calls.push('measure');
          return json({ lengthCm: 45, widthCm: 32, colorName: 'White', widthLabel: 'Width', garmentName: 'Shirt',
            analysisPhotoDataUrl: 'data:image/png;base64,iVBORw0KGgo=', message: 'Measurements ready' });
        }
        if (url === '/api/garments' && options.method === 'POST') { calls.push('save'); submittedMetadata=JSON.parse(options.body); stored = true; return json(saved, 201); }
        if (url === '/api/garments') return json(stored ? [saved] : []);
        if (url === '/api/garments/saved-product/product-photo') {
          calls.push('generate');
          return fails ? json({ error: 'AI test failure' }, 502) : json({ productPhotoUrl: photoUrl, generatedAt: 'new' }, 201);
        }
        throw new Error(`Unexpected registration request: ${url}`);
      };
      try {
        await win.eval("import('/js/measurement/register.js?v=registration-flow-test')");
        doc.querySelector('#garment-type').value = '2';
        const transfer = new win.DataTransfer();
        transfer.items.add(new win.File([new Uint8Array([255, 216, 255])], 'shirt.jpg', { type: 'image/jpeg' }));
        doc.querySelector('#gallery-photo').files = transfer.files;
        doc.querySelector('#gallery-photo').dispatchEvent(new win.Event('change'));
        await waitFor(() => !doc.querySelector('#analyze-garment').disabled, 'Photo selection did not complete');
        doc.querySelector('#analyze-garment').click();
        const form = doc.querySelector('#garment-form');
        await waitFor(() => !form.hidden, 'Measurements did not populate the form');
        form.elements.name.value = 'Test shirt';
        form.elements.gender.value = 'unisex';
        assert(!form.elements.size && !form.elements.stretch && !form.elements.style, 'Only seasons should remain in recommendation metadata');
        form.querySelector('[name="seasons"][value="봄"]').checked=true;
        form.querySelector('[name="seasons"][value="가을"]').checked=true;
        form.dispatchEvent(new win.Event('submit', { cancelable: true }));
        await waitFor(() => form.hidden && !form.querySelector('[type=submit]').disabled, 'Registration did not finish');
        assert(calls.join(',') === 'measure,save,generate', `Expected measure,save,generate but got ${calls}`);
        assert(stored && doc.querySelector('.school-closet-card'), 'Saved garment must remain in closet');
        assert(!('size' in submittedMetadata)&&submittedMetadata.seasons.length===2&&!('stretch' in submittedMetadata), 'Registration must send chosen recommendation metadata');
        assert(doc.querySelector('#analysis-status').classList.contains('is-error') === fails,
          'Generation success or failure must be shown accurately');
        if (fails) assert(doc.querySelector('#analysis-status').textContent.includes('AI test failure'), 'AI error must be explained');
      } finally { frame.remove(); }
    });
  }
} finally {
  window.fetch = originalFetch;
  window.confirm = originalConfirm;
  keys.forEach((key, index) => {
    if (previousStorage[index] === null) localStorage.removeItem(key);
    else localStorage.setItem(key, previousStorage[index]);
  });
}

for (const result of checks) {
  const row = document.createElement('li');
  row.textContent = `${result.passed ? 'PASS' : 'FAIL'}: ${result.name}${result.error ? ` - ${result.error}` : ''}`;
  document.querySelector('#results').append(row);
}
const failed = checks.filter((result) => !result.passed).length;
const summary = document.querySelector('#summary');
summary.textContent = `${checks.length - failed} passed, ${failed} failed`;
summary.dataset.failed = String(failed);
