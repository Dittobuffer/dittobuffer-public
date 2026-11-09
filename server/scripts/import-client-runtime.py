"""Copy exact cached 0.429.2 runtime bundles read-only from the authorized dev clone."""
import os,shutil,argparse,hashlib,io,json,re,shlex,subprocess,sys,tarfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'src'))
from dittobuffer.core.asset_catalog import Catalog
class RuntimeCatalog(Catalog):
    @classmethod
    def string(cls,data,offset):
        if data[offset] in (0,1):return super().string(data,offset)
        # This read-only catalog also has non-string dependency-bucket keys.
        # Dependency references use bucket indices, so preserve their identity.
        return 'opaque-key-at-'+str(offset),offset+1

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--serial',required=True);ap.add_argument('--output',required=True);ap.add_argument('--adb',default=os.environ.get('ADB') or shutil.which('adb') or 'adb');args=ap.parse_args()
    output=(ROOT/args.output).resolve()
    if not any(output.is_relative_to(ROOT/name) and output!=ROOT/name for name in ('.private','.staging')) or output.exists():raise ValueError('Fresh ignored private import output required')
    output.mkdir();adb=[args.adb,'-s',args.serial];prefix='/sdcard/Android/data/org.librenlp.pokemongo.dev/files/'
    def root(command,binary=False):return subprocess.check_output([*adb,'exec-out','su','-c',command],timeout=180,**({} if binary else {'text':True}))
    files={}
    for name in ['catalog_926.json','catalog_926.hash']:
        raw=root('cat '+shlex.quote(prefix+'com.unity.addressables/'+name),True);(output/name).write_bytes(raw)
        files[name]={'file':(output/name).relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
    catalog=RuntimeCatalog(json.loads((output/'catalog_926.json').read_text(encoding='utf8')))
    paths=root('find '+prefix+'UnityCache/Shared -type f -name __data').splitlines();versions={}
    for path in paths:
        if not re.fullmatch(re.escape(prefix)+r'UnityCache/Shared/[A-Za-z0-9_]+/[a-f0-9]{32}/__data',path):raise ValueError('Unexpected dev cache path')
        versions.setdefault(path.split('/')[-2],[]).append(path)
    rows=[];missing=[]
    for i in range(len(catalog.entries)):
        loc=catalog.location(i)
        if not loc['type'].endswith('.IAssetBundleResource'):continue
        opt=catalog.options(i)['value'];name=loc['internal_id'].replace('\\','/').rsplit('/',1)[-1]
        if not re.fullmatch(r'[A-Za-z0-9_.-]+\.bundle',name):raise ValueError('Unexpected runtime bundle name')
        candidates=versions.get(opt['m_Hash'],[])
        if not candidates:missing.append(name);continue
        rows.append({'name':name,'source':candidates[0],'version':opt['m_Hash'],'expected_bytes':opt['m_BundleSize']})
    selected={r['source'].lstrip('/'):r for r in rows}
    archive=root('tar -cf - -C / '+' '.join(shlex.quote(r['source'].lstrip('/')) for r in rows),True)
    with tarfile.open(fileobj=io.BytesIO(archive),mode='r:') as tar:
        for member in tar.getmembers():
            if member.name not in selected or not member.isfile():raise ValueError('Unexpected archive entry')
            row=selected[member.name];raw=tar.extractfile(member).read()
            if len(raw)!=row['expected_bytes'] or not raw.startswith((b'UnityFS\0',b'UnityRaw\0',b'UnityWeb\0')):raise ValueError('Cached resource does not match its catalog')
            path=output/row['name'];path.write_bytes(raw)
            files[row['name']]={'file':path.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'source':row['source'],'cache_version':row['version']}
    if len(files)!=len(rows)+2:raise ValueError('Incomplete exact cache copy')
    result={'format':1,'source':'Read-only development clone 0.429.2 catalog_926 and matching Unity cache; private research only','serial':args.serial,'files':files,'missing_bundles':missing,'counts':{'bundles':len(rows),'bytes':sum(r['bytes'] for r in files.values()),'missing':len(missing)}}
    (output/'registry.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result['counts']))
if __name__=='__main__':main()
