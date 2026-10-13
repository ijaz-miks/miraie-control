"""Protocol and transaction tests. No real network, credentials or AC commands."""
from __future__ import annotations

import copy
import importlib.util
import io
import json
from pathlib import Path
import queue
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('miraie_core_under_test', Path(__file__).resolve().parents[1] / 'backend' / 'core.py')
core = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(core)


def device(**overrides):
    result = {'id': 'ac/test?x=1', 'name': 'Test AC', 'room': 'Test room', 'model': 'Test model',
              'online': True, 'updatedAt': time.time(),
              'state': {'power': True, 'display': False, 'nanoe': False, 'temperature': 24.0,
                        'mode': 'cool', 'fan': 'auto', 'verticalSwing': '0', 'horizontalSwing': '0', 'capacity': '100'},
              'capabilities': {'modes': ['cool', 'dry'], 'fanModes': ['auto', 'high'],
                               'temperature': {'min': 16, 'max': 30, 'step': 1},
                               'verticalSwing': ['0', '1', '2'], 'horizontalSwing': ['0', '1'],
                               'nanoe': True, 'display': True, 'capacity': ['40', '60', '100']}}
    result.update(overrides)
    return result


class FakeAPI:
    def __init__(self, status=None):
        self.credentials = ('test@example.invalid', 'synthetic-password')
        self.token = 'synthetic-token'
        self.expiry = float('inf')
        self.status_value = status or {'ps': 'on', 'actmp': '24', 'acmd': 'cool'}
        self.status_calls = []
        self.control_calls = []

    def status(self, identity):
        self.status_calls.append(identity)
        return copy.deepcopy(self.status_value)

    def control(self, identity, payload):
        self.control_calls.append((identity, copy.deepcopy(payload)))
        return {'accepted': True}


class FakePublish:
    def __init__(self, rc=0, acknowledged=True):
        self.rc = rc
        self.acknowledged = acknowledged
        self.wait_calls = []

    def wait_for_publish(self, timeout):
        self.wait_calls.append(timeout)

    def is_published(self):
        return self.acknowledged


class FakeMQTT:
    def __init__(self, result=None, on_publish=None):
        self.result = result or FakePublish()
        self.on_publish = on_publish
        self.published = []

    def is_connected(self):
        return True

    def publish(self, topic, payload, qos, retain):
        self.published.append((topic, json.loads(payload), qos, retain))
        if self.on_publish:
            self.on_publish()
        return self.result


class VirtualClock:
    def __init__(self):
        self.current = 0.0

    def monotonic(self):
        return self.current


class VirtualCondition:
    """Real reentrant lock with deterministic waiting and optional status callback."""
    def __init__(self, clock, callback=None):
        self.condition = threading.Condition(threading.RLock())
        self.clock = clock
        self.callback = callback
        self.wait_count = 0

    def __enter__(self):
        self.condition.__enter__()
        return self

    def __exit__(self, *args):
        return self.condition.__exit__(*args)

    def notify_all(self):
        self.condition.notify_all()

    def wait(self, timeout):
        self.wait_count += 1
        self.clock.current += timeout
        if self.callback:
            callback, self.callback = self.callback, None
            callback()
        return True


def controller_fixture(api=None, lock=None):
    """Initialize the transaction state without launching the background worker."""
    controller = core.Controller.__new__(core.Controller)
    controller.credentials = lambda: None
    controller.changed = lambda state: controller.events.append(copy.deepcopy(state))
    controller.events = []
    controller.api = api or FakeAPI()
    controller.mqtt_factory = None
    controller.local_factory = None
    controller.local = None
    controller.lock = lock or threading.Condition(threading.RLock())
    controller.tasks = queue.Queue()
    controller.clients = {}
    item = device()
    controller.internal = {item['id']: {'home': 'test-home', 'topic': 'test/topic',
                                      'detail': {}, 'raw': {'ps': 'on', 'actmp': '24', 'acmd': 'cool'}}}
    controller.sequence = {item['id']: 1}
    controller.field_sequence = {item['id']: {field: 1 for field in core.FIELDS}}
    controller.epoch = 0
    controller.running = True
    controller.state = {'connection': 'ready', 'busy': False, 'error': '', 'devices': [item]}
    return controller


