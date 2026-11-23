"""Run CLI evaluation in an isolated corpus. Exit 1 on a gate failure."""
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import random
import resource
import shutil
from statistics import mean, median
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
from time import perf_counter, process_time
from unittest.mock import patch

import navigator as n

ROOT = Path(__file__).resolve().parent

def read(path):
    return json.loads(path.read_text())

def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False)+'\n')

def percentile(values, fraction):
    return sorted(values)[math.ceil(len(values)*fraction)-1]

def times(values):
    return {'p50_ms':median(values),'p95_ms':percentile(values,.95)}

def invoke(*args):
    p=subprocess.run([sys.executable,'-I',str(ROOT/'navigator.py'),*map(str,args)],capture_output=True,text=True,timeout=10,check=True)
    return json.loads(p.stdout) if p.stdout else None

def catalog(corpus):
    labels={}
    for file in corpus.glob('*.json'):
        for p in n.load(file):
            labels[p.id]=(p.version+'/'+p.title,p)
    return labels

def measure(gold, db, labels, record_cli_errors=False):
    values=defaultdict(list)
    rows=[]
    for item in gold:
        start=perf_counter()
        args=[arg for v in item['versions'] for arg in ('--version',v)]
        try:
            result=invoke('ask',item['question'],*args,'--db',db)
        except subprocess.CalledProcessError as exc:
            if not record_cli_errors:
                raise
            result=dict(status='cli_error',answer=exc.stderr.strip(),citations=[],retrieved=[])
        values['cli_ms'].append((perf_counter()-start)*1000)
        ranked=[labels[i][0] for i in result['retrieved'][:5]]
        truth=set(item['relevant'])
        answer=result['answer']
        emitted=result['citations']
        cited=[labels[c['id']][0] if c['id'] in labels else None for c in emitted]
        correct=(set(cited)==truth and bool(emitted) and
                 all(e in answer for e in item['expected'])) if truth else (result['status']!='answered' and answer==n.UNKNOWN and not emitted)
        values['answer_correctness'].append(correct)
        if truth:
            hits=[int(label in truth) for label in ranked]
            values['recall_at_5'].append(len(set(ranked)&truth)/len(truth))
            values['precision_at_5'].append(sum(hits)/5)
            values['context_relevance'].append(sum(hits)/max(1,len(hits)))
            values['mrr'].append(next((1/i for i,h in enumerate(hits,1) if h),0))
            dcg=sum(h/math.log2(i+2) for i,h in enumerate(hits))
            ideal=sum(1/math.log2(i+2) for i in range(min(5,len(truth))))
            values['ndcg_at_5'].append(dcg/ideal)
            values['answer_relevance'].append(bool(cited) and set(cited)==truth)
        else:
            values['refusal_correctness'].append(correct)
        if emitted:
            grounded=True
            quotes=[]
            for c in emitted:
                entry=labels.get(c['id'])
                match=bool(entry and all(getattr(entry[1],k,None)==v for k,v in c.items()))
                grounded &= match
                values['citation_accuracy'].append(match and entry[0] in truth)
                quotes.append(f"[{c['version']} · {c['source']} · {c['title']}] {c['text']}")
            grounded &= answer=='\n'.join(quotes)
            values['faithfulness'].append(grounded)
            values['hallucination_rate'].append(not grounded)
        rows.append(dict(**item,actual=answer,status=result['status'],citations=emitted,retrieved=ranked,correct=correct))
    latency=times(values.pop('cli_ms'))
    return {k:mean(v) for k,v in values.items()},rows,latency

