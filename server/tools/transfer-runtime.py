"""Private transfer extractor/provisioner/verifier. No operations on source PC data."""
from pathlib import Path
import argparse,json,hashlib,zipfile,shutil,subprocess,sys,os,time,ssl,importlib.util,secrets,ipaddress,socket,sqlite3,re,tarfile,tempfile
PKG=Path(__file__).resolve().parent

def activate(root):
 # A source checkout need not be installed into the interpreter that launched
 # this standalone helper. The maintained Python package lives under src/.
 for path in (root,root/'protocol',root/'scripts',root/'src'):
  value=str(path)
  if value not in sys.path:sys.path.insert(0,value)

def python_env(root):
 env=dict(os.environ);existing=env.get('PYTHONPATH','')
 env['PYTHONPATH']=os.pathsep.join([str(root/'src'),str(root/'protocol'),str(root)]+([existing] if existing else []))
 return env

def transfer_manifest(required=True):
 path=PKG/'TRANSFER_MANIFEST.json'
 if not path.is_file():
  if required:raise ValueError('This action requires the private transfer package tools/TRANSFER_MANIFEST.json; normal provisioning does not')
  return {}
 return json.loads(path.read_text(encoding='utf8'))

def protect(raw,decrypt=False):
 # Preserve the original CurrentUser DPAPI blob format without importing a
 # historical Android fixture (which is neither shipped nor needed here).
 if os.name!='nt':raise ValueError('Private transfer credentials require Windows CurrentUser DPAPI; no plaintext fallback is available')
 import ctypes
 from ctypes import wintypes
 class Blob(ctypes.Structure):_fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
 buffer=ctypes.create_string_buffer(raw);source=Blob(len(raw),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));out=Blob()
 fn=ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
 fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)];fn.restype=wintypes.BOOL
 if not fn(ctypes.byref(source),None,None,None,None,1,ctypes.byref(out)):raise ctypes.WinError()
 try:return ctypes.string_at(out.data,out.size)
 finally:
  free=ctypes.windll.kernel32.LocalFree;free.argtypes=[ctypes.c_void_p];free.restype=ctypes.c_void_p;free(ctypes.cast(out.data,ctypes.c_void_p))

def reference_path(root,name):
 return root/('src/dittobuffer/world/' if name=='world-item-rules' else 'src/dittobuffer/gameplay/')/(name+'.json')

def source_profiles(root):
 return [('normal','src/dittobuffer/gameplay/config.json'),('world','src/dittobuffer/gameplay/netherlands.json')]

def native_artifact(root):
 # New builds must remain beneath client/native/target, already ignored by the
 # maintained Rust project's policy. Legacy archive artifacts are read-only fallbacks.
 for relative in ['client/native/target/aarch64-linux-android/release/libNianticLabsPlugin.so','client/plugin/libNianticLabsPlugin.so','builds/libNianticLabsPlugin.so']:
  path=root/relative
  if path.is_file():return path
 raise ValueError('Build scripts/build-native.sh first; expected client/native/target/aarch64-linux-android/release/libNianticLabsPlugin.so')

def io_path(p):
 return Path('\\\\?\\'+str(p)) if os.name=='nt' and len(str(p))>=240 and not str(p).startswith('\\\\?\\') else p

def sha(p):
 with io_path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def run(args,root,log):
 p=subprocess.run(list(map(str,args)),cwd=root,stdout=log,stderr=subprocess.STDOUT,env=python_env(root))
 if p.returncode:raise RuntimeError('Command failed: '+str(args[0])+' (see restoration log)')
