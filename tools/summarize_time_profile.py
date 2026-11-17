#!/usr/bin/env python3
"""Summarize xctrace time-profile XML; resolve shared ID references."""
import collections
import gzip
import json
from pathlib import Path
import xml.etree.ElementTree as ET

base = Path('benchmark-results/rust-profile-25q')
source = base / 'time-samples.xml'
stream = source.open('rb') if source.exists() else gzip.open(str(source)+'.gz', 'rb')
with stream:
    root = ET.parse(stream).getroot()
ids = {e.attrib['id']: e for e in root.iter() if 'id' in e.attrib}
def resolve(e):
    return ids[e.attrib['ref']] if 'ref' in e.attrib else e
leaf = collections.Counter()
regions = collections.Counter()
cores = collections.Counter()
total = 0
for row in root.iter('row'):
    if row.find('tagged-backtrace') is None:
        continue
    weight = int(resolve(row.find('weight')).text)
    tagged = resolve(row.find('tagged-backtrace'))
    frames = [resolve(e) for e in resolve(tagged.find('backtrace')).findall('frame')]
    if not frames:
        continue
    f = frames[0]
    src = f.find('source')
    loc = ''
    if src is not None:
        path = resolve(src.find('path')).text
        loc = f"{path}:{src.attrib.get('line')}"
    key = f.attrib.get('name', '?') + ' @ ' + loc
    leaf[key] += weight
    names = ' '.join(x.attrib.get('name','') for x in frames)
    if 'pairs' in names:
        region = 'Pair traversal subtree (CX dominated)'
    elif 'rayon' in names:
        region = 'Rayon outside pair traversal'
    elif 'serde' in names:
        region = 'JSON parsing/serialization'
    else:
        region = 'Other (startup, allocation, runtime)'
    regions[region] += weight
    cores[resolve(row.find('core')).attrib.get('fmt','?')] += weight
    total += weight
summary = dict(sample_weight_seconds=total/1e9,
               regions=[dict(region=k,percent=100*v/total,seconds=v/1e9) for k,v in regions.most_common()],
               cores=[dict(core=k,percent=100*v/total) for k,v in cores.most_common()],
               leaves=[dict(symbol=k,percent=100*v/total,seconds=v/1e9) for k,v in leaf.most_common(25)])
(base / 'time-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary,indent=2))
