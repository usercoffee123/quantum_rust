#!/usr/bin/env python3
"""Select installed Instruments L1D counters, preserving the vendor template."""
import argparse
import json
from pathlib import Path
import plistlib


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    source = Path('/Applications/Xcode.app/Contents/Applications/Instruments.app/Contents/Resources/templates/CPU Counters.tracetemplate')
    template = plistlib.loads(source.read_bytes())
    changed = False
    for i, obj in enumerate(template['$objects']):
        if not isinstance(obj, bytes):
            continue
        try:
            config = json.loads(obj)
        except (ValueError, UnicodeError):
            continue
        if 'selectedCountingMode' in config:
            config['selectedCountingMode'] = dict(analysisMode='metrics', countingMode='l1d_metrics')
            config['selectedCountingModeDisplayName'] = 'L1D Cache Metrics'
            template['$objects'][i] = json.dumps(config).encode()
            changed = True
    if not changed:
        raise RuntimeError('Installed template has no recognized counting-mode configuration')
    args.output.write_bytes(plistlib.dumps(template, fmt=plistlib.FMT_BINARY))


if __name__ == '__main__':
    main()