def load_module(path,name):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def extract(root,m,allow_overlay=False):
 if root==PKG or root.is_relative_to(PKG) or PKG.is_relative_to(root):raise ValueError('Package and destination must be separate sibling locations')
 state=root/'.transfer/restore-state.json';identity=sha(PKG/'TRANSFER_MANIFEST.json')
 if root.exists() and any(root.iterdir()) and not state.exists() and not allow_overlay:raise ValueError('Nonempty installation: explicit backup and confirmation required')
 if state.exists() and json.loads(state.read_text())['package_id']!=identity:raise ValueError('Destination belongs to another transfer; preserve it and use a new directory')
 # All checksums are checked BEFORE creating any destination payload.
 for archive in m['archives']:
  p=PKG/archive['file'];assert p.stat().st_size==archive['size'] and sha(p)==archive['sha256'],archive['file']
 root.mkdir(parents=True,exist_ok=True);state.parent.mkdir(parents=True,exist_ok=True);state.write_text(json.dumps({'package_id':identity,'started':time.time()}))
 groups={}
 for v in m['files']:
  rel=Path(v['restore_path']);dest=(root/rel).resolve()
  if rel.is_absolute() or '..' in rel.parts or not dest.is_relative_to(root):raise ValueError('Unsafe restore path')
  groups.setdefault(v['archive'],{}).setdefault(v['member'],[]).append(v)
 for archive,members in groups.items():
  print('Restoring',archive,flush=True)
  with zipfile.ZipFile(PKG/archive) as z:
   for member,entries in members.items():
    todo=[]
    for v in entries:
     dest=io_path(root/v['restore_path'])
     if dest.exists():
      if v['restore_path']=='builds/libNianticLabsPlugin.so':continue
      if dest.stat().st_size!=v['size'] or sha(dest)!=v['sha256']:
       if allow_overlay:todo.append((v,dest));continue
       raise ValueError('Existing destination changed; not overwritten: '+v['restore_path'])
     else:todo.append((v,dest))
    if not todo:continue
    v,dest=todo[0];dest.parent.mkdir(parents=True,exist_ok=True);staging=root/'.transfer/staging';staging.mkdir(parents=True,exist_ok=True);tmp=staging/(v['sha256']+'-'+secrets.token_hex(4));h=hashlib.sha256();size=0
    with z.open(member) as incoming,tmp.open('xb') as out:
     while data:=incoming.read(1024*1024):out.write(data);h.update(data);size+=len(data)
    assert size==v['size'] and h.hexdigest()==v['sha256'],v['restore_path'];os.replace(tmp,dest)
    for other,target in todo[1:]:target=io_path(target);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(dest,target)
 print('Extraction verified; all paths preserved',flush=True)

def configure(root,host,port,rotate=False):
 activate(root)
 ip=ipaddress.ip_address(host)
 if not (ip.is_private or ip.is_loopback):raise ValueError('Use a private or loopback interface')
 if type(port) is not int or not 1<=port<=65535:raise ValueError('Valid TLS port required')
 # Fail before TLS/profile/account writes if secure local storage is absent.
 protect(b'private-transfer-provision-preflight')
 private=root/'.private';private.mkdir(exist_ok=True);state=private/'provision-state.json';old=json.loads(state.read_text()) if state.exists() else {}
 if old and old['host']!=host and not rotate:raise ValueError('Host changed: rerun with -RotateTls or use the existing host')
 prefix=old.get('tls_prefix','.private/transfer-tls')
 if rotate:prefix='.private/transfer-tls-'+str(time.time_ns())
 if not (root/prefix).exists():load_module(root/'scripts/create-family-tls.py','create_tls').create(host,root/prefix)
 cfg=private/'family.json'
 # Preserve an existing provisioned collection on reruns. Fresh provisioning
 # has its own database, separate from the normal config/family.json profile.
 config=json.loads((cfg if cfg.exists() else root/'config/family.json').read_text(encoding='utf8'))
 if not cfg.exists():config.update(database='data/private-transfer-family.sqlite3',profile='private-transfer-family')
 config.update(host=host,port=port)
 config['authentication']={**config.get('authentication',{}),'mode':'family'}
 config['network']={**config.get('network',{}),'mode':'tls','certificate':prefix+'/server.pem','private_key':prefix+'/server-key.pem'}
 cfg.write_text(json.dumps(config,indent=2)+'\n')
 from dittobuffer.gameplay.game import Game
 game=Game(config)
 credentials=private/'bootstrap-passwords.dpapi'
 created=not credentials.exists()
 if credentials.exists():accounts=json.loads(protect(credentials.read_bytes(),True))
 else:
  with game.db.transaction() as db:assert db.execute('SELECT count(*) FROM family_accounts').fetchone()[0]==0,'Existing accounts: not replaced'
  accounts=[]
  for name in ['devone','devtwo']:
   password=secrets.token_urlsafe(24);game.accounts.register(name,password,name.title(),game.accounts.invite(),peer='private-transfer-provision');accounts.append({'username':name,'password':password})
  credentials.write_bytes(protect(json.dumps(accounts).encode()))
 # No passwords/tickets returned in logs or reports.
 with game.db.transaction() as db:
  players=[r[0] for r in db.execute('SELECT player_id FROM family_accounts ORDER BY username')];assert len(set(players))>=2
  owned_count=db.execute('SELECT count(*) FROM pokemon').fetchone()[0]
  if created:assert owned_count==0
 for a in accounts:
  result=game.accounts.login(a['username'],a['password'],peer='transfer-validation');assert result['token'].startswith('lf1_')
 public={'origin':f'https://{host}:{port}','ca_file':prefix+'/ca.pem','world_style':json.loads((root/'src/dittobuffer/world/world-style.json').read_text())}
 runtime=config.get('client_resources',{})
 if runtime.get('enabled'):public['runtime_registry']=runtime['registry']
 for key in ('rocket_server_combat','pvp_server_combat','max_server','routes_foreground_creation','routes_saved_postcard_compatibility'):
  if config.get(key) is True:public[key]=True
 for section,key in [('rocket','rocket_server_combat'),('pvp','pvp_server_combat'),('max_system','max_server')]:
  if config.get(section,{}).get('enabled') is True:public[key]=True
 (root/'builds').mkdir(exist_ok=True)
 (root/'builds/transfer-public-profile.json').write_text(json.dumps(public,indent=2)+'\n')
 state.write_text(json.dumps({'host':host,'port':port,'tls_prefix':prefix,'accounts':2,'passwords':'CurrentUser DPAPI encrypted; not logged'}))
 return {'status':'MATCH','independent_accounts':2,'owned_pokemon':owned_count,'credentials':'fresh DPAPI-protected; never copied from source'}