def run(out,baseline=None):
    out.mkdir(parents=True,exist_ok=True)
    gold=read(ROOT/'qa/golden.json')
    limits=read(ROOT/'qa/thresholds.json')
    with TemporaryDirectory() as tmp:
        corpus=Path(tmp)/'corpus'
        shutil.copytree(ROOT/'demo',corpus)
        db=Path(tmp)/'index.sqlite'
        invoke('sync',corpus,'--db',db)
        labels=catalog(corpus)
        metrics,rows,cli_latency=measure(gold,db,labels)
        durations=defaultdict(list)
        # Component profiling in one process; total CLI measurement above includes interpreter startup.
        def timed(name,fn):
            def wrapper(*args,**kwargs):
                start=perf_counter()
                try: return fn(*args,**kwargs)
                finally: durations[name].append(1000*(perf_counter()-start))
            return wrapper
        with patch.object(n,'read_versions',timed('snapshot_read_verify',n.read_versions)), patch.object(n,'rank',timed('rank',n.rank)):
            for item in gold:
                start=perf_counter()
                a,b=len(durations['rank']),len(durations['snapshot_read_verify'])
                n.answer(item['question'],item['versions'],db)
                total=1000*(perf_counter()-start)
                durations['answer_assembly_and_routing'].append(max(0,total-sum(durations['rank'][a:])-sum(durations['snapshot_read_verify'][b:])))
                durations['total_in_process'].append(total)
        # Add nine distractor manuals per release, matching the original section count.
        for i in range(9):
            for v in ['1.0','2.0']:
                doc=read(corpus/(v+'.json'))
                doc['source']=f'extension-{i}'
                for sec in doc['sections']:
                    sec['title']=f"plugin{i} {sec['title']}"
                    sec['aliases']='plugin extension settings'
                    sec['text']=f"Extension {i} has a separate {sec['title']} configuration."
                save(corpus/(f'extension-{i}-{v}.json'),doc)
        invoke('sync',corpus,'--db',db)
        scaled,_,scale_latency=measure(gold,db,catalog(corpus))
        metrics['scale_accuracy_drop']=max(0,metrics['answer_correctness']-scaled['answer_correctness'])
        metrics['scale_p95_ms']=scale_latency['p95_ms']
        barrier=threading.Barrier(50)
        def worker(_):
            barrier.wait(timeout=15)
            start=perf_counter()
            result=n.answer('request timeout',['1.0'],db)
            return (perf_counter()-start)*1000,'10 seconds' in result['answer']
        cpu=process_time()
        wall=perf_counter()
        with ThreadPoolExecutor(50) as pool:
            load=list(pool.map(worker,range(50)))
        wall=perf_counter()-wall
        load_cpu=process_time()-cpu
        metrics['load_p95_ms']=percentile([x for x,_ in load],.95)
        load_ok=all(ok for _,ok in load)
    failures=[k for k,v in limits['minimum'].items() if metrics[k]<v]
    failures += [k for k,v in limits['maximum'].items() if metrics[k]>v]
    if not load_ok: failures.append('load_answers')
    if baseline:
        prior=read(baseline)
        previous=prior['metrics']
        for key in [*limits['minimum'],'hallucination_rate','scale_accuracy_drop']:
            if (metrics[key]-previous[key])*(1 if key in limits['minimum'] else -1)<-1e-12:
                failures.append('regression:'+key)
        for key in [*limits['minimum'],'hallucination_rate']:
            delta=scaled[key]-prior['scale']['metrics'][key]
            if delta*(1 if key in limits['minimum'] else -1)<-1e-12:
                failures.append('scale-regression:'+key)
    digest=hashlib.sha256((ROOT/'navigator.py').read_bytes()).hexdigest()
    report=dict(source_sha256=digest,python=sys.version,metrics=metrics,thresholds=limits,failures=failures,
                correct=sum(r['correct'] for r in rows),cases=len(rows),cli_latency=cli_latency,
                stages={k:times(v) for k,v in durations.items()},scale={'chunks':10*len(labels),'metrics':scaled,'cli':scale_latency},
                load={'workers':50,'passed':sum(ok for _,ok in load),'wall_seconds':wall,'cpu_seconds':load_cpu,
                      'peak_process_rss_kib_linux':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss},
                model_tokens_in=0,model_tokens_out=0,api_cost_per_query=0)
    save(out/'results.json',report)
    save(out/'cases.json',rows)
    sample=random.Random(23).sample(rows,10)
    save(out/'human-review.json',[dict(question=r['question'],versions=r['versions'],expected=r['expected'],
         actual=r['actual'],passages=r['citations'],reviewer=None,decision=None,notes=None) for r in sample])
    print(json.dumps(report,indent=2))
    return bool(failures)

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,default=Path('run'))
    parser.add_argument('--baseline',type=Path)
    args=parser.parse_args()
    sys.exit(run(args.out,args.baseline))
