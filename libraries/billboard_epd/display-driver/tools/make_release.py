"""Create deterministic source archives from explicitly selected library files."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[2]
C = ROOT / 'C/components/billboard_epd'
PY = ROOT / 'Python/badge/lib/billboard_epd'
DOCS = ROOT / 'display-driver'
VERSION = json.loads((PY / 'package.json').read_text())['version']


def selected(directory, patterns):
    return sorted({path for pattern in patterns for path in directory.glob(pattern)
                   if path.is_file()})


def source_files():
    paths = [ROOT / 'LICENSE', ROOT / 'README.md', ROOT / '.gitignore']
    paths += selected(C, ['*.c', '*.h', 'include/*.h', '*.md', 'LICENSE',
                          'CMakeLists.txt', 'idf_component.yml'])
    paths += selected(PY, ['*.py', '*.md', 'LICENSE', 'package.json'])
    paths += selected(DOCS, ['*.md', 'waveforms.json', 'tools/*.py', 'tests/*.py',
                            'examples/micropython/*.py',
                            'examples/esp-idf/CMakeLists.txt',
                            'examples/esp-idf/sdkconfig.defaults',
                            'examples/esp-idf/main/*.c',
                            'examples/esp-idf/main/CMakeLists.txt'])
    return paths


def write_zip(path, entries):
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, source in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes())
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('Invalid ZIP: ' + str(path))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=DOCS / 'dist')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for package in (C, PY):
        if (package / 'LICENSE').read_bytes() != (ROOT / 'LICENSE').read_bytes():
            raise SystemExit('Package LICENSE differs: ' + str(package))
    files = source_files()
    for source in files:
        if not source.is_file():
            raise SystemExit('Missing source file: ' + str(source))
    source = {'billboard_epd/' + path.relative_to(ROOT).as_posix(): path for path in files}
    component = {'billboard_epd/' + path.relative_to(C).as_posix(): path
                 for path in files if path.is_relative_to(C)}
    micropython = {'billboard_epd/' + path.relative_to(PY).as_posix(): path
                  for path in files if path.is_relative_to(PY)}
    for path in selected(DOCS, ['*.md']):
        component['billboard_epd/docs/' + path.name] = path
        micropython['docs/' + path.name] = path
    for path in files:
        if path.is_relative_to(DOCS / 'examples/esp-idf'):
            component['billboard_epd/examples/hello/' +
                      path.relative_to(DOCS / 'examples/esp-idf').as_posix()] = path
        elif path.is_relative_to(DOCS / 'examples/micropython'):
            micropython['examples/' + path.name] = path
    checksums = []
    for kind, entries in [('source', source), ('esp-idf', component), ('micropython', micropython)]:
        name = 'billboard-epd-' + VERSION + '-' + kind + '.zip'
        digest = write_zip(args.output / name, entries)
        checksums.append(digest + '  ' + name)
        print(name + ': ' + str(len(entries)) + ' files')
    (args.output / 'SHA256SUMS').write_text('\n'.join(checksums) + '\n', encoding='ascii')


if __name__ == '__main__':
    main()