def java_home():
 p=subprocess.run(['java','-XshowSettings:properties','-version'],capture_output=True,text=True)
 match=re.search(r'java.home = ([^\r\n]+)',p.stderr)
 if p.returncode or not match:raise ValueError('JDK 21 (javac/keytool) required')
 return Path(match[1].strip())

def native(root,log):
 required=['scripts/test-native.sh','scripts/build-native.sh','scripts/fix-ndk-modes.py']
 missing=[name for name in required if not (root/name).is_file()]
 if missing:raise ValueError('Private native transfer requires its reviewed helper sources: '+', '.join(missing)+'; maintained Rust sources are in client/native')
 p=subprocess.run(['wsl.exe','-d','Ubuntu-24.04','--exec','wslpath','-a','-u',root.as_posix()],capture_output=True,text=True)
 if p.returncode:raise ValueError('WSL Ubuntu-24.04 with GCC/tar/curl/python3 is required')
 linux=p.stdout.strip()
 # Do not overwrite an existing native toolchain on a rerun.
 marker=root/'.transfer/native-extracted.json'
 if not marker.exists():
  run(['wsl.exe','-d','Ubuntu-24.04','--exec','tar','--no-same-owner','-xf',linux+'/.transfer/native-toolchain.tar','-C',linux],root,log);marker.write_text('{}')
 run(['wsl.exe','-d','Ubuntu-24.04','--exec','python3',linux+'/scripts/fix-ndk-modes.py','--extract'],root,log)
 # Validate every copied native file/link under WSL, where Unix symlinks work.
 expected=root/'.transfer/native-members.json';expected.write_text(json.dumps(transfer_manifest()['native_members']))
 helper=root/'.transfer/native-verify.py'
 helper.write_text('''from pathlib import Path
import sys,json,hashlib,os
root=Path(sys.argv[1]);records=json.loads((root/'.transfer/native-members.json').read_text())
for v in records:
 p=root/v['restore_path']
 if v['kind']=='file':
  with p.open('rb') as f:h=hashlib.file_digest(f,'sha256').hexdigest()
  assert h==v['sha256'],v['restore_path']
 elif p.is_symlink():assert os.readlink(p)==v['link_target'],v['restore_path']
 else:assert p.samefile(root/v['link_target']),v['restore_path']
print('Native source files and links verified:',len(records))
''')
 run(['wsl.exe','-d','Ubuntu-24.04','--exec','python3',linux+'/.transfer/native-verify.py',linux],root,log)
 # Validate and build offline from the copied registry and exact pinned toolchain.
 for script in ['scripts/test-native.sh','scripts/build-native.sh']:
  if not (root/script).is_file():raise ValueError('Private native transfer requires its reviewed '+script+' entry point; use the maintained client/native source and restore the toolchain separately')
  run(['wsl.exe','-d','Ubuntu-24.04','--exec','env','CARGO_NET_OFFLINE=true','sh',linux+'/'+script],root,log)
 return {'status':'MATCH','rust_host_tests':'PASS','android_arm64_native_build':'PASS','artifact':'client/native/target/aarch64-linux-android/release/libNianticLabsPlugin.so','android_execution':'NOT_TESTED'}

