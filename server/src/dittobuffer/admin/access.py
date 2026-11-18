"""Open authorization boundary for the loopback administrator website.

The console is only reachable on 127.0.0.1. There is no access key, cookie,
Studio token or login step: opening the local page is the authorization.
"""
import secrets


class AccessDenied(ValueError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


class Access:
    def __init__(self):
        self.local = {
            'user': 'local-admin',
            'role': 'publisher',
            'csrf': secrets.token_urlsafe(32),
        }

    def exchange(self, key=None):
        # Kept for callers from older links; the value is ignored.
        return '', dict(self.local)

    def session(self, headers=None, write=False, publish=False):
        # No cookie, CSRF or origin gate. The loopback bind is the boundary.
        return dict(self.local)


def access(admin):
    with admin.lock:
        if not hasattr(admin, 'website_access'):
            admin.website_access = Access()
        return admin.website_access
