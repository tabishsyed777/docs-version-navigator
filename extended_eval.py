"""Replay frozen blind, real-reference, and >=2,000-passage CLI benchmarks."""
import hashlib
import json
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from evaluate import ROOT, catalog, invoke, measure, read, save

QA=ROOT/'qa/extended'
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def benchmark(key, corpus, db, gold):
    invoke('sync',corpus,'--db',db)
    labels=catalog(corpus)
    metrics,rows,latency=measure(gold,db,labels,record_cli_errors=True)
    critical=[]
    for idx,row in enumerate(rows):
        allowed=set(row['versions'])
        if any(c['version'] not in allowed or labels[c['id']][1].version!=c['version'] for c in row['citations']):
            critical.append(idx)
        if any(ref.split('/',1)[0] not in allowed for ref in row['retrieved']):
            critical.append(idx)
        for c in row['citations']:
            if c['text']!=labels[c['id']][1].text:
                critical.append(idx)
    save(QA/(key+'-cases.json'),rows)
    return dict(passages=len(labels),cases=len(rows),correct=sum(bool(r['correct']) for r in rows),
                answerable=sum(bool(r['relevant']) for r in rows),abstained=sum(r['status']!='answered' for r in rows),
                metrics=metrics,cli_ms=latency,wrong_version_cases=sorted(set(critical)))

def run():
    for entry in (QA/'freeze.sha256').read_text().splitlines():
        h,p=entry.split(maxsplit=1)
        if digest(ROOT/p)!=h: raise ValueError('freeze mismatch: '+p)
    gates=read(QA/'gates.json')
    results={}
    with TemporaryDirectory() as td:
        t=Path(td)
        for key,folder in [('blind','demo'),('manual','manual')]:
            results[key]=benchmark(key,ROOT/folder,t/'index.db',read(QA/(key+'.json')))
        c=t/'large'
        shutil.copytree(ROOT/'demo',c)
        for v in ('1.0','2.0'):
            for batch in range(8):
                sections=[]
                for i in range(130):
                    index=batch*130+i
                    topic=['timeout','cache','retries','redirects','pagination','upload','logging'][i%7]
                    sections.append({'title':f'Plugin {index} {topic}','aliases':f'extension {topic} configuration',
                                     'text':f'Plugin {index} stores its {topic} configuration in profile {index+1}.'})
                save(c/f'plugin-{v}-{batch}.json',dict(version=v,source=f'extensions-{batch}',date='2026-11-23',sections=sections))
        results['scale']=benchmark('scale',c,t/'index.db',read(ROOT/'qa/golden.json'))
    failures=[]
    for name,data in results.items():
        if len(data['wrong_version_cases'])>gates['wrong_version_max']: failures.append('Critical: '+name+' version isolation')
        for key,gate in [('answer_correctness','accuracy_min'),('recall_at_5','recall_at_5_min')]:
            if data['metrics'][key]<gates[gate]: failures.append('High: '+name+' '+key)
    scale=results['scale']
    drop=read(ROOT/'qa/run/results.json')['metrics']['answer_correctness']-scale['metrics']['answer_correctness']
    if drop>gates['scale_accuracy_drop_max'] or scale['passages']<gates['minimum_passages'] or scale['cli_ms']['p95_ms']>gates['scale_cli_p95_ms_max']:
        failures.append('High: scale gate')
    result=dict(source_sha256=digest(ROOT/'navigator.py'),sets=results,failures=failures,
                questions_sha256={k:digest(QA/(k+'.json')) for k in ['blind','manual']})
    save(QA/'results.json',result)
    print(json.dumps(result,indent=2))
    return bool(failures)
if __name__=='__main__':sys.exit(run())
