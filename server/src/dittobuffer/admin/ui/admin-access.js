/* The local admin is open on loopback. */
(() => {
  const request = window.fetch.bind(window);
  let csrf = '';
  const ready = (async () => {
    if (location.hash) history.replaceState(null, '', location.pathname + location.search);
    const r = await request('/api/admin-session');
    if (!r.ok) throw Error('The local admin did not respond. Restart the server and try again.');
    const value = await r.json();
    csrf = value.csrf || '';
  })();
  window.fetch = async (url, options = {}) => {
    if (typeof url === 'string' && url.startsWith('/api/')) {
      await ready;
      options = { ...options, headers: { ...(options.headers || {}), 'X-Admin-CSRF': csrf } };
    }
    return request(url, options);
  };
  ready.catch(e => {
    const box = document.createElement('div');
    box.setAttribute('role', 'alert');
    box.style.cssText = 'position:fixed;inset:24px auto auto 50%;transform:translateX(-50%);z-index:10000;max-width:660px;padding:24px;background:#fff3dc;border:1px solid #c27816;border-radius:12px;color:#442c0f';
    box.textContent = e.message;
    document.body.append(box);
  });
})();
