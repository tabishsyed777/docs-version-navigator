"""Run isolated, hand-selected semantic mutants against the actual test suite."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parent
MUTATIONS=[
 ('ignore-version-conflict','if value not in versions:', 'if False:'),
 ('remove-version-filter','WHERE version=? ORDER BY id','WHERE version=? OR 1 ORDER BY id'),
 ('invert-score-order','(-h[0], h[1].id)','(h[0], h[1].id)'),
 ('drop-stale-check','if fingerprint(Path(rows[0][2])) != rows[0][3]:','if False:'),
 ('bypass-record-check','if (records or expected) and expected != (record_digest(records),):','if False:'),
 ('ignore-conflicts','if len({p.source for p in same}) > 1 or len({p.start for p in same}) != len(same):','if False:'),
 ('disable-linked-evidence','if chosen.see:', 'if False:'),
 ('disable-secret-screen','if SENSITIVE.search(" ".join((title, body, aliases, doc["source"]))):','if False:'),
 ('remove-title-boost','score += 1.0','score += 0.0'),
 ('drop-last-character','text[start:pos]))','text[start:pos-1]))'),
 ('wrong-metric','0.3*cosine','0.0*cosine'),
 ('permit-negative-k','if k < 0:', 'if k < -1:')]
def run():
    src=(ROOT/'navigator.py').read_text()
    rows=[]
    for name,old,new in [('control','',''),*MUTATIONS]:
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            for folder in ['demo','tests']:
                shutil.copytree(ROOT/folder,root/folder,ignore=shutil.ignore_patterns('__pycache__'))
            if old and src.count(old)!=1: raise ValueError(name)
            (root/'navigator.py').write_text(src.replace(old,new,1) if old else src)
            p=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests'],cwd=root,capture_output=True,text=True,timeout=40)
            failures=re.findall(r'^(?:FAIL|ERROR): ([^\n]+)',p.stderr,re.M)
            valid=bool(re.search(r'Ran \d+ tests?',p.stderr))
            result='invalid' if not valid else ('killed' if failures else 'survived')
            if name=='control' and p.returncode: raise RuntimeError(p.stderr)
            rows.append(dict(name=name,old=old,new=new,exit=p.returncode,status='clean' if not old else result,detected_by=failures))
    out={'source_sha256':hashlib.sha256(src.encode()).hexdigest(),'control':rows[0],'mutants':rows[1:]}
    (ROOT/'qa/extended/mutations.json').write_text(json.dumps(out,indent=2))
    return any(x['status']!='killed' for x in rows[1:])
if __name__=='__main__': sys.exit(run())
