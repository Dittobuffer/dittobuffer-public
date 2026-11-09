/* Shared admin-site capability exchange. The key stays in the URL fragment,
   is immediately removed, and is never stored in localStorage or a cookie. */
(()=>{
 const request=window.fetch.bind(window);let csrf='';
 const ready=(async()=>{
  const hash=new URLSearchParams(location.hash.slice(1)),key=hash.get('admin');
  if(key){history.replaceState(null,'',location.pathname+location.search);const r=await request('/api/admin-session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({access_key:key})});if(!r.ok)throw Error('Invalid administrator access link');}
  const r=await request('/api/admin-session');if(!r.ok)throw Error('Open the private administrator link. Its key is in .private/admin-website-access.json; append #admin=KEY to this website URL. No separate Studio password is needed.');csrf=(await r.json()).csrf;
 })();
 window.fetch=async(url,options={})=>{
  if(typeof url==='string'&&url.startsWith('/api/')){await ready;options={...options,headers:{...(options.headers||{}),'X-Admin-CSRF':csrf}};}
  return request(url,options);
 };
 ready.catch(e=>{const box=document.createElement('div');box.setAttribute('role','alert');box.style.cssText='position:fixed;inset:24px auto auto 50%;transform:translateX(-50%);z-index:10000;max-width:660px;padding:24px;background:#fff3dc;border:1px solid #c27816;border-radius:12px;color:#442c0f';box.textContent=e.message;document.body.append(box);});
})();
