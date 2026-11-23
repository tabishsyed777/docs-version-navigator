"""Measure executable Python lines in navigator.py during this unittest run."""
import json
from pathlib import Path
import sys
import trace
import unittest

root=Path(__file__).resolve().parent
target=str(root/'navigator.py')
tracer=trace.Trace(count=True,trace=False,ignoredirs=[sys.base_prefix])
def tests():
    return unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover(str(root/'tests')))
result=tracer.runfunc(tests)
possible={line for line in trace._find_executable_linenos(target) if line > 0}
executed={line for (name,line),count in tracer.results().counts.items() if name==target and count}
covered=possible & executed
report=dict(source='navigator.py',executable_lines=len(possible),covered_lines=len(covered),
            line_coverage_pct=round(100*len(covered)/len(possible),2),missed=sorted(possible-covered),
            tests=result.testsRun,failures=len(result.failures),errors=len(result.errors))
(root/'qa'/'coverage.json').write_text(json.dumps(report,indent=2)+'\n')
sys.exit(not result.wasSuccessful())
