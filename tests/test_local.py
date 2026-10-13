"""Local UDP protocol tests using synthetic keys and fake sockets only."""
from __future__ import annotations

import copy
import importlib.util
import ipaddress
import json
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

SPEC = importlib.util.spec_from_file_location('miraie_local_under_test', Path(__file__).resolve().parents[1] / 'backend' / 'local.py')
local = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(local)
KEY = bytes.fromhex('000102030405060708090a0b0c0d0e0f')
IV = bytes.fromhex('101112131415161718191a1b1c1d1e1f')
MAC = '0123456789AB'
IDENTITY = 'synthetic-ac'
NETWORK = ipaddress.IPv4Network('192.0.2.0/24')
OWN = ipaddress.IPv4Address('192.0.2.10')


def detail(**overrides):
    result = {'_model': {'controlCapabilities': [{'capability': 'udpControl', 'MinFWVersion': '3.2.0'}]},
              'firmwareVersion': '3.2.1', 'udpControlKey': KEY.hex(), 'udpcontrolIV': IV.hex(),
              'macAddress': '01:23:45:67:89:AB'}
    result.update(overrides)
    return result


class FakeSocket:
    def __init__(self, packets=()):
        self.packets = iter(packets)
        self.sent = []
        self.binds = []
        self.options = []
        self.timeout = None
        self.closed = False

    def sendto(self, payload, destination):
        self.sent.append((payload, destination))
        return len(payload)

    def recvfrom(self, _limit):
        try:
            return next(self.packets)
        except StopIteration:
            raise OSError('Synthetic socket exhausted') from None

    def bind(self, address):
        self.binds.append(address)

    def setsockopt(self, *args):
        self.options.append(args)

    def settimeout(self, timeout):
        self.timeout = timeout

    def close(self):
        self.closed = True


def setup_transport(packets=(), model_detail=None):
    events = []
    transport = local.LocalTransport(lambda identity, status: events.append((identity, status)))
    sock = FakeSocket(packets)
    with patch.object(local, 'wifi_networks', return_value=[(NETWORK, OWN)]), \
         patch.object(local.socket, 'socket', return_value=sock), \
         patch.object(local.threading, 'Thread', return_value=Mock()):
        assert transport.add(IDENTITY, model_detail or detail())
    return transport, sock, events


class CryptoTests(unittest.TestCase):
    def test_fixed_aes_cbc_vector_verified_independently_with_openssl(self):
        # AES-128-CBC, no PKCS padding. This aligned ASCII vector was checked with OpenSSL.
        packet = local.encrypt('0123456789ABCDEF', KEY, IV)
        self.assertEqual(packet.hex(), '9be37dd9a6322852855fd577ed0b73a0')
        self.assertEqual(local.decrypt(packet, KEY, IV), '0123456789ABCDEF')

    def test_zero_padding_at_block_boundaries_and_utf8_roundtrip(self):
        for text, length in [('x', 16), ('x' * 15, 16), ('x' * 16, 16), ('x' * 17, 32),
                             ('{"name":"गर्मी","ps":"on"}', 48)]:
            with self.subTest(text=text):
                packet = local.encrypt(text, KEY, IV)
                self.assertEqual(len(packet), length)
                self.assertEqual(local.decrypt(packet, KEY, IV), text)
                decryptor = Cipher(algorithms.AES(KEY), modes.CBC(IV)).decryptor()
                raw = decryptor.update(packet) + decryptor.finalize()
                self.assertEqual(raw[len(text.encode('utf-8')):], b'\x00' * (-len(text.encode('utf-8')) % 16))

    def test_all_supported_aes_key_lengths_roundtrip(self):
        for key_length in (16, 24, 32):
            with self.subTest(key_length=key_length):
                key = bytes(range(key_length))
                self.assertEqual(local.decrypt(local.encrypt('DISC_RSP_' + MAC, key, IV), key, IV), 'DISC_RSP_' + MAC)

    def test_invalid_ciphertext_length_and_utf8_are_rejected(self):
        for packet in (b'', b'x', b'x' * 15, b'x' * 17):
            with self.subTest(length=len(packet)):
                with self.assertRaises(ValueError):
                    local.decrypt(packet, KEY, IV)
        encryptor = Cipher(algorithms.AES(KEY), modes.CBC(IV)).encryptor()
        malformed = encryptor.update(b'\xff' * 16) + encryptor.finalize()
        with self.assertRaises(UnicodeError):
            local.decrypt(malformed, KEY, IV)


