#!/usr/bin/python3
"""GTK menu smoke tests with fake indicators/proxies, no service or device IO."""
import importlib.util
import json
from pathlib import Path
import unittest
from types import SimpleNamespace

SPEC = importlib.util.spec_from_file_location('miraie_tray_test', Path(__file__).resolve().parents[1] / 'backend' / 'tray.py')
tray = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tray)
Gtk, GLib = tray.Gtk, tray.GLib


def state():
    return {'connection': 'ready', 'busy': False, 'error': '', 'devices': [{
        'id': 'ac-1', 'name': 'Living room AC', 'room': 'Living room', 'model': 'Synthetic model',
        'online': True, 'state': {'power': True, 'temperature': 24, 'roomTemperature': 26.5,
                                 'mode': 'cool', 'fan': 'auto', 'display': False},
        'capabilities': {'modes': ['cool', 'dry'], 'fanModes': ['auto', 'high'],
                         'temperature': {'min': 16, 'max': 30, 'step': 1}, 'verticalSwing': [],
                         'horizontalSwing': [], 'capacity': [], 'display': True, 'nanoe': False}}]}


class Indicator:
    def __init__(self):
        self.label = ''
        self.status = None
    def set_title(self, _title): pass
    def set_menu(self, _menu): pass
    def set_status(self, status): self.status = status
    def set_label(self, label, _guide): self.label = label


