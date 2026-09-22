#!/usr/bin/env python3
import hashlib, pathlib, re, sys

def verify(root):
    root = pathlib.Path(root).resolve()
    manifest = root / "MANIFEST.sha256"
    expected = {}
    for line in manifest.read_text().splitlines():
        digest, name = line.split("  ", 1)
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or pathlib.PurePosixPath(name).is_absolute() or ".." in pathlib.PurePosixPath(name).parts:
            raise ValueError("Unsafe manifest entry")
        expected[name] = digest
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and p != manifest}
    if actual != set(expected):
        raise ValueError(f"Manifest file set mismatch; missing={sorted(set(expected)-actual)}, unexpected={sorted(actual-set(expected))}")
    for name, digest in expected.items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"SHA-256 mismatch: {name}")
    print(f"Manifest OK: {len(expected)} files")

if __name__ == "__main__":
    verify(sys.argv[1] if len(sys.argv) > 1 else pathlib.Path(__file__).parent)
