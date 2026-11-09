"""One authorization boundary for the entire administrator website.

The private operator capability opens an HttpOnly site session. Studio reuses
that session; game/account tokens and loopback addresses confer no privileges.
"""
import hashlib,hmac,json,secrets,threading,time
from pathlib import Path
from http.cookies import SimpleCookie
from urllib.parse import urlparse
from dittobuffer.paths import ROOT

class AccessDenied(ValueError):
    def __init__(self,status,message):self.status=status;super().__init__(message)

class Access:
    def __init__(self,private_file=None):
        self.path=Path(private_file or ROOT/'.private/admin-website-access.json')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        if not self.path.exists():
            # Exclusive creation: concurrent starts must reuse the first key.
            try:
                with self.path.open('x',encoding='utf8') as f:json.dump({'access_key':secrets.token_urlsafe(40)},f)
            except FileExistsError:pass
        self.key=json.loads(self.path.read_text(encoding='utf8'))['access_key']
        self.sessions={};self.lock=threading.Lock()

    def exchange(self,key):
        if not isinstance(key,str) or not hmac.compare_digest(key,self.key):raise AccessDenied(401,'Use the private administrator access link')
        token=secrets.token_urlsafe(40);row={'user':'website-admin','role':'publisher','csrf':secrets.token_urlsafe(32),'expires':time.monotonic()+8*3600}
        with self.lock:self.sessions[hashlib.sha256(token.encode()).hexdigest()]=row
        return token,row

    def session(self,headers,write=False,publish=False):
        try:
            cookie=SimpleCookie();cookie.load(headers.get('Cookie',''));token=cookie['dittobuffer_admin'].value
        except (KeyError,ValueError):raise AccessDenied(401,'Open the private administrator access link') from None
        with self.lock:row=self.sessions.get(hashlib.sha256(token.encode()).hexdigest())
        if not row or row['expires']<time.monotonic():raise AccessDenied(401,'Administrator session expired')
        if write:
            if not hmac.compare_digest(headers.get('X-Admin-CSRF',headers.get('X-Studio-CSRF','')),row['csrf']):raise AccessDenied(403,'Administrator CSRF check failed')
            origin=headers.get('Origin')
            if origin and urlparse(origin).netloc!=headers.get('Host'):raise AccessDenied(403,'Cross-origin administrator request blocked')
        return row


def access(admin):
    with admin.lock:
        if not hasattr(admin,'website_access'):admin.website_access=Access()
        return admin.website_access
