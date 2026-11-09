"""Exact, opt-in runtime resource registry. No fetching or arbitrary file serving."""
import hashlib,json,logging,re
from pathlib import Path
# Bundles the client asks for by a catalog name we do not have as a separate
# file, but whose every sprite already lives in one recovered bundle. The
# badge grid and the NPC billboards are each a single atlas in this build, so
# any badges_* / npctrainerassets_* request is answered from that atlas. Without
# it the client gets a 404 and renders the whole medal grid and every team
# leader portrait blank.
FALLBACKS=(
 ('badges_playerdynamic_','assets/client-runtime/recovered/badges_assets_all_recovered.bundle'),
 ('npctrainerassets_assets_all_','assets/client-runtime/recovered/npcicons_assets_teamleaders_recovered.bundle'),
 ('npcicons_assets_teamleaders_','assets/client-runtime/recovered/npcicons_assets_teamleaders_recovered.bundle'),
)
class ClientResources:
    def __init__(self,root,config):
        self.files={};self.aliases={};self.root=root;self.fallbacks=[]
        for prefix,relative in FALLBACKS:
            path=(root/relative).resolve()
            if path.is_relative_to(root) and path.is_file():self.fallbacks.append((prefix,path))
        if not config.get('enabled'):return
        registry=(root/config['registry']).resolve()
        if not registry.is_relative_to(root):raise ValueError('Project-only runtime registry')
        data=json.loads(registry.read_text(encoding='utf8'))
        partial=bool(config.get('allow_partial'))
        for name,row in data['files'].items():
            # Any external catalog pair the client asks for (catalog_926,
            # catalog_ext_12, …) plus content bundles.
            if not re.fullmatch(r'(?:catalog_[A-Za-z0-9_.-]+\.(?:json|hash)|[A-Za-z0-9_.-]+\.bundle)',name):raise ValueError('Invalid runtime resource key')
            path=(root/row['file']).resolve()
            try:
                # Startup only proves the file is there with the recorded size;
                # the full SHA-256 is checked in read(), so a multi-gigabyte
                # portable cache does not have to be hashed on every launch.
                if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size!=row['bytes']:raise ValueError('Invalid runtime resource file')
            except ValueError:
                # A portable install ships the cache it could verify; an
                # unlisted bundle is simply absent, never served unchecked.
                if not partial:raise
                logging.getLogger(__name__).warning('Runtime resource unavailable: %s',name)
                continue
            self.files[name]=(path,row['sha256'])
            # The addressables catalog addresses bundles by cache hash as well
            # as by bundle name, so accept both spellings.
            version=str(row.get('cache_version') or '').strip().lower()
            if version:
                self.aliases.setdefault(version, name)
                self.aliases.setdefault(version+'.bundle', name)
    def resolve(self,name):
        """Map a catalog request path onto a registry key.

        Catalog internal IDs are ``...\\bundles\\Android\\<name>.bundle``, and
        a Unity cache fetch can use ``<cache-hash>.bundle``. The registry is
        still the only allowlist; this only strips routing decoration.
        """
        key=str(name or '').replace('\\','/').split('?',1)[0].strip('/')
        changed=True
        while changed:
            changed=False
            for prefix in ('bundles/Android/','bundles/android/','bundles/','Android/','android/'):
                if key.startswith(prefix):
                    key=key[len(prefix):];changed=True
        if key in self.files:
            return key
        return self.aliases.get(key.lower(), key)
    def read(self,name):
        key=self.resolve(name)
        item=self.files.get(key)
        if not item:
            for prefix,path in self.fallbacks:
                if key.startswith(prefix):
                    return path.read_bytes(),'application/octet-stream'
            return None
        if not item:return None
        raw=item[0].read_bytes()
        if hashlib.sha256(raw).hexdigest()!=item[1]:raise ValueError('Runtime resource changed after startup')
        return raw,'application/json' if name.endswith('.json') else 'application/octet-stream'