class PayloadTests(unittest.TestCase):
    def test_power_and_display_use_distinct_fields(self):
        for field, wire in [('power', 'ps'), ('display', 'acdc')]:
            with self.subTest(field=field):
                payload = core.control_payload(field, False, device())
                self.assertEqual(payload[wire], 'off')
                self.assertNotIn('acdc' if wire == 'ps' else 'ps', payload)
                self.assertEqual(set(payload), {'ki', 'cnt', 'sid', wire})

    def test_capability_key_indices_match_rea_protocol(self):
        expected = {'power': (False, 1), 'mode': ('dry', 0), 'temperature': (25, 2),
                    'fan': ('high', 4), 'verticalSwing': ('2', 5), 'horizontalSwing': ('1', 6),
                    'display': (True, 8), 'capacity': ('60', 28)}
        for field, (value, key_index) in expected.items():
            with self.subTest(field=field):
                self.assertEqual(core.control_payload(field, value, device())['ki'], key_index)
        item = device()
        item['capabilities']['nanoe'] = True
        self.assertEqual(core.control_payload('nanoe', True, item)['ki'], 7)
        self.assertFalse(core.capabilities({'acng': 'on'}, {})['nanoe'],
                         'Hide nanoe until the model declares the matching capability')

    def test_boolean_controls_reject_integer_and_string_values(self):
        for value in (0, 1, 'on', 'off', None):
            with self.subTest(value=value):
                with self.assertRaises(core.MirAIeError):
                    core.control_payload('power', value, device())

    def test_enum_and_model_capabilities_are_enforced(self):
        for field, value in [('mode', 'heat'), ('fan', 'turbo'), ('verticalSwing', '9'),
                             ('horizontalSwing', 1), ('capacity', '110'), ('unknown', True)]:
            with self.subTest(field=field, value=value):
                with self.assertRaises(core.MirAIeError):
                    core.control_payload(field, value, device())
        item = device()
        item['capabilities']['nanoe'] = False
        with self.assertRaises(core.MirAIeError):
            core.control_payload('nanoe', True, item)
        item['state']['display'] = None
        with self.assertRaises(core.MirAIeError):
            core.control_payload('display', False, item)

    def test_temperature_bounds_steps_and_nonfinite_values(self):
        for value in (15, 31, 24.5, True, None, float('inf'), float('nan'), 'invalid'):
            with self.subTest(value=value):
                with self.assertRaises(core.MirAIeError):
                    core.control_payload('temperature', value, device())
        self.assertEqual(core.control_payload('temperature', 25, device())['actmp'], '25')
        item = device()
        item['capabilities']['temperature']['step'] = 0.5
        self.assertEqual(core.control_payload('temperature', 24.5, item)['actmp'], '24.5')

    def test_swing_and_capacity_transport_types(self):
        self.assertEqual(core.control_payload('verticalSwing', '2', device())['acvs'], 2)
        payload = core.control_payload('capacity', '60', device())
        self.assertEqual(payload['cnv'], 60)
        self.assertEqual((payload['acem'], payload['acpm']), ('off', 'off'))
        self.assertNotIn('ps', payload)


class NormalizationTests(unittest.TestCase):
    def test_firmware_specific_room_temperature_decoding(self):
        self.assertEqual(core.room_temperature('128.25', '3.2.1'), 25.5)
        self.assertEqual(core.room_temperature('64.27', 'V3.3'), 27.25)
        self.assertEqual(core.room_temperature('25.5', '3.1'), 25.5)
        self.assertEqual(core.room_temperature('25.5', ''), 25.5)
        self.assertIsNone(core.room_temperature('128.25', '3.1'))
        for raw in ('unknown', None, 'nan', 'inf', '-100'):
            with self.subTest(raw=raw):
                self.assertIsNone(core.room_temperature(raw))

    def test_power_and_display_status_do_not_alias(self):
        state = core.normalize_status({'ps': 'off', 'acdc': 'on'})
        self.assertEqual(state, {'power': False, 'display': True})
        self.assertNotIn('temperature', state)
        self.assertIsNone(core.normalize_status({'ps': 'unknown'})['power'])

    def test_wrapped_status_and_integer_swing_values(self):
        state = core.normalize_status({'data': {'status': {'actmp': '24.5', 'acvs': 2}}})
        self.assertEqual(state, {'temperature': 24.5, 'verticalSwing': '2'})
        with self.assertRaises(core.MirAIeError):
            core.normalize_status([])

    def test_unproven_advanced_features_are_unavailable(self):
        caps = core.capabilities({'actmp': '24', 'cnv': 60}, {})
        self.assertEqual(caps['capacity'], [])
        self.assertFalse(caps['nanoe'])
        self.assertFalse(caps['display'])
        self.assertEqual(caps['modes'], [])
        declared = core.capabilities({'cnv': 60}, {'supportedCapacity': [40, 60, 100]})
        self.assertEqual(declared['capacity'], ['40', '60', '100'])


