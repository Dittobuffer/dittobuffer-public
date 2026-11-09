"""Regression coverage for large Windows model registry publication."""
import hashlib,importlib.util,json,subprocess,sys,tempfile,unittest
from pathlib import Path
from tests.test_asset_catalog import compact_catalog
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('publish_models',ROOT/'scripts/publish-reviewed-pokemon-assets.py');publish=importlib.util.module_from_spec(spec);spec.loader.exec_module(publish)

class AssetPublicationTests(unittest.TestCase):
    def test_path_aliases_do_not_reprioritize_or_duplicate_baseline(self):
        before='builds/fixture/original.json';new='builds/fixture/new.json'
        alias=str(Path(before))
        candidate=dict(source_imports=[new,alias,before],variants=[dict(address='pm1',source_import=alias),dict(address='pm2',source_import=new)])
        preferred,baseline=publish.reviewed_imports(candidate,{'pm1','pm2'},[before,alias])
        self.assertEqual(preferred,[new]);self.assertEqual(baseline,[before])

    def test_large_import_list_combines_exact_dependency_and_content(self):
        with tempfile.TemporaryDirectory(prefix='asset-publication-',dir=ROOT/'builds') as tmp:
            directory=Path(tmp);data=b'fixture';sha=hashlib.sha256(data).hexdigest();bundle=directory/'fixture.bundle';bundle.write_bytes(data)
            document=compact_catalog();document['m_InternalIds'][0]='http://127.0.0.1:8080/assets/pokemon/bundles/'+sha+'.bundle'
            catalog=directory/'source.catalog.json';catalog.write_text(json.dumps(document));catalog_sha=hashlib.sha256(catalog.read_bytes()).hexdigest()
            manifest=dict(provenance='isolated publication regression',source_catalog=catalog.relative_to(ROOT).as_posix(),source_catalog_sha256=catalog_sha,local_catalog=catalog.relative_to(ROOT).as_posix(),local_catalog_sha256=catalog_sha,
                content={sha:dict(bytes=len(data),file=bundle.relative_to(ROOT).as_posix())},bundles=[dict(index=0,status='LOCATED',sha256=sha,version='1'*32,bundle_name='bundle-A',serialized_files=['CAB-fixture'])],
                variants=[dict(address='pm9999',species=9999,bundle_indices=[0],missing_dependencies=[],model_paths=['Packages/pm9999.prefab'])])
            source=directory/'source.json';source.write_text(json.dumps(manifest));names=[source.relative_to(ROOT).as_posix()]*600
            self.assertGreater(sum(len(n)+12 for n in names),32767)
            plan=directory/'imports.json';plan.write_text(json.dumps(names));output=directory/'combined.json'
            run=subprocess.run([sys.executable,str(ROOT/'scripts/combine-pokemon-assets.py'),'--import-list',str(plan.relative_to(ROOT)),'--output',str(output.relative_to(ROOT))],cwd=ROOT,capture_output=True,text=True,timeout=30)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)
            combined=json.loads(output.read_text());self.assertEqual(combined['content'],manifest['content']);self.assertEqual(combined['source_imports'],names)
            self.assertEqual(combined['variants'][0]['bundle_indices'],[0]);self.assertEqual(combined['counts']['dependency_complete_addresses'],1)

if __name__=='__main__':unittest.main()
