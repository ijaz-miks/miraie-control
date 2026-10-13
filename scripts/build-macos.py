#!/usr/bin/env python3
"""Build a new, ad-hoc signed macOS app bundle without replacing existing files."""
import argparse
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import uuid


def main():
    if sys.platform != 'darwin':
        raise SystemExit('Build this app on macOS with the Xcode 26 or newer command-line tools.')
    source = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, help='new destination directory for the app bundle')
    args = parser.parse_args()
    output = (args.output_dir or source / 'macos/.build/bundles' / uuid.uuid4().hex[:8]).absolute()
    bundle = output / 'MirAIe Control.app'
    if bundle.exists() or bundle.is_symlink():
        raise SystemExit(f'Refusing to replace an existing application: {bundle}')
    subprocess.run(['xcrun', 'swift', 'build', '--package-path', str(source / 'macos'), '-c', 'release'], check=True)
    binary_dir = subprocess.check_output(['xcrun', 'swift', 'build', '--package-path', str(source / 'macos'),
        '-c', 'release', '--show-bin-path'], text=True).strip()
    executables = bundle / 'Contents/MacOS'
    resources = bundle / 'Contents/Resources'
    executables.mkdir(parents=True)
    resources.mkdir()
    shutil.copy2(Path(binary_dir) / 'MirAIeControl', executables / 'MirAIeControl')
    for name in ('LICENSE', 'NOTICE'):
        shutil.copy2(source / name, resources / name)
    metadata = {
        'CFBundleName': 'MirAIe Control', 'CFBundleDisplayName': 'MirAIe Control',
        'CFBundleIdentifier': 'io.github.ijaz.Miraie.macOS', 'CFBundleExecutable': 'MirAIeControl',
        'CFBundlePackageType': 'APPL', 'CFBundleShortVersionString': '0.2.0', 'CFBundleVersion': '2',
        'LSMinimumSystemVersion': '14.0', 'LSUIElement': True,
        'NSHighResolutionCapable': True,
        'NSHumanReadableCopyright': 'Copyright 2026 MirAIe Control contributors. MIT License.',
    }
    with (bundle / 'Contents/Info.plist').open('xb') as stream:
        plistlib.dump(metadata, stream)
    subprocess.run(['codesign', '--sign', '-', '--identifier', metadata['CFBundleIdentifier'], str(bundle)], check=True)
    subprocess.run(['codesign', '--verify', '--strict', str(bundle)], check=True)
    print(str(bundle))


if __name__ == '__main__':
    main()