class CloudRequestTests(unittest.TestCase):
    def test_status_device_identity_is_one_encoded_path_segment(self):
        requests = []
        class Opener:
            def open(self, request, timeout):
                requests.append((request, timeout))
                return io.BytesIO(b'{"ps":"on"}')
        api = core.CloudAPI(opener=Opener())
        api.token = 'synthetic-token'
        identity = 'ac/a?mode=on#fragment,% snow'
        api.status(identity)
        request, timeout = requests[0]
        self.assertEqual(request.full_url, core.API_BASE + '/deviceManagement/devices/ac%2Fa%3Fmode%3Don%23fragment%2C%25%20snow/mobile/status')
        self.assertEqual(request.get_method(), 'GET')
        self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic-token')
        self.assertEqual(timeout, 12)

    def test_https_control_keeps_identity_out_of_url(self):
        requests = []
        class Opener:
            def open(self, request, timeout):
                requests.append(request)
                return io.BytesIO(b'{}')
        api = core.CloudAPI(opener=Opener())
        api.token = 'synthetic-token'
        api.control('ac/test?x=1', {'ps': 'off'})
        self.assertEqual(requests[0].full_url, core.API_BASE + '/deviceManagement/control')
        self.assertEqual(json.loads(requests[0].data), {'deviceId': 'ac/test?x=1', 'control_params': {'ps': 'off'}})


