import json
import subprocess
import sys
from test_pipeline import Fixture, ROOT

class VersionScope(Fixture):
    def test_cli_scope(self):
        self.build()
        def cli(q,*versions):
            args=[a for v in versions for a in ('--version',v)]
            return subprocess.run([sys.executable,str(ROOT/'navigator.py'),'ask',q,*args,'--db',str(self.db)],capture_output=True,text=True)
        self.assertEqual(cli('timeout').returncode,2)
        self.assertEqual(json.loads(cli('timeout','1.5').stdout)['citations'],[])
        for q in ['timeout for version 2.0','v2.0 timeout','timeout in 2.0','timeout in release 2.0']:
            r=cli(q,'1.0')
            self.assertEqual(r.returncode,2)
            self.assertIn('version conflict',r.stderr)
        self.assertIn('10 seconds',json.loads(cli('timeout in v1.0','1.0').stdout)['answer'])

    def test_same_symbol_and_near_negatives(self):
        for v,num in [('1.0','12'),('2.0','24')]:
            f=self.root/(v+'.json')
            data=json.loads(f.read_text())
            data['sections'].append(dict(title='flush',aliases='buffer flush records',text=f'flush() commits {num} records.'))
            f.write_text(json.dumps(data))
        self.build()
        for v,num,other in [('1.0','12','24'),('2.0','24','12')]:
            result=self.ask('flush()',v)
            self.assertIn(num,result['answer'])
            self.assertNotIn(other,result['answer'])
            self.assertEqual({c['version'] for c in result['citations']},{v})
            for q in ['flush() every midnight','flush() encryption','flush() commits '+other+' records']:
                self.assertEqual(self.ask(q,v)['citations'],[])
