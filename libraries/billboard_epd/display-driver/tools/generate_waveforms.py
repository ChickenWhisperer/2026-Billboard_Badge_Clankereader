"""Regenerate the C and MicroPython tables from the reviewed JSON source."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def tables():
    source = json.loads((ROOT / "display-driver/waveforms.json").read_text())
    result = {name: bytes.fromhex(source[name]) for name in ("BOLD", "RED")}
    gray = bytearray(result["RED"])
    gray[160:240] = gray[80:160]
    for group in range(1, 5):
        gray[160 + group * 8] = 0
    result["GRAY"] = bytes(gray)
    for name, table in result.items():
        assert len(table) == 400, name
        assert all(table[offset] == 0 for offset in range(240, 320, 8)), name
    return result


def outputs():
    data = tables()
    python = ['"""Generated B1 LUTs; edit display-driver/waveforms.json, then regenerate."""', '']
    c = ['/* Generated B1 LUTs. See display-driver/waveforms.json. */',
         '#pragma once', '#include <stdint.h>', '']
    for name, table in data.items():
        python += [name + ' = (']
        for start in range(0, len(table), 32):
            python.append('    b"' + ''.join('\\x%02x' % b for b in table[start:start+32]) + '"')
        python += [')', '']
        c.append('static const uint8_t BILLBOARD_LUT_%s[400] = {' % name)
        for start in range(0, len(table), 16):
            c.append('    ' + ', '.join('0x%02x' % b for b in table[start:start+16]) + ',')
        c += ['};', '']
    return {
        ROOT / 'Python/badge/lib/billboard_epd/waveforms.py': '\n'.join(python),
        ROOT / 'C/components/billboard_epd/billboard_waveforms.h': '\n'.join(c),
    }


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    for path, content in outputs().items():
        if args.check:
            if not path.exists() or path.read_text() != content:
                raise SystemExit('Outdated generated waveform: ' + str(path))
        else:
            path.write_text(content, encoding='utf-8', newline='\n')
    print('Waveforms verified' if args.check else 'Waveforms generated')