class ReachabilityTests(unittest.TestCase):
    def test_rest_without_online_evidence_does_not_make_device_online(self):
        controller = controller_fixture()
        controller.state['devices'][0]['online'] = False
        controller._update('ac/test?x=1', {'ps': 'on', 'actmp': '25'}, source='rest')
        self.assertFalse(controller.snapshot()['devices'][0]['online'])
        self.assertEqual(controller.snapshot()['devices'][0]['state']['temperature'], 25)

    def test_mqtt_telemetry_does_not_override_connection_status(self):
        controller = controller_fixture()
        controller.state['devices'][0]['online'] = False
        controller._update('ac/test?x=1', {'actmp': '25'}, source='mqtt')
        self.assertFalse(controller.snapshot()['devices'][0]['online'])
        controller._connection_status('ac/test?x=1', {'onlineStatus': 'on'})
        self.assertTrue(controller.snapshot()['devices'][0]['online'])
        controller._update('ac/test?x=1', {'ps': 'off', 'onlineStatus': 'offline'}, source='mqtt')
        self.assertTrue(controller.snapshot()['devices'][0]['online'])
        controller._connection_status('ac/test?x=1', {'onlineStatus': 'unknown'})
        self.assertTrue(controller.snapshot()['devices'][0]['online'])

    def test_offline_connection_then_retained_on_status_stays_offline(self):
        controller = controller_fixture()
        controller._update('ac/test?x=1', {'onlineStatus': 'false', 'ps': 'on'}, source='rest')
        controller._connection_status('ac/test?x=1', {'onlineStatus': 'false'}, retained=True)
        controller._update('ac/test?x=1', {'ps': 'on', 'actmp': '26', 'ts': '1790953064'}, source='mqtt', retained=True)
        item = controller.snapshot()['devices'][0]
        self.assertFalse(item['online'])
        self.assertIsNone(item['state']['power'], 'Offline ACs have no confirmed current power state')
        self.assertTrue(item['lastKnownPower'])
        self.assertTrue(controller.state['devices'][0]['state']['power'], 'Preserve the old setting only in the internal cache')
        with self.assertRaises(core.MirAIeError):
            controller.command(item['id'], 'temperature', 25)

    def test_retained_telemetry_cannot_replace_fresh_settings_or_renew_freshness(self):
        controller = controller_fixture()
        controller._update('ac/test?x=1', {'onlineStatus': 'true', 'ps': 'off', 'actmp': '27'}, source='rest')
        original = controller.snapshot()['devices'][0]
        generation = controller.field_sequence['ac/test?x=1']['temperature']
        controller._update('ac/test?x=1', {'ps': 'on', 'actmp': '26'}, source='mqtt', retained=True)
        item = controller.snapshot()['devices'][0]
        self.assertEqual(item['state']['temperature'], 27)
        self.assertFalse(item['state']['power'])
        self.assertEqual(item['updatedAt'], original['updatedAt'])
        self.assertEqual(controller.field_sequence['ac/test?x=1']['temperature'], generation)

    def test_retained_positive_connection_cannot_revive_offline_device(self):
        controller = controller_fixture()
        controller._update('ac/test?x=1', {'onlineStatus': 'false'}, source='rest')
        controller._connection_status('ac/test?x=1', {'onlineStatus': 'true'}, retained=True)
        self.assertFalse(controller.snapshot()['devices'][0]['online'])
        controller._connection_status('ac/test?x=1', {'onlineStatus': 'true'}, retained=False)
        self.assertTrue(controller.snapshot()['devices'][0]['online'])

    def test_local_keys_and_iv_are_never_in_public_snapshots(self):
        controller = controller_fixture()
        key = 'a' * 32
        iv = 'b' * 32
        controller.internal['ac/test?x=1']['detail'].update(udpControlKey=key, udpcontrolIV=iv,
                                                         macAddress='01:23:45:67:89:AB')
        controller._update('ac/test?x=1', {'actmp': '25'}, source='local')
        public = json.dumps(controller.snapshot())
        for secret in ('udpControlKey', 'udpcontrolIV', key, iv):
            self.assertNotIn(secret, public)

    def test_snapshots_cannot_mutate_confirmed_device_state(self):
        controller = controller_fixture()
        snapshot = controller.snapshot()
        snapshot['devices'][0]['state']['power'] = False
        snapshot['devices'][0]['capabilities']['modes'].append('heat')
        self.assertTrue(controller.state['devices'][0]['state']['power'])
        self.assertNotIn('heat', controller.state['devices'][0]['capabilities']['modes'])