def android(root,log):
 from urllib.parse import urlsplit
 root=Path(root).resolve()
 def project(relative):
  path=root/relative
  if path.resolve()!=path or not path.resolve().is_relative_to(root):raise ValueError('Android build path must stay project-local without symlink aliases: '+str(relative))
  return path
 def required(relative):
  path=project(relative)
  if not path.is_file():raise ValueError('Missing Android build input: '+str(relative))
  return path
 # Preflight everything before signer creation or working-tree copies. The old
 # decoded trees remain read-only sources, even on failed or repeated attempts.
 builder=required('scripts/build-standalone.py')
 native_lib=native_artifact(root)
 if not native_lib.resolve().is_relative_to(root):raise ValueError('Native plugin leaves the project')
 with native_lib.open('rb') as stream:
  if stream.read(4)!=b'\x7fELF':raise ValueError('Native plugin must contain actual ELF bytes, not an LFS pointer')
 public_path=required('builds/transfer-public-profile.json');public=json.loads(public_path.read_text(encoding='utf8'))
 if not isinstance(public,dict) or not isinstance(public.get('origin'),str) or not public['origin'].startswith('https://'):raise ValueError('Public Android profile requires an HTTPS origin')
 origin=urlsplit(public['origin'])
 if not origin.hostname or origin.username is not None or origin.password is not None or origin.query or origin.fragment:raise ValueError('Public Android origin must not contain credentials, query data or fragments')
 def review(value):
  if isinstance(value,dict):
   for key,item in value.items():
    if key.lower() in ('password','token','access_token','refresh_token','auth_ticket','account_id','username','private_key'):raise ValueError('Public Android profile contains private account or key material')
    review(item)
  elif isinstance(value,list):
   for item in value:review(item)
 review(public)
 ca_value=public.get('ca_file')
 if not isinstance(ca_value,str) or Path(ca_value).is_absolute():raise ValueError('Project-relative public CA file required')
 ca=required(ca_value)
 for suffix in ['', '2', '3']:required('builds/client-base/classes'+suffix+'.dex')
 required('builds/client-base/AndroidManifest.xml')
 split_libs=project('builds/client-split/lib/arm64-v8a')
 if not split_libs.is_dir() or not any(split_libs.glob('*.so')):raise ValueError('Reviewed ARM64 decoded split libraries required')
 for part in ['base','split']:
  if any(path.is_symlink() for path in project('builds/client-'+part).rglob('*')):raise ValueError('Decoded Android inputs must not contain symlink aliases')
 for relative in ('.staging','apk/built','.tools','.private'):project(relative)
 home=java_home();key=project('.tools/local-test.jks');fresh_signer=not key.exists()
 if not key.exists():
  key.parent.mkdir(parents=True,exist_ok=True)
  run([home/'bin/keytool.exe','-genkeypair','-keystore',key,'-storepass','android','-keypass','android','-alias','dittobuffer','-keyalg','RSA','-keysize','2048','-validity','3650','-dname','CN=Dittobuffer new PC development'],root,log)
 stage=project('.staging');stage.mkdir(exist_ok=True)
 output=Path(tempfile.mkdtemp(prefix='transfer-android-',dir=stage))
 build_profile=output/'public-profile.json';shutil.copy2(public_path,build_profile)
 for part in ['base','split']:
  shutil.copytree(root/('builds/client-'+part),output/part)
 for part in ['base','split']:
  target=output/part/'lib/arm64-v8a/libNianticLabsPlugin.so';target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(native_lib,target)
 run([sys.executable,builder,'--profile',build_profile,'--output',output],root,log)
 apk=output/'dittobuffer-family.apk'
 with zipfile.ZipFile(apk) as z:
  for suffix in ['', '2', '3']:
   name='classes'+suffix+'.dex';assert hashlib.sha256(z.read(name)).hexdigest()==sha(root/'builds/client-base'/name),name
  libs=0
  for lib in (root/'builds/client-split/lib/arm64-v8a').glob('*.so'):
   if lib.name.startswith('libdittobuffer_'):continue
   expected=native_lib if lib.name=='libNianticLabsPlugin.so' else lib
   assert hashlib.sha256(z.read('lib/arm64-v8a/'+lib.name)).hexdigest()==sha(expected),lib.name;libs+=1
  assert 'classes4.dex' in z.namelist()
  actual=json.loads(z.read('assets/dittobuffer/profile.json'))
  assert actual['origin']==public['origin'] and actual['ca_asset']=='dittobuffer/family-ca.pem'
  assert hashlib.sha256(z.read('assets/dittobuffer/family-ca.pem')).hexdigest()==sha(ca)
  runtime=json.loads(z.read('assets/dittobuffer/libdittobuffer_runtime.config.so'))
  assert runtime['interaction']['type']=='script' and 'address' not in runtime['interaction'] and 'port' not in runtime['interaction']
 build_report=output/'build.json'
 if not build_report.is_file():raise ValueError('Standalone builder did not emit its build manifest')
 export=project('apk/built');export.mkdir(parents=True,exist_ok=True)
 identity=output.name.removeprefix('transfer-android-');destination=export/('dittobuffer-transfer-'+identity+'.apk')
 with apk.open('rb') as incoming,destination.open('xb') as outgoing:shutil.copyfileobj(incoming,outgoing)
 with build_report.open('rb') as incoming,destination.with_suffix('.build.json').open('xb') as outgoing:shutil.copyfileobj(incoming,outgoing)
 result={'status':'MATCH','preserved_game_dex_files':3,'verified_native_libraries':libs,'embedded_new_origin_and_ca':True,'fresh_signer':fresh_signer,'apk_bytes':destination.stat().st_size,'sha256':sha(destination),'artifact':destination.relative_to(root).as_posix(),'working_directory':output.relative_to(root).as_posix(),'android_install_and_gameplay':'NOT_TESTED'}
 private=project('.private');private.mkdir(exist_ok=True);state=private/('transfer-android-'+identity+'.json')
 with state.open('x',encoding='utf8') as stream:json.dump(result,stream,indent=2);stream.write('\n')
 result['artifact_state']=state.relative_to(root).as_posix()
 return result