class ModelGatingTests(unittest.TestCase):
    def test_unsupported_model_never_opens_socket_or_sends_discovery(self):
        for model in ({}, None, [], {'controlCapabilities': [{'capability': 'cloudControl'}]}):
            with self.subTest(model=model):
                transport = local.LocalTransport(lambda *_args: None)
                with patch.object(local, 'wifi_networks') as networks, patch.object(local.socket, 'socket') as socket_factory:
                    self.assertFalse(transport.add(IDENTITY, detail(_model=model)))
                    self.assertIsNone(transport.discover(IDENTITY, timeout=0))
                    self.assertFalse(transport.send(IDENTITY, {'ps': 'off'}))
                    networks.assert_not_called()
                    socket_factory.assert_not_called()
                self.assertEqual(transport.devices, {})

    def test_invalid_key_iv_mac_and_old_firmware_never_open_socket(self):
        cases = [{'udpControlKey': ''}, {'udpControlKey': 'zz' * 16}, {'udpControlKey': '00' * 15},
                 {'udpcontrolIV': '00' * 15}, {'udpcontrolIV': None}, {'macAddress': 'invalid'},
                 {'firmwareVersion': '3.1.9'}, {'firmwareVersion': ''}]
        for override in cases:
            with self.subTest(override=override):
                transport = local.LocalTransport(lambda *_args: None)
                with patch.object(local, 'wifi_networks') as networks, patch.object(local.socket, 'socket') as socket_factory:
                    self.assertFalse(transport.add(IDENTITY, detail(**override)))
                    networks.assert_not_called()
                    socket_factory.assert_not_called()

    def test_no_active_wifi_never_opens_socket_or_sends(self):
        transport = local.LocalTransport(lambda *_args: None)
        with patch.object(local, 'wifi_networks', return_value=[]), patch.object(local.socket, 'socket') as socket_factory:
            self.assertFalse(transport.add(IDENTITY, detail()))
            self.assertIsNone(transport.discover(IDENTITY, timeout=0))
            self.assertFalse(transport.send(IDENTITY, {'ps': 'off'}))
            socket_factory.assert_not_called()

    def test_equivalent_firmware_versions_have_same_gate_result(self):
        transport, sock, _events = setup_transport(model_detail=detail(firmwareVersion='3.2'))
        self.assertEqual(sock.binds, [('', 5001)])
        transport.close()

    def test_explicit_model_without_firmware_floor_is_allowed(self):
        model = {'controlCapabilities': [{'capability': 'udpControl'}]}
        transport, sock, _events = setup_transport(model_detail=detail(_model=model, firmwareVersion=''))
        self.assertEqual(sock.binds, [('', 5001)])
        transport.close()


class DiscoveryAndPacketTests(unittest.TestCase):
    def test_discovery_request_and_encrypted_matching_response(self):
        response = local.encrypt('DISC_RSP_' + MAC, KEY, IV)
        transport, sock, events = setup_transport([(response, ('192.0.2.25', 5001))])
        found = transport.devices[IDENTITY]['found']
        original_wait = found.wait
        found.wait = lambda _timeout: transport._receive()
        self.assertEqual(transport.discover(IDENTITY, timeout=0), '192.0.2.25')
        found.wait = original_wait
        self.assertEqual(sock.sent, [(('DISC_REQ_' + MAC).encode(), ('192.0.2.255', 5001))])
        self.assertEqual(events, [], 'Discovery must not be reported as AC state')
        transport.close()

    def test_control_payload_uses_exact_local_envelope_and_learned_destination(self):
        transport, sock, _events = setup_transport()
        transport.devices[IDENTITY].update(ip='192.0.2.25', seen=time.monotonic())
        self.assertTrue(transport.send(IDENTITY, {'ki': 8, 'sid': '42', 'cnt': 'other', 'acdc': 'off'}))
        self.assertEqual(len(sock.sent), 1)
        packet, destination = sock.sent[0]
        self.assertEqual(destination, ('192.0.2.25', 5001))
        self.assertEqual(json.loads(local.decrypt(packet, KEY, IV)), {'ki': 1, 'sid': '1', 'cnt': 'an', 'acdc': 'off'})
        self.assertNotIn('ps', json.loads(local.decrypt(packet, KEY, IV)))
        transport.close()

    def test_only_learned_ip_same_wifi_source_port_and_identity_are_accepted(self):
        discovery = local.encrypt('DISC_RSP_' + MAC, KEY, IV)
        status = local.encrypt('{"actmp":"25","deviceId":"synthetic-ac"}', KEY, IV)
        wrong_identity = local.encrypt('{"actmp":"28","deviceId":"other-ac"}', KEY, IV)
        packets = [(discovery, ('198.51.100.25', 5001)),  # outside Wi-Fi subnet
                   (discovery, ('192.0.2.10', 5001)),   # this computer
                   (discovery, ('192.0.2.25', 1234)),   # wrong source port
                   (status, ('192.0.2.25', 5001)),      # no matching discovery yet
                   (discovery, ('192.0.2.25', 5001)),
                   (wrong_identity, ('192.0.2.25', 5001)),
                   (status, ('192.0.2.26', 5001)),      # unlearned source
                   (b'corrupt', ('192.0.2.25', 5001)),
                   (status, ('192.0.2.25', 5001))]
        transport, sock, events = setup_transport(packets)
        transport._receive()
        self.assertEqual(transport.devices[IDENTITY]['ip'], '192.0.2.25')
        self.assertEqual(events, [(IDENTITY, {'actmp': '25', 'deviceId': IDENTITY})])
        self.assertEqual(sock.sent, [], 'Passive receiving must not send control packets')
        transport.close()

    def test_wrong_mac_discovery_does_not_learn_ip(self):
        wrong = local.encrypt('DISC_RSP_FFFFFFFFFFFF', KEY, IV)
        transport, _sock, events = setup_transport([(wrong, ('192.0.2.25', 5001))])
        transport._receive()
        self.assertIsNone(transport.devices[IDENTITY]['ip'])
        self.assertFalse(transport.devices[IDENTITY]['found'].is_set())
        self.assertEqual(events, [])
        transport.close()

    def test_close_releases_socket_thread_and_in_memory_keys(self):
        transport, sock, _events = setup_transport()
        thread = transport.thread
        transport.close()
        self.assertFalse(transport.running)
        self.assertIsNone(transport.socket)
        self.assertEqual(transport.devices, {})
        self.assertTrue(sock.closed)
        thread.join.assert_called_once_with(timeout=2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
