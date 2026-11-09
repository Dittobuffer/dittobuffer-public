"""Issue a short-lived private test identity with an explicit IP SAN; no OS trust changes."""
import argparse,datetime,ipaddress,json
from pathlib import Path
from cryptography import x509
from cryptography.x509.oid import NameOID,ExtendedKeyUsageOID
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
ROOT=Path(__file__).resolve().parents[1]
def create(host,directory):
 ip=ipaddress.ip_address(host)
 if not ip.is_private:raise ValueError('Private test interface required')
 directory=directory.resolve()
 if not directory.is_relative_to(ROOT/'.private') or directory.exists():raise ValueError('Fresh project-private directory required')
 directory.mkdir(parents=True)
 now=datetime.datetime.now(datetime.timezone.utc);key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
 subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Dittobuffer private family CA')])
 ca=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=5)).not_valid_after(now+datetime.timedelta(days=30)).add_extension(x509.BasicConstraints(ca=True,path_length=0),True).add_extension(x509.KeyUsage(False,False,False,False,False,True,True,False,False),True).add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),False).add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),False).sign(key,hashes.SHA256()))
 leafkey=rsa.generate_private_key(public_exponent=65537,key_size=2048)
 cert=(x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Dittobuffer family server')])).issuer_name(subject).public_key(leafkey.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=5)).not_valid_after(now+datetime.timedelta(days=14)).add_extension(x509.BasicConstraints(ca=False,path_length=None),True).add_extension(x509.KeyUsage(True,False,True,False,False,False,False,False,False),True).add_extension(x509.SubjectAlternativeName([x509.IPAddress(ip)]),False).add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),False).add_extension(x509.SubjectKeyIdentifier.from_public_key(leafkey.public_key()),False).add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),False).sign(key,hashes.SHA256()))
 for filename,value in [('ca.pem',ca.public_bytes(serialization.Encoding.PEM)),('server.pem',cert.public_bytes(serialization.Encoding.PEM)),('server-key.pem',leafkey.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))]:(directory/filename).write_bytes(value)
 return {'host':host,'ca_sha256':ca.fingerprint(hashes.SHA256()).hex(),'server_sha256':cert.fingerprint(hashes.SHA256()).hex(),'valid_days':14,'system_trust_modified':False}
if __name__=='__main__':
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--host',required=True);ap.add_argument('--directory',required=True);args=ap.parse_args()
 print(json.dumps(create(args.host,ROOT/args.directory),indent=2))
