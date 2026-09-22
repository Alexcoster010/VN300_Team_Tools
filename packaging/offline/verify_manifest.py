#!/usr/bin/env python3
"""Check every bundled file, reject missing/extra files and unsafe paths."""
import hashlib
from pathlib import Path
import sys


def verify(root):
    expected = set()
    for line in (root / 'MANIFEST.sha256').read_text().splitlines():
        digest, name = line.split('  ', 1)
        path = root / name
        if name in expected or Path(name).is_absolute() or '..' in Path(name).parts or path.is_symlink():
            raise ValueError(f'Unsafe/duplicate manifest entry: {name}')
        expected.add(name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f'SHA-256 mismatch: {name}')
    actual = {str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()}
    extra = actual - expected - {'MANIFEST.sha256'}
    if extra:
        raise ValueError(f'Unexpected files: {sorted(extra)}')
    print(f'PASS manifest: {len(expected)} files verified')


if __name__ == '__main__':
    try:
        verify(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent)
    except (OSError, ValueError) as exc:
        sys.exit(f'FAIL manifest: {exc}')