def game_master_semantics(root):
 activate(root)
 from dittobuffer.gameplay.species import catalog,form_catalog,form_metadata
 from dittobuffer.gameplay.master import template_bytes
 gm=root/'data/static/gmts.binpb.gz'
 def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
 defaults=catalog(gm);forms=form_catalog(gm);metadata=form_metadata(gm)
 output={'default_species':len(defaults),'stat_forms':len(forms),'form_metadata':len(metadata),
  'default_rules_sha256':fingerprint({str(k):v for k,v in defaults.items()}),
  'form_rules_sha256':fingerprint({str(k):v for k,v in forms.items()}),
  'form_metadata_sha256':fingerprint({str(k):v for k,v in metadata.items()}),'catalogs':{},'delivered_templates':{}}
 for name in ['pokemon-reference','battle-moves','mega-rules','avatar-catalog','trainer-reference','raid-items','world-item-rules']:
  data=json.loads(reference_path(root,name).read_text());output['catalogs'][name]=fingerprint(data)
 for label,file in source_profiles(root):
  payload=sorted(template_bytes(json.loads((root/file).read_text())))
  output['delivered_templates'][label]={'count':len(payload),'semantic_wire_sha256':hashlib.sha256(b''.join(len(v).to_bytes(8,'big')+v for v in payload)).hexdigest()}
 return output


