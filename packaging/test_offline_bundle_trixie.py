import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile
import email

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('manifest', ROOT / 'packaging/offline-trixie/verify_manifest.py')
manifest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manifest)


class IntegrityTests(unittest.TestCase):
    def fixture(self, directory):
        root = Path(directory)
        (root / 'payload').write_bytes(b'committed logger')
        (root / 'MANIFEST.sha256').write_text(hashlib.sha256(b'committed logger').hexdigest() + '  payload\n')
        return root

    def test_intact_manifest_and_corrupted_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            with contextlib.redirect_stdout(io.StringIO()):
                manifest.verify(root)
            (root / 'payload').write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError, 'mismatch'):
                manifest.verify(root)

    def test_extra_file_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            (root / 'credentials').write_text('not allowed')
            with self.assertRaisesRegex(ValueError, 'Unexpected'):
                manifest.verify(root)

    def test_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            (root / 'MANIFEST.sha256').write_text('0' * 64 + '  ../escape\n')
            with self.assertRaisesRegex(ValueError, 'Unsafe'):
                manifest.verify(root)

    def test_native_and_python_dependency_closure(self):
        """Evaluate every wheel's real metadata against the target, including markers."""
        from packaging.requirements import Requirement
        from packaging.specifiers import SpecifierSet
        from packaging.utils import canonicalize_name
        from packaging.markers import default_environment
        cache = ROOT / 'build/offline-cache-trixie'
        lock = json.loads((ROOT / 'packaging/offline-trixie/artifacts.lock.json').read_text())
        if not cache.exists():
            self.skipTest('Run build first to populate artifact cache')
        env = default_environment()
        env.update(python_version='3.13', python_full_version='3.13.5',
                   sys_platform='linux', platform_system='Linux', platform_machine='aarch64',
                   implementation_name='cpython', platform_python_implementation='CPython', extra='')
        metadata = {}
        for artifact in lock['artifacts']:
            path = cache / Path(artifact['path']).name
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), artifact['sha256'])
            if path.suffix != '.whl':
                continue
            with zipfile.ZipFile(path) as wheel:
                m = email.message_from_bytes(wheel.read(next(n for n in wheel.namelist() if n.endswith('.dist-info/METADATA'))))
            metadata[canonicalize_name(m['Name'])] = m
            self.assertIn('3.13.5', SpecifierSet(m.get('Requires-Python', '')))
        for name, m in metadata.items():
            for text in m.get_all('Requires-Dist', []):
                req = Requirement(text)
                if req.marker and not req.marker.evaluate(env):
                    continue
                dependency = canonicalize_name(req.name)
                self.assertIn(dependency, metadata, f'{name} needs {text}')
                self.assertIn(metadata[dependency]['Version'], req.specifier, f'{name} needs {text}')
        self.assertEqual(len(metadata), 16)

    def test_target_and_installer_preflight(self):
        lock = json.loads((ROOT / 'packaging/offline-trixie/artifacts.lock.json').read_text())
        self.assertEqual(lock['target'], {
            'os': 'trixie', 'architecture': 'arm64', 'python': '3.13.5',
            'implementation': 'cpython', 'abi': 'cp313'})
        paths = [a['path'] for a in lock['artifacts']]
        self.assertTrue(all(path.endswith('_arm64.deb') for path in paths if path.endswith('.deb')))
        native_wheels = [path for path in paths if path.endswith('.whl') and 'none-any' not in path]
        self.assertEqual(native_wheels, [
            'wheels/bitstruct-8.23.0-cp313-cp313-manylinux2014_aarch64.manylinux_2_17_aarch64.manylinux_2_28_aarch64.whl'])
        installer = (ROOT / 'packaging/offline-trixie/install.sh').read_text()
        guard = '[ "${VERSION_CODENAME:-}" = trixie ] && [ "$ARCH" = arm64 ] && [ "$PY" = 3.13.5 ]'
        self.assertIn(guard, installer)
        self.assertLess(installer.index('verify_manifest.py'), installer.index(guard))
        self.assertLess(installer.index(guard), installer.index('apt-get "$@" --simulate'))
        self.assertLess(installer.index('apt-get "$@" --simulate'), installer.index('apt-get "$@" -y'))
        self.assertIn('Dir::Etc::sourcelist=/dev/null', installer)
        self.assertIn('Dir::Etc::sourceparts=/dev/null', installer)
        self.assertIn('Dir::State::lists=$APT_OFFLINE_LISTS', installer)

    def test_offline_apt_resolves_a_bundled_deb_without_repository_lists(self):
        if not all(shutil.which(cmd) for cmd in ('apt-get', 'dpkg-deb', 'dpkg')):
            self.skipTest('APT and dpkg are needed for the local package simulation')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'pkg/DEBIAN').mkdir(parents=True)
            (root / 'lists').mkdir()
            arch = subprocess.check_output(['dpkg', '--print-architecture'], text=True).strip()
            (root / 'pkg/DEBIAN/control').write_text(
                'Package: vn300-offline-apt-fixture\nVersion: 1\n'
                f'Architecture: {arch}\nMaintainer: VN300 test <test@example.com>\n'
                'Description: Local APT resolution fixture\n')
            deb = root / f'vn300-offline-apt-fixture_1_{arch}.deb'
            subprocess.run(['dpkg-deb', '--build', str(root / 'pkg'), str(deb)],
                           check=True, capture_output=True)
            result = subprocess.run([
                'apt-get', '-o', 'Dir::Etc::sourcelist=/dev/null',
                '-o', 'Dir::Etc::sourceparts=/dev/null',
                '-o', f'Dir::State::lists={root / "lists"}',
                '--simulate', '--no-download', '--no-install-recommends',
                '--no-remove', 'install', str(deb)],
                text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('local-deb', result.stdout)

    def test_locked_requirements_match_wheel_hashes(self):
        lock = json.loads((ROOT / 'packaging/offline-trixie/artifacts.lock.json').read_text())
        requirement_lines = (ROOT / 'packaging/offline-trixie/requirements.lock').read_text().splitlines()
        expected = {a['package'].replace('_', '-').lower(): a['sha256']
                    for a in lock['artifacts'] if a['path'].endswith('.whl')}
        for line in requirement_lines:
            name = line.split('==', 1)[0].replace('_', '-').lower()
            self.assertEqual(line.rsplit('sha256:', 1)[1], expected[name])


if __name__ == '__main__':
    unittest.main()
