import os,hashlib,importlib.util,json,ssl,threading,socket,tempfile,unittest,zipfile
from pathlib import Path
from dittobuffer.core.family_network import peer_allowed
from dittobuffer.core.client_resources import ClientResources
ROOT=Path(__file__).resolve().parents[1]

class StandaloneTests(unittest.TestCase):
    def test_runtime_resources_are_opt_in_exact_bytes_and_fail_closed(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'builds') as folder:
            root=Path(folder).resolve();bundle=root/'sky.bundle';bundle.write_bytes(b'UnityFS-exact-owned-fixture')
            registry=root/'registry.json'
            row={'file':'sky.bundle','bytes':bundle.stat().st_size,'sha256':hashlib.sha256(bundle.read_bytes()).hexdigest()}
            registry.write_text(json.dumps({'files':{'sky.bundle':row}}))
            self.assertIsNone(ClientResources(root,{}).read('sky.bundle'))
            store=ClientResources(root,{'enabled':True,'registry':'registry.json'})
            self.assertEqual(store.read('sky.bundle'),(bundle.read_bytes(),'application/octet-stream'))
            for name in ['../sky.bundle','missing.bundle','file:///sky.bundle']:
                self.assertIsNone(store.read(name))
            bundle.write_bytes(b'altered')
            with self.assertRaisesRegex(ValueError,'changed'):store.read('sky.bundle')
            with self.assertRaisesRegex(ValueError,'Invalid runtime'):ClientResources(root,{'enabled':True,'registry':'registry.json'})
            row['file']='../../outside.bundle';registry.write_text(json.dumps({'files':{'sky.bundle':row}}))
            with self.assertRaisesRegex(ValueError,'Invalid runtime'):ClientResources(root,{'enabled':True,'registry':'registry.json'})
            registry.write_text(json.dumps({'files':{'../sky.bundle':row}}))
            with self.assertRaisesRegex(ValueError,'resource key'):ClientResources(root,{'enabled':True,'registry':'registry.json'})

    def test_tls_peer_allowlist_is_optional_but_fails_closed_when_present(self):
        cfg={'network':{'mode':'tls','allowed_networks':['192.168.0.11/32','192.168.0.12/32']}}
        self.assertTrue(peer_allowed(cfg,'192.168.0.11'))
        self.assertTrue(peer_allowed(cfg,'192.168.0.12'))
        self.assertFalse(peer_allowed(cfg,'192.168.0.13'))
        self.assertFalse(peer_allowed(cfg,'127.0.0.1'))
        self.assertFalse(peer_allowed(cfg,'not-an-address'))
        self.assertTrue(peer_allowed({'network':{'mode':'tls'}},'192.168.0.13'))
        self.assertFalse(peer_allowed({'network':{'mode':'tls','allowed_networks':[]}},'192.168.0.11'))

    def test_private_certificate_passes_strict_tls_and_rejects_wrong_hostname(self):
        spec=importlib.util.spec_from_file_location('family_tls',ROOT/'scripts/create-family-tls.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix='m151-tls-test-',dir=ROOT/'.private') as folder:
            directory=Path(folder)/'issued';module.create('192.168.0.10',directory)
            server=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);server.load_cert_chain(directory/'server.pem',directory/'server-key.pem')
            client=ssl.create_default_context(cafile=str(directory/'ca.pem'))
            for name,expected in [('192.168.0.10',True),('wrong.example',False)]:
                with socket.socket() as listener:
                    listener.bind(('127.0.0.1',0));listener.listen(1)
                    def accept():
                        s,_=listener.accept()
                        try:
                            with server.wrap_socket(s,server_side=True) as secured:secured.sendall(b'verified')
                        except ssl.SSLError:s.close()
                    worker=threading.Thread(target=accept);worker.start()
                    with socket.create_connection(listener.getsockname(),timeout=3) as raw:
                        if expected:
                            with client.wrap_socket(raw,server_hostname=name) as secured:self.assertEqual(secured.recv(20),b'verified')
                        else:
                            with self.assertRaises(ssl.SSLCertVerificationError):client.wrap_socket(raw,server_hostname=name)
                    worker.join(3);self.assertFalse(worker.is_alive())

    def test_full_apk_preserves_original_game_dex_and_native_content(self):
        path=ROOT/os.environ.get('DITTOBUFFER_TEST_APK','builds/m151-standalone/dittobuffer-family.apk')
        base=ROOT/os.environ.get('DITTOBUFFER_TEST_BASE','builds/client-base')
        split=ROOT/os.environ.get('DITTOBUFFER_TEST_SPLIT','builds/client-split')
        if not path.exists():self.skipTest('Standalone build has not been produced locally')
        with zipfile.ZipFile(path) as apk:
            self.assertIn('classes4.dex',apk.namelist())
            for i in ('','2','3'):
                name='classes'+i+'.dex';self.assertEqual(apk.read(name),(base/name).read_bytes())
            for lib in (split/'lib/arm64-v8a').glob('*.so'):
                if lib.name.startswith('libdittobuffer_'):continue
                self.assertEqual(hashlib.sha256(apk.read('lib/arm64-v8a/'+lib.name)).digest(),hashlib.sha256(lib.read_bytes()).digest(),lib.name)
            cfg=json.loads(apk.read('assets/dittobuffer/libdittobuffer_runtime.config.so'))
            self.assertEqual(cfg['interaction']['type'],'script')
            self.assertNotIn('address',cfg['interaction']);self.assertNotIn('port',cfg['interaction'])
            self.assertNotIn('lib/arm64-v8a/libdittobuffer_agent.so',apk.namelist())

if __name__=='__main__':unittest.main()