def verify(root,m,deep=False):
 activate(root);results={};missing=[];mismatch=[]
 # Compiled artifacts may change during a fresh build. Source/resources are fixed.
 for v in m['files']:
  if v['restore_path']=='builds/libNianticLabsPlugin.so':continue
  p=io_path(root/v['restore_path'])
  if not p.is_file():missing.append(v['restore_path'])
  elif p.stat().st_size!=v['size'] or sha(p)!=v['sha256']:mismatch.append(v['restore_path'])
 results['payloads']={'status':'MISSING' if missing else 'MISMATCH' if mismatch else 'MATCH','missing':missing,'mismatch':mismatch}
 from dittobuffer.core.asset_catalog import AssetStore,Catalog,validate_manifest
 model_path='config/resources/pokemon-assets.json' if (root/'config/resources/pokemon-assets.json').is_file() else 'builds/m113-reviewed-assets.json'
 store=AssetStore(root,{'enabled':True,'manifest':model_path});model=store.manifest;variants=[v for v in model['variants'] if not v['missing_dependencies']]
 species={v['species'] for v in variants};shiny={v['species'] for v in variants if '.s' in v['address'].split('.g')[0]}
 assert len(species)==958 and len(shiny)==951 and len(variants)==3700
 catalog=Catalog(json.loads(store.read('/assets/pokemon/catalog.json')[0]));structure=validate_manifest(model,catalog)
 results['models']={'status':'MATCH','species':len(species),'shiny_species':len(shiny),'complete_appearance_keys':len(variants),'bundles':len(model['content']),'structure':structure,'actual_animation_on_second_pc':'NOT_TESTED'}
 avatar_path='config/resources/avatar-assets.json' if (root/'config/resources/avatar-assets.json').is_file() else 'builds/m10-avatar-assets.json'
 avatar=AssetStore(root,{'enabled':True,'manifest':avatar_path});av=avatar.manifest
 avatar_structure=validate_manifest(av,Catalog(json.loads(avatar.read('/assets/avatar/catalog.json')[0])))
 results['avatar_resources']={'status':'MATCH','located_bundles':len(av['content']),'structure':avatar_structure,'known_source_gaps':'preserved'}
 from dittobuffer.core.client_resources import ClientResources
 runtime_path='config/resources/client-runtime.json'
 runtime=ClientResources(root,{'enabled':True,'registry':runtime_path})
 results['client_runtime']={'status':'MATCH','located_files':len(runtime.files),'known_missing_bundles':len(json.loads((root/runtime_path).read_text()).get('missing_bundles',[])),'actual_device_loading':'NOT_TESTED'}
 pointer=json.loads((root/'data/world/current.json').read_text());assert sha(root/pointer['database'])==pointer['sha256']
 with sqlite3.connect((root/pointer['database']).as_uri()+'?mode=ro&immutable=1',uri=True) as db:counts=dict(db.execute("SELECT kind,count(*) FROM pois WHERE status='APPROVED' GROUP BY kind"))
 assert counts=={0:4524,1:20023};results['world']={'status':'MATCH','gyms':counts[0],'stops':counts[1],'all_provinces':'source coverage report preserved','geometry_fix':'source hash checked'}
 for label,file in source_profiles(root):
  current=json.loads((root/file).read_text());expected=m['source_features'][label]
  keys=set(expected)-{'database','player_name','host','port','profile','authentication','network'}
  assert all(current.get(k)==expected.get(k) for k in keys),label
 results['feature_configuration']={'status':'MATCH','family_social_multiplayer':'enabled in fresh family profile','known_incomplete_features':'preserved, not certified by transfer'}
 semantics=game_master_semantics(root);assert semantics==json.loads((PKG/'GM_PARITY_EXPECTED.json').read_text()),'Game Master semantic mismatch'
 results['game_master']={'status':'MATCH','source_semantics':semantics,'raw_compatibility_inputs':'payload hashes verified'}
 from elftools.elf.elffile import ELFFile
 with native_artifact(root).open('rb') as f:
  elf=ELFFile(f);exports={x.name for x in elf.get_section_by_name('.dynsym').iter_symbols() if x['st_shndx']!='SHN_UNDEF'}
  required={'GetN2Api','Java_com_nianticlabs_nia_unity_UnityUtil_nativeInit','CreateBackgroundContext','UnityPluginLoad','UnityPluginUnload'}
  assert elf.header['e_machine']=='EM_AARCH64' and required<=exports
 results['native_abi']={'status':'MATCH','architecture':'EM_AARCH64','required_exports':sorted(required),'runtime_execution_on_second_pc_android':'NOT_TESTED'}
 if deep:
  import UnityPy
  cab_owners={}
  for h,v in model['content'].items():
   env=UnityPy.load(str(root/v['file']))
   for obj in env.objects:
    name=obj.assets_file.name.lower()
    if name in cab_owners:assert cab_owners[name]==h,'Conflicting Unity CAB identity'
    cab_owners[name]=h
  results['unity_resources']={'status':'MATCH','actual_serialized_files':len(cab_owners),'rendering':'NOT_TESTED'}
 else:results['unity_resources']={'status':'NOT_TESTED','reason':'Use -DeepUnity to parse every resource; byte and dependency checks always run'}
 results['actual_second_pc']={'status':'NOT_TESTED'}
 return results

