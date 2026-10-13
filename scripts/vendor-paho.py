#!/usr/bin/python3
"""Acquire the pinned pure-Python MQTT dependency without installer cleanup."""
import hashlib
import io
import json
import pathlib
import urllib.request
import zipfile

VERSION = '2.1.0'
WHEEL = 'paho_mqtt-2.1.0-py3-none-any.whl'
SHA256 = '6db9ba9b34ed5bc6b6e3812718c7e06e2fd7444540df2455d2c51bd58808feee'


def main():
    root = pathlib.Path(__file__).resolve().parent.parent / 'vendor'
    with urllib.request.urlopen(f'https://pypi.org/pypi/paho-mqtt/{VERSION}/json', timeout=30) as response:
        metadata = json.load(response)
    wheel = next(item for item in metadata['urls'] if item['filename'] == WHEEL)
    if wheel['digests']['sha256'] != SHA256 or not wheel['url'].startswith('https://files.pythonhosted.org/'):
        raise RuntimeError('PyPI metadata did not match the pinned MQTT wheel.')
    with urllib.request.urlopen(wheel['url'], timeout=30) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise RuntimeError('The MQTT dependency digest did not match the pinned SHA256.')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = []
        for member in archive.infolist():
            relative = pathlib.PurePosixPath(member.filename)
            if relative.is_absolute() or '..' in relative.parts:
                raise RuntimeError('Unsafe wheel member path.')
            destination = root.joinpath(*relative.parts)
            if destination.is_symlink() or any(parent.is_symlink() for parent in destination.parents):
                raise RuntimeError('Refusing to follow a symlink in the vendor directory.')
            contents = None if member.is_dir() else archive.read(member)
            if destination.exists() and contents is not None:
                if not destination.is_file() or destination.read_bytes() != contents:
                    raise RuntimeError(f'Refusing to replace a differing vendored file: {destination}')
            members.append((destination, contents))
        # A second run verifies the existing dependency without overwriting it.
        root.mkdir(exist_ok=True)
        for destination, contents in members:
            if contents is None:
                destination.mkdir(parents=True, exist_ok=True)
            elif not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open('xb') as stream:
                    stream.write(contents)
    print(f'Verified paho-mqtt {VERSION}; SHA256={SHA256}')


if __name__ == '__main__':
    main()
