"""Model-gated MirAIe UDP control on the current Wi-Fi network."""
import ipaddress
import json
import pathlib
import re
import shutil
import socket
import subprocess
import threading
import time

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def wifi_networks():
    executable = shutil.which('ip')
    if not executable:
        return []
    result = subprocess.run([executable, '-j', '-4', 'addr', 'show'], capture_output=True, text=True, timeout=3, check=True)
    networks = []
    for interface in json.loads(result.stdout):
        if not (pathlib.Path('/sys/class/net') / interface['ifname'] / 'wireless').exists():
            continue
        for address in interface.get('addr_info', []):
            if address.get('scope') == 'global' and address.get('family') == 'inet':
                item = ipaddress.IPv4Interface(f"{address['local']}/{address['prefixlen']}")
                networks.append((item.network, item.ip))
    return networks


def encrypt(text, key, iv):
    data = text.encode('utf-8')
    data += b'\x00' * (-len(data) % 16)
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return encryptor.update(data) + encryptor.finalize()


def decrypt(data, key, iv):
    if not data or len(data) % 16:
        raise ValueError('Invalid encrypted packet length.')
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return (decryptor.update(data) + decryptor.finalize()).rstrip(b'\x00').decode('utf-8')


class LocalTransport:
    def __init__(self, changed):
        self.changed = changed
        self.socket = None
        self.devices = {}
        self.lock = threading.RLock()
        self.networks = []
        self.running = False
        self.thread = None

    def add(self, identity, detail):
        model = detail.get('_model', {})
        entries = model.get('controlCapabilities', []) if isinstance(model, dict) else []
        declaration = next((item for item in entries if isinstance(item, dict) and item.get('capability') == 'udpControl'), None)
        if not declaration:
            return False
        required = re.findall(r'\d+', str(declaration.get('MinFWVersion', '')))
        firmware = re.findall(r'\d+', str(detail.get('firmwareVersion', '')))
        length = max(len(required), len(firmware))
        installed_version = tuple(map(int, firmware + ['0'] * (length - len(firmware))))
        minimum_version = tuple(map(int, required + ['0'] * (length - len(required))))
        if required and (not firmware or installed_version < minimum_version):
            return False
        try:
            key = bytes.fromhex(detail.get('udpControlKey', ''))
            iv = bytes.fromhex(detail.get('udpcontrolIV', ''))
            mac = re.sub(r'[:-]', '', detail.get('macAddress', '')).upper()
        except (ValueError, TypeError):
            return False
        if len(key) not in (16, 24, 32) or len(iv) != 16 or not re.fullmatch(r'[0-9A-F]{12}', mac):
            return False
        with self.lock:
            self.devices[identity] = {'key': key, 'iv': iv, 'mac': mac, 'ip': None, 'seen': 0, 'found': threading.Event()}
        if self.socket is None:
            try:
                self.networks = wifi_networks()
                if not self.networks:
                    return False
                self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                self.socket.bind(('', 5001))
                self.socket.settimeout(0.5)
                self.running = True
                self.thread = threading.Thread(target=self._receive, name='miraie-local', daemon=True)
                self.thread.start()
            except (OSError, ValueError, subprocess.SubprocessError):
                if self.socket:
                    self.socket.close()
                self.socket = None
                return False
        return True

    def discover(self, identity, timeout=1.5):
        with self.lock:
            device = self.devices.get(identity)
            if not self.socket or not device:
                return None
            if device['ip'] and time.monotonic() - device['seen'] < 30:
                return device['ip']
            device['found'].clear()
            request = ('DISC_REQ_' + device['mac']).encode('ascii')
            for network, _own in self.networks:
                self.socket.sendto(request, (str(network.broadcast_address), 5001))
        device['found'].wait(timeout)
        with self.lock:
            return device['ip'] if device['found'].is_set() else None

    def send(self, identity, payload):
        address = self.discover(identity)
        if not address:
            return False  # Discovery sent no control. Cloud fallback remains safe.
        with self.lock:
            device = self.devices[identity]
            local_payload = {**payload, 'ki': 1, 'cnt': 'an', 'sid': '1'}
            packet = encrypt(json.dumps(local_payload, separators=(',', ':')), device['key'], device['iv'])
            self.socket.sendto(packet, (address, 5001))
        return True

    def _receive(self):
        while self.running:
            try:
                data, (address, port) = self.socket.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                source = ipaddress.IPv4Address(address)
                if port != 5001 or not any(source in network and source != own for network, own in self.networks):
                    continue
                with self.lock:
                    items = list(self.devices.items())
                for identity, device in items:
                    try:
                        text = decrypt(data, device['key'], device['iv'])
                        if text.upper() == 'DISC_RSP_' + device['mac']:
                            with self.lock:
                                device.update(ip=address, seen=time.monotonic())
                                device['found'].set()
                            break
                        if device['ip'] != address:
                            continue
                        status = json.loads(text)
                        if not isinstance(status, dict) or status.get('deviceId', identity) != identity:
                            continue
                        with self.lock:
                            device['seen'] = time.monotonic()
                        self.changed(identity, status)
                        break
                    except (ValueError, UnicodeError, TypeError):
                        continue
            except ValueError:
                continue

    def close(self):
        self.running = False
        if self.socket:
            self.socket.close()
        if self.thread:
            self.thread.join(timeout=2)
        self.socket = None
        self.devices.clear()
