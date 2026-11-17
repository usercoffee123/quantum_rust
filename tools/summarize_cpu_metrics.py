#!/usr/bin/env python3
"""Summarize process-only xctrace MetricTable rows, retaining ratio semantics."""
import collections
import gzip
import json
import sys
from pathlib import Path
import xml.etree.ElementTree as ET

source = Path(sys.argv[1])
ids = {}
metrics = collections.defaultdict(lambda: dict(rows=0, weighted=0., duration=0., total=0., ratio=False))
stream = gzip.open(source, 'rb') if source.suffix == '.gz' else source.open('rb')
for _, element in ET.iterparse(stream, events=['end']):
    if 'id' in element.attrib:
        ids[element.attrib['id']] = (element.text, element.attrib.get('fmt',''))
    if element.tag != 'row':
        continue
    def val(child):
        return ids[child.attrib['ref']] if 'ref' in child.attrib else (child.text, child.attrib.get('fmt',''))
    cells = list(element)
    if len(cells) < 10:
        element.clear(); continue
    if 'statevector' not in val(cells[5])[1] or int(val(cells[0])[0]) < 1_000_000_000:
        element.clear(); continue
    duration = int(val(cells[1])[0])
    name = val(cells[3])[0]
    value = float(val(cells[6])[0])
    ratio = val(cells[8])[0] == '1'
    item = metrics[name]
    item['rows'] += 1
    item['ratio'] = ratio
    item['weighted'] += value*duration
    item['duration'] += duration
    item['total'] += value
    element.clear()
result = {k: dict(rows=v['rows'], is_ratio=v['ratio'],
                 value=v['weighted']/v['duration'] if v['ratio'] else v['total'])
          for k,v in metrics.items()}
stream.close()
summary_path = source.with_suffix('') if source.suffix == '.gz' else source
summary_path.with_suffix('.summary.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