def smoke(root,log,tests=True):
 cfg=json.loads((root/'.private/family.json').read_text());state=json.loads((root/'.private/provision-state.json').read_text());context=ssl.create_default_context(cafile=str(root/state['tls_prefix']/'ca.pem'))
 from urllib.request import urlopen
 process=subprocess.Popen([sys.executable,'-m','dittobuffer.runtime.http','--config','.private/family.json'],cwd=root,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),env=python_env(root))
 try:
  health=None
  for _ in range(100):
   if process.poll() is not None:raise ValueError('Restored server failed to start; see restore log')
   try:
    with urlopen(f"https://{cfg['host']}:{cfg['port']}/health",context=context,timeout=.5) as response:health=json.load(response)
    break
   except OSError:time.sleep(.2)
  assert health and health['profile']=='private-transfer-family';result={'status':'MATCH','tls_verified':True,'health':health}
 finally:process.terminate();process.wait(timeout=15)
 if tests:
  if (root/'scripts/test-server.py').is_file():run([sys.executable,'scripts/test-server.py'],root,log)
  else:run([sys.executable,'-m','pytest','-q'],root,log)
  node=root/'.tools/node/node.exe'
  if not node.is_file():node=shutil.which('node')
  if not node:raise ValueError('Node.js is required for the four client regression suites')
  for script in ['ui','raids','family','world']:run([node,'scripts/test-client-'+script+'.js'],root,log)
  result['gameplay_regressions']='PASS'
 else:result['gameplay_regressions']='NOT_TESTED'
 return result

def main():
 ap=argparse.ArgumentParser();ap.add_argument('action',choices=['extract','provision','native','android','verify','smoke','credentials']);ap.add_argument('--destination',required=True);ap.add_argument('--host',default='127.0.0.1');ap.add_argument('--port',type=int,default=8445);ap.add_argument('--rotate-tls',action='store_true');ap.add_argument('--deep',action='store_true');ap.add_argument('--skip-tests',action='store_true');ap.add_argument('--allow-overlay',action='store_true');a=ap.parse_args();root=Path(a.destination).resolve();m=transfer_manifest() if a.action in ('extract','native','verify') else {};report=root/'.transfer/restoration-report.json';report.parent.mkdir(parents=True,exist_ok=True) if a.action!='extract' else None
 logpath=root/'logs/transfer-restore.log';results=json.loads(report.read_text()) if report.exists() else {}
 try:
  if a.action=='extract':extract(root,m,a.allow_overlay);return
  logpath.parent.mkdir(exist_ok=True)
  with logpath.open('a') as log:
   if a.action=='provision':value=configure(root,a.host,a.port,a.rotate_tls)
   elif a.action=='native':value=native(root,log)
   elif a.action=='android':value=android(root,log)
   elif a.action=='verify':value=verify(root,m,a.deep)
   elif a.action=='smoke':value=smoke(root,log,not a.skip_tests)
   else:
    print(protect((root/'.private/bootstrap-passwords.dpapi').read_bytes(),True).decode());return
  results=json.loads(report.read_text()) if report.exists() else {};results[a.action]=value;report.write_text(json.dumps(results,indent=2)+'\n');print(a.action,'completed; report:',report)
  if a.action=='verify' and value['payloads']['status']!='MATCH':raise ValueError('Payload mismatch; inspect report')
 except Exception as e:
  results=json.loads(report.read_text()) if report.exists() else {};results[a.action]={'status':'BLOCKED','reason':str(e)};report.parent.mkdir(parents=True,exist_ok=True);report.write_text(json.dumps(results,indent=2)+'\n');raise
if __name__=='__main__':main()
