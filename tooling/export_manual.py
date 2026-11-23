"""Export the public Version class API reference from an installed packaging release."""
import hashlib
import inspect
import json
from pathlib import Path
import packaging
from packaging.version import Version

root = Path(__file__).resolve().parents[1]
release = packaging.__version__
sections = []
for name, value in inspect.getmembers(Version):
    if name.startswith('_'):
        continue
    doc = inspect.getdoc(value)
    if doc:
        sections.append(dict(title=name, aliases=name.replace('_',' '), text=doc))
file = root / 'manual' / (release+'.json')
file.write_text(json.dumps(dict(source='packaging-version-reference', version=release,
    date='2026-11-23', sections=sections), indent=2)+'\n')
print(release, len(sections), hashlib.sha256(file.read_bytes()).hexdigest())
