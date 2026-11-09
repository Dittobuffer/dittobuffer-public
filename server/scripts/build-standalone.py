"""Build a separate autonomous ARM64 clone; never modifies working dev APKs."""
import argparse,hashlib,json,os,shutil,subprocess,sys,xml.etree.ElementTree as ET
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT))
A='{http://schemas.android.com/apk/res/android}'
ET.register_namespace('android',A[1:-1])
GADGET_SHA='de0feab1ae960aa6cb24440bcb02dc3149ef84c4672a4f19ff4405aba63eaabc'

def run(args):subprocess.run(list(map(str,args)),cwd=ROOT,check=True)
def tool(*candidates):
 for candidate in candidates:
  if candidate.exists():return candidate
 raise SystemExit('Missing tool: '+' | '.join(str(candidate) for candidate in candidates))
TOOLKIT=ROOT/'.tools'
GADGET=tool(TOOLKIT/'frida-gadget-17.24.3-android-arm64.so',ROOT/'tools/frida-gadget/arm64-v8a/libgadget.so')
APKTOOL=tool(TOOLKIT/'apktool.jar',ROOT/'tools/apktool/apktool.jar')
FRAMEWORK=TOOLKIT/'apktool-framework' if (TOOLKIT/'apktool-framework').is_dir() else ROOT/'tools/apktool'
BUILD_TOOLS=TOOLKIT/'android-16' if (TOOLKIT/'android-16').is_dir() else ROOT/'tools/android-build-tools'
ANDROID_JAR=tool(TOOLKIT/'android-sdk-36/android.jar',ROOT/'tools/android-sdk-36/android.jar')
D8=tool(BUILD_TOOLS/'lib/d8.jar',TOOLKIT/'android-16/lib/d8.jar')
KEYSTORE=tool(TOOLKIT/'local-test.jks',ROOT/'src/dittobuffer/patcher/keys/dittobuffer-patch.jks')
def build_agent(profile):
 # Keep the existing Max/PvP adapters alongside explicitly enabled Route UI
 # compatibility. Assembly can be checked without touching any APK or device.
 scripts=['local-auth.js','local-ui.js','local-social.js','local-maps.js','local-world.js','local-gameplay.js','local-raids.js','local-raid-presentation.js','local-pvp.js','local-rocket.js','local-max.js','local-max-ball.js','local-backgrounds.js','local-assets.js','standalone-render-diagnostics.js']
 integration='\n'.join((ROOT/'client'/p).read_text(encoding='utf8') for p in scripts)
 for flag,name in (
  ('map_diagnostics','inspect-map-geometry.js'),
  ('pvp_ui_diagnostics','inspect-pvp-ui.js'),
  ('routes_diagnostics','inspect-routes.js'),
  ('native_research_diagnostics','inspect-special-research.js'),
  ('routes_foreground_creation','local-routes.js'),
  ('routes_saved_postcard_compatibility','local-postcards.js'),
 ):
  if profile.get(flag) is True:integration+='\n'+(ROOT/'client'/name).read_text(encoding='utf8')
 if profile.get('fixture_origin'):integration+='\n'+(ROOT/'client/world-fixture-location.js').read_text(encoding='utf8')
 bootstrap=(ROOT/'client/standalone-bootstrap.js').read_text(encoding='utf8')
 marker='/* DITTOBUFFER_INTEGRATION */'
 if bootstrap.count(marker)!=1:raise ValueError('Expected one Dittobuffer integration marker')
 return bootstrap.replace(marker,integration)

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--profile',required=True);ap.add_argument('--output',default='builds/m151-standalone');ap.add_argument('--key-alias',default='dittobuffer',help='Existing local development signing alias; does not alter the keystore');ap.add_argument('--base-tree',default='builds/client-base',help='Project-local independently supplied decoded base; read only');ap.add_argument('--split-tree',default='builds/client-split',help='Project-local independently supplied ARM64 split; read only');args=ap.parse_args()
 source=(ROOT/args.profile).resolve()
 if not source.is_relative_to(ROOT):raise ValueError('Project-local public build profile required')
 profile=json.loads(source.read_text(encoding='utf8'))
 if not profile['origin'].startswith('https://'):raise ValueError('Standalone remote profile requires HTTPS')
 runtime_registry=profile.pop('runtime_registry',None)
 catalog=None
 if runtime_registry:
  # The main locator is loaded from the APK. Validate every selected resource
  # before copying that locator into this separate build's disposable base.
  from dittobuffer.core.client_resources import ClientResources
  resources=ClientResources(ROOT,dict(enabled=True,registry=runtime_registry))
  catalog=resources.read('catalog_926.json')
  if catalog is None:raise ValueError('Selected runtime registry has no catalog')
 gadget=GADGET
 digest=hashlib.sha256(gadget.read_bytes()).hexdigest()
 if digest!=GADGET_SHA:raise ValueError('Pinned embedded runtime hash mismatch')
 output=(ROOT/args.output).resolve()
 if not any(output.is_relative_to(ROOT/name) and output != ROOT/name for name in ('builds','.staging')):raise ValueError('Build output must stay within project builds or .staging')
 output.mkdir(parents=True,exist_ok=True)
 for part in ('base','split'):
  target=output/part
  if not target.exists():
   original=(ROOT/getattr(args,part+'_tree')).resolve()
   if not original.is_relative_to(ROOT) or not original.is_dir():raise ValueError('Project-local authorized decoded '+part+' tree required; pass --'+part+'-tree')
   if original==target or target.is_relative_to(original):raise ValueError('Build output cannot be nested inside decoded inputs')
   shutil.copytree(original,target)
 base=output/'base';split=output/'split'
 if catalog is not None:
  target=base/'assets/aa/catalog.json'
  if not target.is_file():raise ValueError('Expected 0.429.2 embedded catalog layout')
  target.write_bytes(catalog[0]);profile['runtime_catalog_sha256']=hashlib.sha256(catalog[0]).hexdigest()
 manifest=base/'AndroidManifest.xml';tree=ET.parse(manifest);app=tree.getroot().find('application')
 for key in ('requiredSplitTypes','splitTypes'):tree.getroot().attrib.pop(A+key,None)
 for node in list(app.findall('meta-data')):
  if node.get(A+'name','').startswith('com.android.vending.splits'):app.remove(node)
 app.set(A+'allowBackup','false');app.set(A+'debuggable','false');app.set(A+'usesCleartextTraffic','true')
 app.set(A+'label','Dittobuffer Family 0.429.2')
 # Rebrand every leftover LibreNLP label without touching the package id.
 for node in app.iter():
  label=node.get(A+'label')
  if label and 'LibreNLP' in label:node.set(A+'label','Dittobuffer Family 0.429.2')
 for activity in app.findall('activity'):
  for intent in list(activity.findall('intent-filter')):
   if any(n.get(A+'name')=='android.intent.category.LAUNCHER' for n in intent.findall('category')):activity.remove(intent)
  if activity.get(A+'name')=='org.dittobuffer.launcher.FamilyActivity':app.remove(activity)
  elif activity.get(A+'name','').endswith('UnityMainActivity'):activity.set(A+'exported','false')
 launch=ET.SubElement(app,'activity',{A+'name':'org.dittobuffer.launcher.FamilyActivity',A+'exported':'true',A+'theme':'@android:style/Theme.Material.Light.NoActionBar'})
 intent=ET.SubElement(launch,'intent-filter');ET.SubElement(intent,'action',{A+'name':'android.intent.action.MAIN'});ET.SubElement(intent,'category',{A+'name':'android.intent.category.LAUNCHER'})
 tree.write(manifest,encoding='utf-8',xml_declaration=True)
 assets=base/'assets/dittobuffer';assets.mkdir(exist_ok=True)
 ca=profile.pop('ca_file',None)
 if ca:
  certificate=(ROOT/ca).resolve()
  if not certificate.is_relative_to(ROOT):raise ValueError('Project-local CA certificate required')
  shutil.copy2(certificate,assets/'family-ca.pem');profile['ca_asset']='dittobuffer/family-ca.pem'
 (assets/'profile.json').write_text(json.dumps(profile,indent=2)+'\n',encoding='utf8')
 java=Path(shutil.which('java')).resolve();props=subprocess.run([str(java),'-XshowSettings:properties','-version'],capture_output=True,text=True,check=True)
 import re
 java_home=Path(re.search(r'java.home = ([^\r\n]+)',props.stderr)[1].strip())
 classes=output/'classes';dex=output/'dex';classes.mkdir(exist_ok=True);dex.mkdir(exist_ok=True)
 sources=list((ROOT/'client/standalone').rglob('*.java'));android=ANDROID_JAR
 run([java_home/'bin/javac.exe','--release','8','-classpath',android,'-d',classes,*sources])
 run([java,'-cp',D8,'com.android.tools.r8.D8','--min-api','28','--lib',android,'--output',dex,*classes.rglob('*.class')])
 shutil.copy2(dex/'classes.dex',base/'classes4.dex')
 agent=build_agent(profile)
 # Unity probes the installed native directory. Text masquerading as .so there
 # triggers its loader path, so isolate the autonomous runtime in app code cache.
 for libdir in (split/'lib/arm64-v8a',base/'lib/arm64-v8a'):
  for name in ('libdittobuffer_runtime.so','libdittobuffer_runtime.config.so','libdittobuffer_agent.so'):
   (libdir/name).unlink(missing_ok=True)
 shutil.copy2(gadget,assets/'libdittobuffer_runtime.so')
 (assets/'libdittobuffer_runtime.config.so').write_text(json.dumps({'interaction':{'type':'script','path':'agent.js','on_change':'ignore'},'runtime':'qjs'}),encoding='utf8')
 (assets/'agent.js').write_text(agent,encoding='utf8')
 # The supplied ABI split contains only native libraries. Merge those exact
 # libraries into a full APK so family installation needs no split installer.
 shutil.copytree(split/'lib',base/'lib',dirs_exist_ok=True)
 results={}
 for part in ('base',):
  unsigned=output/(part+'-unsigned.apk');apk=output/'dittobuffer-family.apk'
  run([java,'-jar',APKTOOL,'b','-p',FRAMEWORK,'-o',unsigned,output/part])
  run([BUILD_TOOLS/'zipalign.exe','-f','-P','16','4',unsigned,apk])
  run([BUILD_TOOLS/'apksigner.bat','sign','--ks',KEYSTORE,'--ks-key-alias',args.key_alias,'--ks-pass','pass:android',apk])
  run([BUILD_TOOLS/'apksigner.bat','verify',apk])
  results[part]={'file':apk.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(apk.read_bytes()).hexdigest(),'bytes':apk.stat().st_size}
 (output/'build.json').write_text(json.dumps(dict(package='org.librenlp.pokemongo.dev',profile=profile,embedded_runtime_sha256=digest,external_controller=False,apks=results),indent=2)+'\n')
 print('Standalone clone built:',json.dumps(results))
if __name__=='__main__':main()
