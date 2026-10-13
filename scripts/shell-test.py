#!/usr/bin/python3
"""Exercise MirAIe in a retained, isolated GNOME Shell with an explicit mock.

The local Momentum 4 smoke harness informed the isolation approach. This harness
is independently implemented for the MirAIe D-Bus extension.
"""
import argparse
import configparser
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import uuid


def main():
    source = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    root = (args.output_dir or source / 'test-output' / f'shell-{uuid.uuid4().hex[:8]}').absolute()
    runtime = root / 'runtime'
    if len(str(runtime / 'miraie-test').encode()) >= 104:
        parser.error('Choose a shorter retained output directory for the Wayland socket.')
    root.mkdir(parents=True, exist_ok=False)
    for folder in ('data', 'config', 'cache', 'runtime', 'helper'):
        (root / folder).mkdir(mode=0o700)
    extension = root / 'data/gnome-shell/extensions/miraie-control@ijaz.local'
    shutil.copytree(source / 'extension', extension)
    subprocess.run(['/usr/bin/glib-compile-schemas', str(extension / 'schemas')], check=True)
    hashes = {str(path.relative_to(extension)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in extension.rglob('*') if path.is_file()}
    (root / 'extension-hashes.json').write_text(json.dumps(hashes, indent=2))
    helper = root / 'helper/gnome-shell-perf-helper'
    helper.write_text('#!/bin/sh\nexec /usr/libexec/gnome-shell-perf-helper --idle-timeout=90 "$@"\n')
    helper.chmod(0o700)
    settings = configparser.ConfigParser()
    settings['org/gnome/shell'] = {
        'enabled-extensions': "['miraie-control@ijaz.local']", 'disable-user-extensions': 'false'}
    settings['org/gnome/desktop/interface'] = {'enable-animations': 'false'}
    settings['org/gnome/desktop/session'] = {'idle-delay': 'uint32 0'}
    settings_dir = root / 'config/glib-2.0/settings'
    settings_dir.mkdir(parents=True)
    with (settings_dir / 'keyfile').open('x') as stream:
        settings.write(stream)
    env = dict(os.environ)
    for name in ('DBUS_SESSION_BUS_ADDRESS', 'DBUS_SYSTEM_BUS_ADDRESS', 'DISPLAY',
                 'GNOME_SETUP_DISPLAY', 'AT_SPI_BUS_ADDRESS', 'PULSE_SERVER'):
        env.pop(name, None)
    env.update(XDG_DATA_HOME=str(root / 'data'), XDG_CONFIG_HOME=str(root / 'config'),
               XDG_CACHE_HOME=str(root / 'cache'), XDG_RUNTIME_DIR=str(runtime),
               GSETTINGS_BACKEND='keyfile', GNOME_SHELL_SESSION_MODE='user',
               GNOME_SHELL_BUILDDIR=str(root / 'helper'), MIRAIETEST_OUTPUT=str(root),
               MIRAIETEST_ISOLATED='1', WAYLAND_DISPLAY='miraie-test',
               LIBGL_ALWAYS_SOFTWARE='1', NO_AT_BRIDGE='1', GIO_USE_VFS='local', GTK_USE_PORTAL='0')
    command = ['/usr/bin/dbus-run-session', '--', '/usr/bin/sh', '-c',
               'DBUS_SYSTEM_BUS_ADDRESS="$DBUS_SESSION_BUS_ADDRESS" exec "$@"', 'miraie-shell-test',
               '/usr/bin/gnome-shell', '--headless', '--no-x11', '--virtual-monitor', '1280x1024',
               '--wayland-display', 'miraie-test', '--mode', 'user',
               '--automation-script', str(source / 'tests/shell-smoke.js')]
    print(f'Retained isolated Shell test: {root}', flush=True)
    with (root / 'shell.log').open('x') as log:
        process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            code = process.wait(timeout=80)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            print('Isolated Shell exceeded its deadline. All test files retained.', file=sys.stderr)
            return 1
        finally:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    result = root / 'result.json'
    log_text = (root / 'shell.log').read_text()
    if code or not result.exists():
        print(log_text[-12000:], file=sys.stderr)
        return code or 1
    report = json.loads(result.read_text())
    for marker in ('JS ERROR', 'already disposed', 'not in the stage', 'Clutter-CRITICAL', 'St-CRITICAL'):
        if marker in log_text:
            report.setdefault('logFailures', []).append(marker)
            report['passed'] = False
    if not report.get('passed'):
        print(log_text[-12000:], file=sys.stderr)
    print(json.dumps(report, indent=2))
    return 0 if report.get('passed') else 1


if __name__ == '__main__':
    sys.exit(main())
