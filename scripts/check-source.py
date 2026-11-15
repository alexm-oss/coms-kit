"""Compile every Python source and check every shell script without running it."""
import pathlib
import subprocess
import sys

root = pathlib.Path(__file__).resolve().parent.parent
shells = []
python = []
for path in sorted(root.rglob('*')):
    if not path.is_file() or any(p in ('.git', '__pycache__') for p in path.parts):
        continue
    raw = path.read_bytes()
    first = raw.splitlines()[0] if raw else b''
    if path.suffix == '.py' or first.startswith(b'#!') and b'python' in first:
        compile(raw, str(path), 'exec')
        python.append(path)
    if path.suffix == '.sh' or first.startswith(b'#!') and (b'bash' in first or b'/sh' in first):
        shells.append(path)
        if '--shellcheck' not in sys.argv:
            subprocess.run(['bash', '-n', str(path)], check=True)
if '--shellcheck' in sys.argv:
    subprocess.run(['shellcheck', *map(str, shells)], check=True)
    print('shellcheck:', len(shells), 'scripts clean')
else:
    print('source syntax:', len(python), 'Python files,', len(shells), 'shell scripts checked')
