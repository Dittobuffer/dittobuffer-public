"""Real loopback TLS listener and protobuf authentication; no Android claim."""
import datetime,hashlib,ipaddress,json,os,socket,ssl,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from dittobuffer.gameplay.game import Game,ROOT,p,wire

class FamilyHttpTests(unittest.TestCase):
    def test_tls_account_auth_rpc_logout_and_no_raw_credentials_logged(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'builds') as temporary:
            folder=Path(temporary);key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
            name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')]);now=datetime.datetime.now(datetime.timezone.utc)
            cert=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=1)).not_valid_after(now+datetime.timedelta(days=1)).add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),False).sign(key,hashes.SHA256())
            (folder/'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM));(folder/'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            cfg=json.loads((ROOT/'config/development.json').read_text(encoding='utf8'));cfg.update(host='127.0.0.1',port=port,database=(folder/'http.sqlite3').relative_to(ROOT).as_posix(),authentication={'mode':'family'},network={'mode':'tls','certificate':(folder/'cert.pem').relative_to(ROOT).as_posix(),'private_key':(folder/'key.pem').relative_to(ROOT).as_posix()})
            catalog=folder/'catalog.json';catalog.write_bytes(b'{"original_test_catalog":true}')
            catalog_sha=hashlib.sha256(catalog.read_bytes()).hexdigest()
            registry=folder/'runtime.json';registry.write_text(json.dumps({'files':{'catalog_926.json':{
                'file':catalog.relative_to(ROOT).as_posix(),'bytes':catalog.stat().st_size,'sha256':catalog_sha}}}))
            cfg['client_resources']={'enabled':True,'registry':registry.relative_to(ROOT).as_posix()}
            config=folder/'config.json';config.write_text(json.dumps(cfg));game=Game(cfg);invite=game.accounts.invite()
            context=ssl.create_default_context(cafile=str(folder/'cert.pem'));base='https://127.0.0.1:'+str(port)
            log=folder/'process.log'
            with log.open('wb') as stream:
                process_options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
                proc=subprocess.Popen([sys.executable,'-m','dittobuffer.runtime.http','--config',config.relative_to(ROOT).as_posix()],cwd=ROOT,stdout=stream,stderr=stream,**process_options)
                try:
                    for _ in range(100):
                        try:
                            with urlopen(base+'/health',context=context,timeout=.3) as response:health=json.load(response)
                            break
                        except (URLError,TimeoutError):time.sleep(.1)
                    else:self.fail('TLS family listener did not become healthy')
                    self.assertEqual(health['authentication'],'family')
                    self.assertTrue(health['client_resources'])
                    self.assertEqual(health['client_catalog_sha256'],catalog_sha)
                    self.assertEqual(health['world'],bool(game.world.path))
                    with urlopen(base+'/assets/client/catalog_926.json',context=context,timeout=2) as response:
                        self.assertEqual(hashlib.sha256(response.read()).hexdigest(),catalog_sha)

                    with self.assertRaises(URLError):urlopen(base+'/health',timeout=2)
                    def post(path,value,token=None,proto=False):
                        headers={'Content-Type':'application/x-protobuf' if proto else 'application/json'}
                        if token:headers['Authorization']='Bearer '+token
                        data=value if proto else json.dumps(value).encode()
                        with urlopen(Request(base+path,data=data,headers=headers),context=context,timeout=5) as response:
                            if path.startswith('/accounts/') or path=='/get_auth_token':self.assertEqual(response.headers['Cache-Control'],'no-store')
                            return response.read()
                    password='HTTP isolated password 42'
                    post('/accounts/register',dict(username='tls-account',password=password,trainer_name='TLS Trainer',invitation=invite))
                    credential=json.loads(post('/accounts/login',dict(username='tls-account',password=password)))['token']
                    ticket=wire.AuthResponse.FromString(post('/get_auth_token',wire.AuthRequest(auth_id='forged',token=credential.encode()).SerializeToString(),proto=True)).ticket
                    req=wire.RpcRequest(rpc_id=1);req.requests.add(method=2)
                    out=wire.RpcResponse.FromString(post('/rpc',req.SerializeToString(),ticket,True));self.assertEqual(p.GetPlayerOutProto.FromString(out.payloads[0].payload).player.name,'TLS Trainer')
                    with self.assertRaises(HTTPError):post('/get_auth_token',wire.AuthRequest(auth_id='tls-account',token=b'wrong').SerializeToString(),proto=True)
                    post('/accounts/logout',{},credential)
                    with self.assertRaises(HTTPError):post('/rpc',req.SerializeToString(),ticket,True)
                    with self.assertRaises(HTTPError):post('/accounts/register',dict(username='tls-replay',password=password,trainer_name='Replay',invitation=invite))
                finally:
                    # This Popen instance owns the isolated listener. Terminate
                    # only that child; never use process names or tree-wide kills.
                    if proc.poll() is None:
                        try:proc.terminate()
                        except ProcessLookupError:pass
                    try:proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill();proc.wait(timeout=10)
            text=log.read_text(encoding='utf8')
            for secret in [password,invite,credential,ticket]:self.assertNotIn(secret,text)

if __name__=='__main__':unittest.main()
