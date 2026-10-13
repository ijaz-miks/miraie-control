"""Portable installation tests; all generated files are retained, never removed."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import time
import unittest
import uuid

from gi.repository import Gio, GLib

SOURCE = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('installer', SOURCE / 'scripts/install-local.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.output = SOURCE / 'test-output' / ('install-' + uuid.uuid4().hex[:8])
        cls.output.mkdir(parents=True)

    def home(self, name):
        path = self.output / name
        path.mkdir()
        return path

    def test_dry_run_does_not_create_destinations(self):
        home = self.home('dry run')
        result = subprocess.run(['/usr/bin/python3', str(SOURCE / 'scripts/install-local.py'), '--dry-run'],
            env={**os.environ, 'HOME': str(home)}, capture_output=True, text=True, check=True)
        self.assertIn(str(home / '.local/bin/miraie-control-settings'), result.stdout)
        self.assertEqual(list(home.iterdir()), [])

    def test_install_preserves_licenses_and_refuses_existing_destinations(self):
        home = self.home('fresh installation')
        command = ['/usr/bin/python3', str(SOURCE / 'scripts/install-local.py')]
        env = {**os.environ, 'HOME': str(home), 'GSETTINGS_BACKEND': 'memory'}
        subprocess.run(command, env=env, capture_output=True, text=True, check=True)
        paths = installer.install_paths(home)
        self.assertTrue(all(path.exists() for path in paths.values()))
        self.assertTrue((paths['destination'] / 'LICENSE').is_file())
        self.assertTrue((paths['destination'] / 'vendor/paho_mqtt-2.1.0.dist-info/licenses/LICENSE.txt').is_file())
        self.assertFalse((paths['destination'] / 'research').exists())
        self.assertFalse(list(paths['destination'].rglob('__pycache__')))
        before = paths['launcher'].read_bytes()
        repeated = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertNotEqual(repeated.returncode, 0)
        self.assertIn('Refusing to replace existing target', repeated.stderr)
        self.assertEqual(paths['launcher'].read_bytes(), before)

    def test_dangling_symlink_is_an_existing_target(self):
        home = self.home('symlink installation')
        paths = installer.install_paths(home)
        paths['launcher'].parent.mkdir(parents=True)
        paths['launcher'].symlink_to(home / 'missing')
        with self.assertRaisesRegex(RuntimeError, 'Refusing to replace'):
            installer.check_targets(paths)

    def test_home_validation(self):
        for home in ('relative', '/example/bad\nname', '/example/bad=name'):
            with self.assertRaises(ValueError):
                installer.install_paths(home)

    def test_shell_and_dbus_paths_roundtrip_with_reserved_characters(self):
        home = self.home("space's $literal `literal` \\path")
        paths = installer.install_paths(home)
        contents = installer.launcher_contents(paths)
        args = shlex.split(contents['launcher'].splitlines()[1])
        self.assertEqual(args, ['exec', '/usr/bin/python3',
            str(paths['destination'] / 'backend/settings.py'), '$@'])
        keyfile = GLib.KeyFile()
        keyfile.load_from_data(contents['daemon'], len(contents['daemon'].encode()), GLib.KeyFileFlags.NONE)
        ok, args = GLib.shell_parse_argv(keyfile.get_string('D-BUS Service', 'Exec'))
        self.assertTrue(ok)
        self.assertEqual(args, ['/usr/bin/python3', str(paths['destination'] / 'backend/service.py')])

    def test_desktop_entry_launches_literal_path_with_spaces_and_symbols(self):
        home = self.home("desktop's $literal `literal` \\path")
        paths = installer.install_paths(home)
        backend = paths['destination'] / 'backend'
        backend.mkdir(parents=True)
        result_path = home / 'launched.json'
        # This harmless fixture replaces the control window in the test install.
        (backend / 'settings.py').write_text('from pathlib import Path\n'
            f'Path({str(result_path)!r}).write_text("[]")\n')
        paths['launcher'].parent.mkdir(parents=True)
        contents = installer.launcher_contents(paths)
        paths['launcher'].write_text(contents['launcher'])
        paths['launcher'].chmod(0o700)
        keyfile = GLib.KeyFile()
        keyfile.load_from_data(contents['desktop'], len(contents['desktop'].encode()), GLib.KeyFileFlags.NONE)
        app = Gio.DesktopAppInfo.new_from_keyfile(keyfile)
        self.assertIsNotNone(app)
        self.assertTrue(app.launch([], None))
        deadline = time.monotonic() + 5
        while not result_path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(result_path.is_file(), 'Desktop entry must execute the literal installed path')
        self.assertEqual(json.loads(result_path.read_text()), [])


if __name__ == '__main__':
    unittest.main()
