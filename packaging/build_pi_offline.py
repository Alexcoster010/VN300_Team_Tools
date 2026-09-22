#!/usr/bin/env python3
"""Build only committed, allowlisted files plus hash-locked public artifacts."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = ['PI_LOGGER_VERSION', 'pi/vn300_button_logger.py',
           'pi/vn300-button-logger.service', 'pi/vn300-shutdown-sudoers',
           'pi/requirements-pi.txt', 'pi/motec_can_signal_map.csv',
           'pi/can_profile.example.json', 'pi/CAN_SETUP.md', 'OFFLINE_INSTALL.md']
SUPPORT = ['install.sh', 'verify.py', 'requirements.lock', 'artifacts.lock.json', 'verify_manifest.py']


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, default=ROOT / 'dist/VN300_Offline_Bookworm_arm64.zip')
    ap.add_argument('--cache', type=Path, default=ROOT / 'build/offline-cache')
    ap.add_argument('--no-download', action='store_true')
    args = ap.parse_args()
    if subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=ROOT).strip():
        raise SystemExit('Build requires a clean committed working tree (generated artifacts must be ignored).')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    def committed(path):
        return subprocess.check_output(['git', 'show', f'{commit}:{path}'], cwd=ROOT)
    files = {p: committed(p) for p in PAYLOAD}
    files.update({p: committed('packaging/offline/' + p) for p in SUPPORT})
    lock = json.loads(files['artifacts.lock.json'])
    args.cache.mkdir(parents=True, exist_ok=True)
    for artifact in lock['artifacts']:
        name = artifact['path']
        cached = args.cache / Path(name).name
        if not cached.exists():
            if args.no_download:
                raise SystemExit(f'Missing cached artifact: {cached}')
            print('Downloading', name, flush=True)
            data = urllib.request.urlopen(artifact['url'], timeout=120).read()
            if sha(data) != artifact['sha256']:
                raise SystemExit(f'Download hash mismatch: {name}')
            cached.write_bytes(data)
        data = cached.read_bytes()
        if sha(data) != artifact['sha256']:
            raise SystemExit(f'Cache hash mismatch: {name}')
        files[name] = data
    files['BUILD.json'] = (json.dumps({'source_commit': commit, 'target': lock['target'],
                                      'artifact_count': len(lock['artifacts'])}, indent=2) + '\n').encode()
    files['MANIFEST.sha256'] = ''.join(f'{sha(data)}  {name}\n' for name, data in sorted(files.items())).encode()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / 'bundle.zip'
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as z:
            for name, data in sorted(files.items()):
                info = zipfile.ZipInfo('VN300_Offline/' + name, (2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (0o100755 if name.endswith('.sh') else 0o100644) << 16
                z.writestr(info, data)
        with zipfile.ZipFile(output) as z:
            assert z.testzip() is None
            assert len(z.namelist()) == len(files)
        args.output.write_bytes(output.read_bytes())
    digest = sha(args.output.read_bytes())
    args.output.with_suffix('.zip.sha256').write_text(f'{digest}  {args.output.name}\n')
    print(json.dumps({'zip': str(args.output.resolve()), 'sha256': digest,
                      'bytes': args.output.stat().st_size, 'commit': commit}, indent=2))


if __name__ == '__main__':
    main()
