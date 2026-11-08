"""Build the portable core and run host tests. Requires Zig or a C compiler.

Windows example: python tools/test_native.py --cc path/to/zig.exe
For an ordinary compiler pass --cc cc (or gcc/clang).
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser()
parser.add_argument('--cc', default='cc')
args = parser.parse_args()
output = ROOT / '.work/native-tests'
output.mkdir(parents=True, exist_ok=True)
dll = output / ('billboard.dll' if os.name == 'nt' else 'libbillboard.so')
compiler = [args.cc]
if Path(args.cc).stem == 'zig':
    compiler.append('cc')
source = ROOT / 'C/components/billboard_epd'
command = compiler + ['-std=c11', '-Wall', '-Wextra', '-Werror', '-O2', '-shared',
                      '-I', str(source / 'include'), str(source / 'billboard_epd.c'),
                      str(source / 'billboard_decay.c'), '-o', str(dll)]
if os.name == 'nt':
    command.append('-Wl,--export-all-symbols')
else:
    command.append('-fPIC')
subprocess.run(command, check=True)
env = dict(os.environ, BILLBOARD_TEST_DLL=str(dll))
subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s',
                str(ROOT / 'display-driver/tests'), '-v'], env=env, check=True)