class Proxy:
    def __init__(self):
        self.calls = []
        self.callbacks = []
    def call(self, method, arguments, _flags, _timeout, _cancellable, callback):
        self.calls.append((method, arguments.unpack() if arguments else None))
        self.callbacks.append(callback)
    def call_finish(self, result): return result
    def reply(self, signature, values):
        self.callbacks.pop(0)(self, GLib.Variant(signature, values))


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.quit_count = 0
        def quit_application(): self.quit_count += 1
        self.proxy = Proxy()
        self.indicator = Indicator()
        self.panel = tray.Panel(SimpleNamespace(quit=quit_application), proxy=self.proxy, indicator=self.indicator, start=False)
        self.panel.accept(json.dumps(state()))
    def tearDown(self):
        self.panel.close()

    def test_render_is_read_only_and_menu_model_is_stable(self):
        power, target, mode = self.panel.power, self.panel.target, self.panel.choices['mode'][0]
        for _ in range(5): self.panel.accept(json.dumps(state()))
        self.assertEqual(self.proxy.calls, [])
        self.assertIs(self.panel.power, power)
        self.assertIs(self.panel.target, target)
        self.assertIs(self.panel.choices['mode'][0], mode)
        self.assertTrue(power.get_active())
        self.assertEqual(target.get_label(), 'Target temperature: 24 °C')
        self.assertEqual(self.indicator.label, '24°')

    def test_offline_cached_power_is_not_shown_as_current_on(self):
        payload = state()
        payload['devices'][0]['online'] = False
        self.panel.accept(json.dumps(payload))
        self.assertFalse(self.panel.power.get_visible())
        self.assertTrue(self.panel.power_unavailable.get_visible())
        self.assertEqual(self.panel.power_unavailable.get_label(), 'Power: Unknown (AC offline)')
        self.assertFalse(self.panel.power_unavailable.get_sensitive())
        self.assertEqual(self.panel.target.get_label(), 'Last known target: 24 °C')
        self.assertEqual(self.indicator.label, '')
        self.assertEqual(self.proxy.calls, [])

    def test_step_sends_typed_command_and_keeps_confirmed_temperature(self):
        self.panel.plus.activate()
        self.assertEqual(self.proxy.calls, [('Command', ('ac-1', 'temperature', '25'))])
        self.assertIsNotNone(self.panel.pending)
        self.assertEqual(self.panel.target.get_label(), 'Target temperature: 24 °C')
        self.assertFalse(self.panel.power.get_sensitive())
        self.proxy.reply('(s)', ('job-1',))
        self.assertEqual(self.proxy.calls[-1][0], 'GetState')
        self.proxy.reply('(s)', (json.dumps(state()),))
        self.assertIsNotNone(self.panel.pending, 'A command acknowledgement must not claim a device response')
        confirmed = state(); confirmed['devices'][0]['state']['temperature'] = 25
        self.panel.accept(json.dumps(confirmed))
        self.assertIsNone(self.panel.pending)
        self.assertEqual(self.panel.pending_timer, 0)
        self.assertEqual(self.panel.target.get_label(), 'Target temperature: 25 °C')

    def test_power_uses_confirmed_switch_until_status_changes(self):
        self.panel.power.set_active(False)
        self.assertEqual(self.proxy.calls, [('Command', ('ac-1', 'power', 'false'))])
        self.assertTrue(self.panel.power.get_active(), 'Pending requests cannot be displayed as confirmed off')
        confirmed = state(); confirmed['devices'][0]['state']['power'] = False
        self.panel.accept(json.dumps(confirmed))
        self.assertFalse(self.panel.power.get_active())

    def test_offline_busy_and_absent_values_disable_controls(self):
        for connection, online, busy in [('offline', True, False), ('ready', False, False), ('ready', True, True)]:
            payload = state(); payload.update(connection=connection, busy=busy); payload['devices'][0]['online'] = online
            self.panel.accept(json.dumps(payload))
            self.assertFalse(self.panel.power.get_sensitive())
            self.assertFalse(self.panel.plus.get_sensitive())
            self.panel.command('temperature', 25)
        self.assertEqual(self.proxy.calls, [])
        payload = state(); payload['devices'][0]['state'].update(power=None, temperature=None)
        self.panel.accept(json.dumps(payload))
        self.assertFalse(self.panel.power.get_sensitive())
        self.assertEqual(self.panel.target.get_label(), 'Target temperature: Unavailable')

    def test_optional_controls_and_option_changes_keep_menu_widgets(self):
        self.assertTrue(self.panel.switches['display'].get_visible())
        self.assertFalse(self.panel.switches['nanoe'].get_visible())
        self.assertFalse(self.panel.choices['capacity'][0].get_visible())
        cool = self.panel.choice_items['mode']['cool']
        payload = state(); payload['devices'][0]['capabilities']['modes'] = ['cool']
        self.panel.accept(json.dumps(payload))
        self.assertIs(self.panel.choice_items['mode']['cool'], cool)
        self.assertFalse(self.panel.choice_items['mode']['dry'].get_visible())
        self.assertEqual(self.proxy.calls, [])

    def test_unsupported_values_and_temperature_bounds_are_rejected(self):
        for field, value in [('temperature', 31), ('temperature', 24.5), ('mode', 'heat'), ('nanoe', True), ('power', 1)]:
            self.panel.command(field, value)
        self.assertEqual(self.proxy.calls, [])

    def test_device_selector_changes_selection_without_commands(self):
        payload = state()
        second = json.loads(json.dumps(payload['devices'][0])); second.update(id='ac-2', name='Bedroom AC')
        payload['devices'].append(second)
        self.panel.accept(json.dumps(payload))
        self.assertTrue(self.panel.devices.get_visible())
        self.panel.device_items['ac-2'].activate()
        self.assertEqual(self.panel.current['id'], 'ac-2')
        self.assertEqual(self.proxy.calls, [])

    def test_old_getstate_response_cannot_overwrite_new_status(self):
        self.panel.get_state()
        fresh = state(); fresh['devices'][0]['state']['temperature'] = 27
        self.panel.accept(json.dumps(fresh))
        self.proxy.reply('(s)', (json.dumps(state()),))
        self.assertEqual(self.panel.target.get_label(), 'Target temperature: 27 °C')

    def test_malformed_status_disables_writes(self):
        self.panel.accept('{invalid')
        self.assertEqual(self.panel.state['connection'], 'error')
        self.assertFalse(self.panel.power.get_sensitive())
        self.assertTrue(self.panel.error.get_visible())
        self.assertEqual(self.proxy.calls, [])

    def test_close_cancels_callbacks_timers_and_indicator(self):
        self.panel.command('temperature', 25)
        self.panel.close()
        self.assertEqual(self.panel.pending_timer, 0)
        self.assertTrue(self.panel.cancellable.is_cancelled())
        self.assertEqual(self.indicator.status, tray.AyatanaAppIndicator3.IndicatorStatus.PASSIVE)
        self.proxy.reply('(s)', ('job-1',))
        self.assertEqual(len(self.proxy.calls), 1, 'Late callbacks must not fetch status or update destroyed widgets')

    def test_tray_quits_only_for_an_enabled_native_extension(self):
        self.panel.native_proxy = self.proxy
        self.panel.check_native()
        self.proxy.reply('(a{sv})', ({'state': GLib.Variant('d', 2)},))
        self.assertEqual(self.quit_count, 0)
        self.panel.check_native()
        self.proxy.reply('(a{sv})', ({'state': GLib.Variant('d', 1)},))
        self.assertEqual(self.quit_count, 1)


if __name__ == '__main__':
    available, _args = Gtk.init_check([])
    if not available: raise SystemExit('A GTK display is required for menu smoke tests')
    unittest.main(verbosity=2)
