"""Guard real index blobs and smudged LFS bytes, without changing LFS setup."""
import hashlib
import importlib.util
import subprocess
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('large_file_guard',ROOT/'scripts/check-large-files.py')
guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)

@pytest.fixture
def repo(tmp_path,monkeypatch):
 subprocess.run(['git','init','-q',str(tmp_path)],check=True,capture_output=True)
 monkeypatch.chdir(tmp_path)
 return tmp_path

def git(*args,data=None):
 return subprocess.run(['git',*args],input=data,capture_output=True,check=True).stdout

def index(path,content):
 oid=git('hash-object','-w','--stdin',data=content).decode().strip()
 git('update-index','--add','--cacheinfo','100644,'+oid+','+path)

@pytest.mark.parametrize('case',['valid','bad_hash','bad_length','malformed','no_filter','ordinary_blob','staged_big_working_small','missing_big_working_file'])
def test_large_file_policy(repo,case):
 body=b'original-test-bytes-'*60000
 path=repo/'payload.bin';path.write_bytes(body)
 digest=hashlib.sha256(body).hexdigest()
 if case=='bad_hash':digest='0'*64
 length=len(body)+(1 if case=='bad_length' else 0)
 pointer=('version https://git-lfs.github.com/spec/v1\noid sha256:'+digest+'\nsize '+str(length)+'\n').encode()
 if case=='malformed':pointer=b'version not-a-git-lfs-pointer\n'
 if case in ('ordinary_blob','staged_big_working_small','missing_big_working_file'):pointer=body
 index('payload.bin',pointer)
 if case!='no_filter':(repo/'.gitattributes').write_text('payload.bin filter=lfs\n')
 if case=='staged_big_working_small':path.write_bytes(b'small')
 if case=='missing_big_working_file':path.unlink()
 assert guard.main(['--limit-mib','1'])==(0 if case=='valid' else 1)

def test_empty_index(repo):
 assert guard.main(['--limit-mib','1'])==0
