#!/usr/bin/python3
"""Install into new user paths; never overwrite an existing installation."""
import argparse
import os
import pathlib
import shlex
import shutil
import subprocess

UUID = 'miraie-control@ijaz.local'


def install_paths(home):
    home = pathlib.Path(home)
    if not home.is_absolute() or any(char in str(home) for char in '\n\r\t='):
        raise ValueError('The home directory must be an absolute path without control characters or =.')
    return {
        'destination': home / '.local/share/miraie-control',
        'extension': home / '.local/share/gnome-shell/extensions' / UUID,
        'daemon': home / '.local/share/dbus-1/services/io.github.ijaz.Miraie.service',
        'desktop': home / '.local/share/applications/io.github.ijaz.Miraie.Settings.desktop',
        'launcher': home / '.local/bin/miraie-control-settings',
        'tray_launcher': home / '.local/bin/miraie-control-tray',
        'autostart': home / '.config/autostart/io.github.ijaz.Miraie.Tray.desktop',
    }


def keyfile_value(value):
    return value.replace('\\', '\\\\')


def desktop_exec(path):
    # Desktop files have keyfile escaping followed by Exec argument escaping.
    value = str(path).replace('%', '%%')
    for char in ('\\', '"', '`', '$'):
        value = value.replace(char, '\\' + char)
    return keyfile_value('"' + value + '"')


def launcher_contents(paths):
    destination = paths['destination']
    contents = {}
    for name, script in (('launcher', 'settings.py'), ('tray_launcher', 'tray.py')):
        argument = shlex.quote(str(destination / 'backend' / script))
        contents[name] = f'#!/bin/sh\nexec /usr/bin/python3 {argument} "$@"\n'
    command = shlex.join(['/usr/bin/python3', str(destination / 'backend/service.py')])
    contents['daemon'] = '[D-BUS Service]\nName=io.github.ijaz.Miraie\nExec=' + keyfile_value(command) + '\n'
    contents['desktop'] = ('[Desktop Entry]\nType=Application\nName=MirAIe AC Controls\n'
        'Comment=Control Panasonic MirAIe air conditioners\nExec=' + desktop_exec(paths['launcher']) +
        '\nIcon=weather-snow-symbolic\nCategories=Utility;GTK;\nTerminal=false\nStartupNotify=true\n')
    contents['autostart'] = ('[Desktop Entry]\nType=Application\nName=MirAIe AC Controls\nExec=' +
        desktop_exec(paths['tray_launcher']) + '\nIcon=weather-snow-symbolic\nTerminal=false\n'
        'X-GNOME-Autostart-enabled=true\n')
    return contents


def check_targets(paths):
    for path in paths.values():
        if os.path.lexists(path):
            raise RuntimeError(f'Refusing to replace existing target: {path}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='show destinations without changing anything')
    args = parser.parse_args()
    source = pathlib.Path(__file__).resolve().parent.parent
    paths = install_paths(pathlib.Path.home())
    if args.dry_run:
        for name, path in paths.items():
            print(f'{name}: {path}' + (' (already exists)' if os.path.lexists(path) else ''))
        return

    # Validate dependencies and targets before creating the first destination.
    from gi.repository import Gio
    schema = Gio.SettingsSchemaSource.get_default().lookup('org.gnome.shell', True)
    if schema is None:
        raise RuntimeError('GNOME Shell settings are required for this installer.')
    if not (source / 'vendor/paho/mqtt/client.py').is_file():
        raise RuntimeError('Run /usr/bin/python3 scripts/vendor-paho.py first.')
    check_targets(paths)
    subprocess.run(['glib-compile-schemas', '--strict', '--dry-run', str(source / 'extension/schemas')], check=True)
    destination, extension = paths['destination'], paths['extension']
    destination.mkdir(parents=True)
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo', 'gschemas.compiled')
    for name in ('backend', 'vendor'):
        shutil.copytree(source / name, destination / name, ignore=ignore)
    for name in ('LICENSE', 'NOTICE'):
        shutil.copyfile(source / name, destination / name)
    shutil.copytree(source / 'extension', extension,
                    ignore=shutil.ignore_patterns('tests', '__pycache__', 'gschemas.compiled'))
    subprocess.run(['glib-compile-schemas', '--strict', str(extension / 'schemas')], check=True)
    for name, content in launcher_contents(paths).items():
        path = paths[name]
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8') as stream:
            stream.write(content)
        if name in ('launcher', 'tray_launcher'):
            path.chmod(0o755)
    settings = Gio.Settings.new_full(schema, None, None)
    enabled = settings.get_strv('enabled-extensions')
    if UUID not in enabled:
        if not settings.set_strv('enabled-extensions', enabled + [UUID]):
            raise RuntimeError('Installed files, but GNOME did not accept the enabled-extension setting.')
        Gio.Settings.sync()
    print('Installed AC backend, account window, and GNOME panel into new user paths.')
    print('Launch: ' + str(paths['launcher']))
    print('Extension: ' + UUID)


if __name__ == '__main__':
    main()
