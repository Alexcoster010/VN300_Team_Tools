import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
import email

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('manifest', ROOT / 'packaging/offline/verify_manifest.py')
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
        cache = ROOT / 'build/offline-cache'
        lock = json.loads((ROOT / 'packaging/offline/artifacts.lock.json').read_text())
        if not cache.exists():
            self.skipTest('Run build first to populate artifact cache')
        env = default_environment()
        env.update(python_version='3.11', python_full_version='3.11.2',
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
            self.assertIn('3.11.2', SpecifierSet(m.get('Requires-Python', '')))
        for name, m in metadata.items():
            for text in m.get_all('Requires-Dist', []):
                req = Requirement(text)
                if req.marker and not req.marker.evaluate(env):
                    continue
                dependency = canonicalize_name(req.name)
                self.assertIn(dependency, metadata, f'{name} needs {text}')
                self.assertIn(metadata[dependency]['Version'], req.specifier, f'{name} needs {text}')
        self.assertEqual(len(metadata), 16)


if __name__ == '__main__':
    unittest.main()
