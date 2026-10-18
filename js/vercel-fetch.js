// Vercel은 요청·응답 한 번에 4.5MB까지만 보낼 수 있어요.
// 큰 사진 요청은 조각으로 나눠 /api/_chunks 로 먼저 보내고, 큰 응답은 조각을 받아 다시 합칩니다.
// 로컬 server.py에는 /api/_platform 이 없으므로 이 파일은 아무것도 바꾸지 않아요.
(function () {
  const CHUNK_CHARS = 3000000;
  const originalFetch = window.fetch.bind(window);
  let platformCheck = null;

  function supportsChunks() {
    if (!platformCheck) {
      platformCheck = originalFetch('/api/_platform', { cache: 'no-store' })
        .then((response) => (response.ok ? response.json() : {}))
        .then((value) => value.chunked === true)
        .catch(() => false);
    }
    return platformCheck;
  }

  function apiUrl(input) {
    const raw = typeof input === 'string' ? input : input instanceof URL ? input.href : input && input.url;
    if (!raw) return null;
    const url = new URL(raw, window.location.href);
    return url.origin === window.location.origin && url.pathname.startsWith('/api/') ? url : null;
  }

  function newId() {
    if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID().replace(/-/g, '');
    return Array.from({ length: 32 }, () => Math.floor(Math.random() * 16).toString(16)).join('');
  }

  async function sendInPieces(body) {
    const id = newId();
    const count = Math.ceil(body.length / CHUNK_CHARS);
    for (let index = 0; index < count; index += 1) {
      const response = await originalFetch(`/api/_chunks/${id}/${index}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'text/plain; charset=utf-8' },
        body: body.slice(index * CHUNK_CHARS, (index + 1) * CHUNK_CHARS),
      });
      if (!response.ok) return { error: response };
    }
    return { marker: `${id}:${count}` };
  }

  async function joinPieces(response, marker) {
    const [id, countText] = marker.split(':');
    const pieces = [];
    for (let index = 0; index < Number(countText); index += 1) {
      const piece = await originalFetch(`/api/_chunks/${id}/${index}`, { cache: 'no-store' });
      if (!piece.ok) return piece;
      pieces.push(await piece.text());
    }
    const headers = new Headers(response.headers);
    headers.delete('X-FitShare-Chunked');
    headers.delete('Content-Length');
    return new Response(pieces.join(''), { status: response.status, statusText: response.statusText, headers });
  }

  window.fetch = async function fitshareFetch(input, init) {
    if (!apiUrl(input)) return originalFetch(input, init);
    let options = init;
    const body = init && init.body;
    if (typeof body === 'string' && body.length > CHUNK_CHARS && (await supportsChunks())) {
      const sent = await sendInPieces(body);
      if (sent.error) return sent.error;
      const headers = new Headers(init.headers);
      headers.set('X-FitShare-Upload', sent.marker);
      options = { ...init, headers, body: '{}' };
    }
    const response = await originalFetch(input, options);
    const marker = response.headers.get('X-FitShare-Chunked');
    return marker ? joinPieces(response, marker) : response;
  };
})();