class TransactionTests(unittest.TestCase):
    def test_broker_publish_ack_is_not_device_confirmation(self):
        clock = VirtualClock()
        controller = controller_fixture(lock=VirtualCondition(clock))
        mqtt = FakeMQTT()
        controller.clients['test-home'] = mqtt
        with patch.object(core.time, 'monotonic', clock.monotonic):
            with self.assertRaisesRegex(core.MirAIeError, 'did not confirm'):
                controller._command('ac/test?x=1', 'temperature', 25, {'actmp': '25'})
        self.assertEqual(len(mqtt.published), 1)
        self.assertEqual(mqtt.result.wait_calls, [4])
        self.assertEqual(controller.api.control_calls, [], 'Uncertain MQTT writes must not be repeated over HTTPS')
        self.assertEqual(controller.state['devices'][0]['state']['temperature'], 24)

    def test_unacknowledged_publish_has_no_https_retry(self):
        controller = controller_fixture()
        mqtt = FakeMQTT(FakePublish(acknowledged=False))
        controller.clients['test-home'] = mqtt
        with self.assertRaisesRegex(core.MirAIeError, 'not acknowledged'):
            controller._command('ac/test?x=1', 'power', False, {'ps': 'off'})
        self.assertEqual(len(mqtt.published), 1)
        self.assertEqual(controller.api.control_calls, [])
        self.assertEqual(controller.api.status_calls, [])

    def test_matching_mqtt_response_confirms_immediately(self):
        controller = controller_fixture()
        mqtt = FakeMQTT(on_publish=lambda: controller._update('ac/test?x=1', {'actmp': '25'}, source='mqtt'))
        controller.clients['test-home'] = mqtt
        controller._command('ac/test?x=1', 'temperature', 25, {'actmp': '25'})
        self.assertEqual(controller.state['devices'][0]['state']['temperature'], 25)
        self.assertEqual(controller.api.status_calls, [])
        self.assertEqual(mqtt.published[0][2:], (1, False))

    def test_mqtt_confirmation_during_wait_is_not_overwritten_by_stale_rest(self):
        clock = VirtualClock()
        condition = VirtualCondition(clock)
        controller = controller_fixture(lock=condition)
        controller.clients['test-home'] = FakeMQTT()
        condition.callback = lambda: controller._update('ac/test?x=1', {'actmp': '25'}, source='mqtt')
        with patch.object(core.time, 'monotonic', clock.monotonic):
            controller._command('ac/test?x=1', 'temperature', 25, {'actmp': '25'})
        self.assertEqual(controller.state['devices'][0]['state']['temperature'], 25)
        self.assertEqual(controller.api.status_calls, [], 'Check MQTT state after waking before fetching older REST state')

    def test_explicitly_offline_rest_status_cannot_confirm_device_change(self):
        clock = VirtualClock()
        api = FakeAPI(status={'onlineStatus': 'off', 'actmp': '25'})
        controller = controller_fixture(api=api, lock=VirtualCondition(clock))
        with patch.object(core.time, 'monotonic', clock.monotonic):
            with self.assertRaises(core.MirAIeError):
                controller._command('ac/test?x=1', 'temperature', 25, {'actmp': '25'})
        self.assertFalse(controller.state['devices'][0]['online'])
        self.assertEqual(len(api.control_calls), 1)

    def test_unrelated_partial_status_cannot_reconfirm_cached_field(self):
        clock = VirtualClock()
        api = FakeAPI(status={'onlineStatus': 'on', 'rmtmp': '27'})
        controller = controller_fixture(api=api, lock=VirtualCondition(clock))
        controller.state['devices'][0]['state']['temperature'] = 25
        controller.internal['ac/test?x=1']['raw']['actmp'] = '25'
        controller.clients['test-home'] = FakeMQTT()
        with patch.object(core.time, 'monotonic', clock.monotonic):
            with self.assertRaisesRegex(core.MirAIeError, 'did not confirm'):
                controller._command('ac/test?x=1', 'temperature', 25, {'actmp': '25'})
        self.assertGreater(controller.sequence['ac/test?x=1'], 1)
        self.assertEqual(controller.field_sequence['ac/test?x=1']['temperature'], 1)

    def test_refresh_reserves_pending_work_before_worker_dequeues(self):
        controller = controller_fixture()
        controller.refresh()
        self.assertTrue(controller.state['busy'])
        self.assertEqual(controller.tasks.qsize(), 1)
        controller.refresh()
        self.assertEqual(controller.tasks.qsize(), 1, 'Do not queue duplicate refreshes while pending')
        with self.assertRaises(core.MirAIeError):
            controller.command('ac/test?x=1', 'power', False)
        self.assertEqual(controller.tasks.qsize(), 1)

    def test_worker_keeps_busy_reserved_until_all_queued_work_finishes(self):
        controller = controller_fixture()
        controller.state['busy'] = True
        controller.tasks.put(('refresh', None))
        controller.tasks.put(('command', ('ac/test?x=1', 'power', False, {'ps': 'off'})))
        seen = []
        controller._refresh = lambda: seen.append(('refresh', controller.state['busy']))
        def execute_command(*_args):
            seen.append(('command', controller.state['busy']))
            controller.running = False
        controller._command = execute_command
        controller._run()
        self.assertEqual(seen, [('refresh', True), ('command', True)])
        self.assertTrue(controller.events[0]['busy'], 'Finishing the first task must not release another queued transaction')
        self.assertFalse(controller.events[-1]['busy'])

    def test_uncertain_local_send_does_not_retry_mqtt_or_https(self):
        clock = VirtualClock()
        controller = controller_fixture(lock=VirtualCondition(clock))
        sent = []
        controller.local = SimpleNamespace(send=lambda identity, payload: sent.append((identity, copy.deepcopy(payload))) or True)
        mqtt = FakeMQTT()
        controller.clients['test-home'] = mqtt
        with patch.object(core.time, 'monotonic', clock.monotonic):
            with self.assertRaisesRegex(core.MirAIeError, 'did not confirm'):
                controller._command('ac/test?x=1', 'temperature', 25, {'actmp': '25'})
        self.assertEqual(len(sent), 1)
        self.assertEqual(mqtt.published, [])
        self.assertEqual(controller.api.control_calls, [])
        self.assertEqual(controller.api.status_calls, [], 'Local confirmation cannot be replaced by cached cloud status')

    def test_local_response_must_confirm_the_requested_field(self):
        clock = VirtualClock()
        controller = controller_fixture(lock=VirtualCondition(clock))
        def local_send(_identity, _payload):
            controller._update('ac/test?x=1', {'ps': 'off', 'actmp': '24'}, source='local')
            return True
        controller.local = SimpleNamespace(send=local_send)
        with patch.object(core.time, 'monotonic', clock.monotonic):
            with self.assertRaisesRegex(core.MirAIeError, 'did not confirm'):
                controller._command('ac/test?x=1', 'temperature', 25, {'actmp': '25'})
        self.assertEqual(controller.state['devices'][0]['state']['temperature'], 24)
        self.assertEqual(controller.api.control_calls, [])

    def test_matching_local_response_confirms_without_cloud_control(self):
        controller = controller_fixture()
        def local_send(_identity, _payload):
            controller._update('ac/test?x=1', {'actmp': '25'}, source='local')
            return True
        controller.local = SimpleNamespace(send=local_send)
        controller._command('ac/test?x=1', 'temperature', 25, {'actmp': '25'})
        self.assertEqual(controller.state['devices'][0]['state']['temperature'], 25)
        self.assertEqual(controller.api.status_calls, [])
        self.assertEqual(controller.api.control_calls, [])

    def test_unsent_local_discovery_allows_exactly_one_cloud_fallback(self):
        clock = VirtualClock()
        controller = controller_fixture(lock=VirtualCondition(clock))
        controller.local = SimpleNamespace(send=lambda _identity, _payload: False)
        mqtt = FakeMQTT(on_publish=lambda: controller._update('ac/test?x=1', {'actmp': '25'}, source='mqtt'))
        controller.clients['test-home'] = mqtt
        with patch.object(core.time, 'monotonic', clock.monotonic):
            controller._command('ac/test?x=1', 'temperature', 25, {'actmp': '25'})
        self.assertEqual(len(mqtt.published), 1)
        self.assertEqual(controller.api.control_calls, [])

    def test_stale_or_offline_status_rejects_writes_before_publication(self):
        for updated, online in [(time.time() - 181, True), (time.time(), False)]:
            with self.subTest(updated=updated, online=online):
                controller = controller_fixture()
                controller.state['devices'][0].update(updatedAt=updated, online=online)
                with self.assertRaises(core.MirAIeError):
                    controller.command('ac/test?x=1', 'power', False)
                self.assertTrue(controller.tasks.empty())
                self.assertFalse(controller.state['busy'])

    def test_concurrent_command_intents_reserve_one_pending_transaction(self):
        controller = controller_fixture()
        gate = threading.Barrier(4)
        outcomes = []
        results_lock = threading.Lock()
        def submit():
            gate.wait(timeout=2)
            try:
                result = ('accepted', controller.command('ac/test?x=1', 'power', False))
            except core.MirAIeError:
                result = ('rejected', None)
            with results_lock:
                outcomes.append(result)
        workers = [threading.Thread(target=submit) for _ in range(3)]
        for worker in workers:
            worker.start()
        gate.wait(timeout=2)
        for worker in workers:
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
        self.assertEqual([kind for kind, _ in outcomes].count('accepted'), 1)
        self.assertEqual([kind for kind, _ in outcomes].count('rejected'), 2)
        self.assertEqual(controller.tasks.qsize(), 1)
        self.assertTrue(controller.state['busy'])
        kind, args = controller.tasks.get_nowait()
        self.assertEqual(kind, 'command')
        self.assertEqual(args[:3], ('ac/test?x=1', 'power', False))
        self.assertTrue(controller.events[-1]['busy'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
