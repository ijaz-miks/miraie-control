#!/usr/bin/python3
"""Active-session AC tray controls until the native Shell extension is loaded."""
from __future__ import annotations

import json
import math
import os
import sys

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('AyatanaAppIndicator3', '0.1')
from gi.repository import AyatanaAppIndicator3, Gio, GLib, Gtk

BUS = 'io.github.ijaz.Miraie'
PATH = '/io/github/ijaz/Miraie'
INTERFACE = BUS + '.Controller1'
NATIVE_UUID = 'miraie-control@ijaz.local'
CONNECTIONS = {'signed-out', 'connecting', 'ready', 'error', 'offline'}
OPTIONS = {'mode': 'modes', 'fan': 'fanModes', 'verticalSwing': 'verticalSwing',
           'horizontalSwing': 'horizontalSwing', 'capacity': 'capacity'}
TITLES = {'power': 'Power', 'temperature': 'Target temperature', 'mode': 'Mode', 'fan': 'Fan speed',
          'verticalSwing': 'Vertical swing', 'horizontalSwing': 'Horizontal swing', 'nanoe': 'nanoe',
          'display': 'Display light', 'capacity': 'Capacity'}
LABELS = {'auto': 'Auto', 'cool': 'Cool', 'dry': 'Dry', 'fan': 'Fan only', 'heat': 'Heat',
          'low': 'Low', 'medium': 'Medium', 'high': 'High', 'quiet': 'Quiet', 'off': 'Off', 'on': 'On',
          '0': 'Automatic swing', '1': 'Position 1', '2': 'Position 2', '3': 'Position 3',
          '4': 'Position 4', '5': 'Position 5'}


def clean_text(value, limit=300):
    return ''.join(character if character >= ' ' and character != '\x7f' else ' ' for character in str(value))[:limit]


def label(value, field=None):
    if field == 'capacity' and str(value).isdigit():
        return 'Off' if str(value) == '0' else str(value) + '%'
    return LABELS.get(str(value), clean_text(value).replace('_', ' ').replace('-', ' ').capitalize())


def parse_state(payload):
    if not isinstance(payload, str) or len(payload) > 262144:
        raise ValueError('Invalid controller response')
    result = json.loads(payload)
    if not isinstance(result, dict) or result.get('connection') not in CONNECTIONS or type(result.get('busy')) is not bool:
        raise ValueError('Invalid controller response')
    devices = result.get('devices')
    if not isinstance(devices, list) or len(devices) > 100:
        raise ValueError('Invalid device response')
    identities = set()
    for device in devices:
        if not isinstance(device, dict) or not isinstance(device.get('id'), str) or not device['id'] or device['id'] in identities:
            raise ValueError('Invalid device identity')
        identities.add(device['id'])
        if type(device.get('online')) is not bool or not isinstance(device.get('state'), dict) or not isinstance(device.get('capabilities'), dict):
            raise ValueError('Invalid device response')
        for field in ('power', 'nanoe', 'display'):
            if device['state'].get(field) is not None and type(device['state'][field]) is not bool:
                raise ValueError('Invalid device switch')
        for field in ('temperature', 'roomTemperature'):
            value = device['state'].get(field)
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or not -50 <= value <= 70):
                raise ValueError('Invalid device temperature')
        for field in OPTIONS:
            if device['state'].get(field) is not None and not isinstance(device['state'][field], str):
                raise ValueError('Invalid device setting')
        for key in OPTIONS.values():
            values = device['capabilities'].get(key, [])
            if not isinstance(values, list) or len(values) > 40 or any(not isinstance(item, str) or not item for item in values):
                raise ValueError('Invalid device capability')
        bounds = device['capabilities'].get('temperature')
        if bounds is not None and (not isinstance(bounds, dict) or
                                  any(type(bounds.get(key)) not in (int, float) or not math.isfinite(bounds[key]) for key in ('min', 'max', 'step')) or
                                  not 10 <= bounds['min'] <= bounds['max'] <= 45 or not 0 < bounds['step'] <= 5):
            raise ValueError('Invalid temperature range')
    return result


class Panel:
    def __init__(self, application, proxy=None, indicator=None, start=True):
        self.application = application
        self.proxy = proxy
        self.indicator = indicator or AyatanaAppIndicator3.Indicator.new(
            'miraie-ac-controls', 'weather-snow-symbolic', AyatanaAppIndicator3.IndicatorCategory.HARDWARE)
        self.indicator.set_title('MirAIe AC Controls')
        self.cancellable = Gio.Cancellable()
        self.dead = False
        self.rendering = False
        self.refreshing = False
        self.native_checking = False
        self.native_proxy = None
        self.proxy_signals = []
        self.widget_signals = []
        self.pending = None
        self.pending_timer = 0
        self.native_timer = 0
        self.revision = 0
        self.selected_id = ''
        self.current = None
        self.state = {'connection': 'connecting', 'busy': False, 'error': '', 'devices': []}
        self.device_items = {}
        self.choice_items = {field: {} for field in OPTIONS}
        self._build_menu()
        self.indicator.set_menu(self.menu)
        self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
        self.render()
        if start:
            Gio.DBusProxy.new_for_bus(Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES,
                                     None, BUS, PATH, INTERFACE, self.cancellable, self._connected)
            Gio.DBusProxy.new_for_bus(Gio.BusType.SESSION,
                                     Gio.DBusProxyFlags.DO_NOT_AUTO_START | Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES,
                                     None, 'org.gnome.Shell.Extensions', '/org/gnome/Shell/Extensions',
                                     'org.gnome.Shell.Extensions', self.cancellable, self._native_connected)
            self.native_timer = GLib.timeout_add_seconds(20, self.check_native)

    def _connect_widget(self, widget, signal, callback):
        def invoke(*args):
            if not self.dead:
                callback(*args)
        self.widget_signals.append((widget, widget.connect(signal, invoke)))

    def _item(self, title, callback=None, check=False):
        item = Gtk.CheckMenuItem(label=title) if check else Gtk.MenuItem(label=title)
        if callback:
            self._connect_widget(item, 'toggled' if check else 'activate', callback)
        self.menu.append(item)
        return item

    def _build_menu(self):
        self.menu = Gtk.Menu()
        self.heading = self._item('MirAIe AC Controls')
        self.heading.set_sensitive(False)
        self.status = self._item('Connecting')
        self.status.set_sensitive(False)
        self.devices = self._item('Air conditioner')
        self.device_menu = Gtk.Menu()
        self.devices.set_submenu(self.device_menu)
        self.info = self._item('')
        self.info.set_sensitive(False)
        self.menu.append(Gtk.SeparatorMenuItem())
        self.power = self._item('Power', lambda item: self.command('power', item.get_active()) if not self.rendering else None, check=True)
        self.power_unavailable = self._item('Power: Unavailable')
        self.power_unavailable.set_sensitive(False)
        self.target = self._item('Target temperature: Unavailable')
        self.target.set_sensitive(False)
        self.minus = self._item('Decrease temperature', lambda _item: self.step(-1))
        self.plus = self._item('Increase temperature', lambda _item: self.step(1))
        self.room = self._item('Room temperature: Unavailable')
        self.room.set_sensitive(False)
        self.choices = {}
        for field in OPTIONS:
            item = self._item(TITLES[field] + ': Unavailable')
            submenu = Gtk.Menu()
            item.set_submenu(submenu)
            self.choices[field] = (item, submenu)
        self.switches = {}
        for field in ('nanoe', 'display'):
            self.switches[field] = self._item(TITLES[field],
                lambda item, selected=field: self.command(selected, item.get_active()) if not self.rendering else None,
                check=True)
        self.error = self._item('')
        self.error.set_sensitive(False)
        self.menu.append(Gtk.SeparatorMenuItem())
        self.refresh_item = self._item('Refresh', lambda _item: self.refresh())
        self._item('Account Settings', lambda _item: self.open_settings())
        self._item('Quit panel', lambda _item: self.application.quit())
        self.menu.show_all()

    def _connected(self, _source, result):
        try:
            proxy = Gio.DBusProxy.new_for_bus_finish(result)
        except GLib.Error:
            self.fail('The AC service is unavailable. Open Account Settings to reconnect.')
            return
        if self.dead:
            return
        self.proxy = proxy
        self.proxy_signals.append(proxy.connect('g-signal', self._signal))
        self.proxy_signals.append(proxy.connect('notify::g-name-owner', self._owner_changed))
        self.get_state()

    def _owner_changed(self, proxy, _property):
        if self.dead:
            return
        if proxy.get_name_owner():
            self.get_state()
        else:
            self.fail('The AC service stopped. Open Account Settings to reconnect.')

    def _signal(self, _proxy, _sender, signal, parameters):
        if not self.dead and signal == 'StateChanged':
            self.accept(parameters.unpack()[0])

    def call(self, method, parameters=None, complete=None):
        if self.dead:
            return
        if not self.proxy:
            self.fail('The AC service is unavailable. Open Account Settings to reconnect.')
            return
        def finished(proxy, result):
            try:
                reply = proxy.call_finish(result).unpack()
                error = None
            except GLib.Error as failure:
                reply, error = None, failure
            if not self.dead and complete:
                complete(reply, error)
        self.proxy.call(method, parameters, Gio.DBusCallFlags.NONE, 15000, self.cancellable, finished)

    def get_state(self):
        revision = self.revision
        def complete(reply, error):
            if error:
                self.fail('Could not read AC status. Refresh to try again.')
            elif revision == self.revision:
                self.accept(reply[0])
        self.call('GetState', complete=complete)

    def accept(self, payload):
        if self.dead:
            return
        try:
            state = parse_state(payload)
        except (ValueError, TypeError, KeyError):
            self.fail('The AC service returned invalid status. Refresh to try again.')
            return
        self.revision += 1
        self.state = state
        if not state['busy']:
            self.refreshing = False
        if self.pending:
            pending = self.pending
            device = next((item for item in state['devices'] if item['id'] == pending['device']), None)
            pending['saw_busy'] = pending['saw_busy'] or state['busy']
            matches = device is not None and device['state'].get(pending['field']) == pending['value']
            if not state['busy'] and (matches or state.get('error') or state['connection'] != 'ready' or not device or not device['online'] or pending['saw_busy']):
                if not matches and not state.get('error') and state['connection'] == 'ready':
                    state['error'] = 'The AC did not confirm the requested setting. Refresh to check its state.'
                self._clear_pending()
        self.render()

    def fail(self, message):
        if self.dead:
            return
        self.revision += 1
        self.refreshing = False
        self._clear_pending()
        self.state = {**self.state, 'connection': 'error', 'busy': False, 'error': clean_text(message)}
        self.render()

    def _clear_pending(self):
        if self.pending_timer:
            GLib.source_remove(self.pending_timer)
        self.pending_timer = 0
        self.pending = None

    def _can_control(self):
        return (not self.dead and self.state['connection'] == 'ready' and not self.state['busy'] and
                not self.pending and not self.refreshing and self.current is not None and self.current['online'])

    def command(self, field, value):
        if self.rendering or not self._can_control():
            return
        device = self.current
        state, caps = device['state'], device['capabilities']
        if state.get(field) is None:
            return
        if field in ('power', 'nanoe', 'display'):
            valid = type(value) is bool and (field == 'power' or caps.get(field) is True)
        elif field == 'temperature':
            bounds = caps.get('temperature')
            valid = bool(bounds and type(value) in (int, float) and math.isfinite(value) and bounds['min'] <= value <= bounds['max'] and
                         math.isclose((value - bounds['min']) / bounds['step'], round((value - bounds['min']) / bounds['step']), abs_tol=0.00001))
        else:
            valid = field in OPTIONS and isinstance(value, str) and value in caps.get(OPTIONS[field], [])
        if not valid or state.get(field) == value:
            self.render()
            return
        self.pending = {'device': device['id'], 'field': field, 'value': value, 'saw_busy': False}
        self.state['error'] = ''
        self.render()
        self.pending_timer = GLib.timeout_add_seconds(30, self._timeout)
        def complete(_reply, error):
            if error:
                self.fail('The AC update could not be completed. Refresh to check its state.')
            else:
                self.get_state()
        self.call('Command', GLib.Variant('(sss)', (device['id'], field, json.dumps(value))), complete)

    def _timeout(self):
        self.pending_timer = 0
        if not self.dead and self.pending:
            self.pending = None
            self.state['error'] = 'Update sent; the AC did not confirm the requested setting. Refresh to check its state.'
            self.render()
        return GLib.SOURCE_REMOVE

    def step(self, direction):
        if not self._can_control():
            return
        bounds = self.current['capabilities'].get('temperature')
        value = self.current['state'].get('temperature')
        if bounds and value is not None:
            self.command('temperature', round(value + direction * bounds['step'], 2))

    def refresh(self):
        if self.dead or self.pending or self.state['busy'] or self.refreshing:
            return
        self.refreshing = True
        self.state['error'] = ''
        self.render()
        def complete(_reply, error):
            if error:
                self.fail('Could not refresh AC status. Check your Internet connection.')
            else:
                self.get_state()
        self.call('Refresh', complete=complete)

    def open_settings(self):
        try:
            launcher = os.path.join(GLib.get_home_dir(), '.local', 'bin', 'miraie-control-settings')
            app = Gio.AppInfo.create_from_commandline(GLib.shell_quote(launcher), 'MirAIe Account Settings', Gio.AppInfoCreateFlags.NONE)
            app.launch([], None)
        except GLib.Error:
            self.fail('Could not open Account Settings.')

    def choose_device(self, identity):
        if not self.dead and not self.rendering and not self.state['busy'] and not self.pending:
            self.selected_id = identity
            self.render()

    def render(self):
        if self.dead:
            return
        self.rendering = True
        try:
            state = self.state
            devices = state['devices']
            self.current = next((device for device in devices if device['id'] == self.selected_id), devices[0] if devices else None)
            if self.current:
                self.selected_id = self.current['id']
            current = self.current
            values = current['state'] if current else {}
            caps = current['capabilities'] if current else {}
            busy = state['busy'] or self.pending is not None or self.refreshing
            sensitive = bool(state['connection'] == 'ready' and current and current['online'] and not busy)
            available = bool(state['connection'] == 'ready' and current and current['online'])
            name = clean_text(current.get('name', 'Air conditioner')) if current else 'MirAIe AC Controls'
            self.heading.set_label(name)
            statuses = {'signed-out': 'Sign in through Account Settings', 'connecting': 'Connecting…', 'error': 'Connection error', 'offline': 'Network unavailable'}
            status = ('Updating ' + TITLES[self.pending['field']].lower() + '…') if self.pending else 'Refreshing…' if busy else (
                'Online' if current and current['online'] else 'AC offline' if current else 'No air conditioners found') if state['connection'] == 'ready' else statuses.get(state['connection'], 'Unavailable')
            self.status.set_label(status)
            self.devices.set_label('Air conditioner: ' + name)
            self.devices.set_sensitive(not busy)
            self.devices.set_visible(len(devices) > 1)
            active_ids = {device['id'] for device in devices}
            for device in devices:
                identity = device['id']
                if identity not in self.device_items:
                    item = Gtk.CheckMenuItem()
                    self._connect_widget(item, 'activate', lambda _item, selected=identity: self.choose_device(selected))
                    self.device_menu.append(item)
                    self.device_items[identity] = item
                item = self.device_items[identity]
                item.set_label(clean_text(device.get('name', 'Air conditioner')))
                item.set_active(identity == self.selected_id)
                item.set_sensitive(not busy)
                item.show()
            for identity, item in self.device_items.items():
                if identity not in active_ids:
                    item.hide()
            info = ' · '.join(clean_text(current.get(key, '')) for key in ('room', 'model') if current and current.get(key))
            self.info.set_label(info)
            self.info.set_visible(bool(info))
            self.power.set_active(values.get('power') is True)
            self.power.set_visible(available)
            self.power_unavailable.set_visible(not available)
            self.power_unavailable.set_label('Power: Unknown (AC offline)' if current and not current['online'] else 'Power: Unavailable')
            self.power.set_label('Power' if values.get('power') is not None else 'Power: Unavailable')
            self.power.set_sensitive(sensitive and values.get('power') is not None)
            temperature = values.get('temperature')
            self.target.set_label(('Target temperature: ' if available else 'Last known target: ') + ('Unavailable' if temperature is None else f'{temperature:g} °C'))
            bounds = caps.get('temperature')
            self.minus.set_sensitive(bool(sensitive and bounds and temperature is not None and temperature > bounds['min']))
            self.plus.set_sensitive(bool(sensitive and bounds and temperature is not None and temperature < bounds['max']))
            room_temperature = values.get('roomTemperature')
            self.room.set_label(('Room temperature: ' if available else 'Last known room temperature: ') + ('Unavailable' if room_temperature is None else f'{room_temperature:g} °C'))
            for field, (item, submenu) in self.choices.items():
                options = caps.get(OPTIONS[field], [])
                value = values.get(field)
                item.set_label(TITLES[field] + ': ' + ('Unavailable' if value is None else label(value, field)))
                item.set_visible(field in ('mode', 'fan') or bool(options))
                item.set_sensitive(bool(sensitive and options and value is not None))
                for option in options:
                    if option not in self.choice_items[field]:
                        row = Gtk.CheckMenuItem()
                        self._connect_widget(row, 'activate', lambda _row, selected=option, setting=field: self.command(setting, selected))
                        submenu.append(row)
                        self.choice_items[field][option] = row
                    row = self.choice_items[field][option]
                    row.set_label(label(option, field))
                    row.set_active(option == value)
                    row.set_sensitive(sensitive and value is not None)
                    row.show()
                for option, row in self.choice_items[field].items():
                    if option not in options:
                        row.hide()
            for field, item in self.switches.items():
                item.set_visible(caps.get(field) is True)
                item.set_active(values.get(field) is True)
                item.set_sensitive(sensitive and caps.get(field) is True and values.get(field) is not None)
            error = clean_text(state.get('error', ''), 600)
            self.error.set_label(error)
            self.error.set_visible(bool(error))
            self.refresh_item.set_sensitive(not busy)
            confirmed_label = f'{temperature:g}°' if sensitive and values.get('power') is True and temperature is not None else ''
            self.indicator.set_label(confirmed_label, '30°')
        finally:
            self.rendering = False

    def _native_connected(self, _source, result):
        try:
            proxy = Gio.DBusProxy.new_for_bus_finish(result)
        except GLib.Error:
            return
        if not self.dead:
            self.native_proxy = proxy
            self.check_native()

    def check_native(self):
        if self.dead:
            return GLib.SOURCE_REMOVE
        if not self.native_proxy or self.native_checking:
            return GLib.SOURCE_CONTINUE
        self.native_checking = True
        def completed(proxy, result):
            self.native_checking = False
            try:
                info = proxy.call_finish(result).unpack()[0]
                status = info.get('state')
                status = status.unpack() if isinstance(status, GLib.Variant) else status
            except GLib.Error:
                return
            if not self.dead and status == 1:
                self.application.quit()
        self.native_proxy.call('GetExtensionInfo', GLib.Variant('(s)', (NATIVE_UUID,)), Gio.DBusCallFlags.NONE,
                               5000, self.cancellable, completed)
        return GLib.SOURCE_CONTINUE

    def close(self):
        if self.dead:
            return
        self.dead = True
        self.cancellable.cancel()
        self._clear_pending()
        if self.native_timer:
            GLib.source_remove(self.native_timer)
        self.native_timer = 0
        if self.proxy:
            for signal in self.proxy_signals:
                self.proxy.disconnect(signal)
        self.proxy_signals = []
        self.native_proxy = None
        for widget, signal in self.widget_signals:
            widget.disconnect(signal)
        self.widget_signals = []
        self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.PASSIVE)
        self.menu.popdown()
        self.menu.destroy()
        self.proxy = None


class Application(Gtk.Application):
    def __init__(self):
        super().__init__(application_id='io.github.ijaz.Miraie.Tray', flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.panel = None
        self.connect('activate', self.activate)
        self.connect('shutdown', self.shutdown)

    def activate(self, *_args):
        if not self.panel:
            self.hold()
            self.panel = Panel(self)

    def shutdown(self, *_args):
        if self.panel:
            self.panel.close()
            self.panel = None


if __name__ == '__main__':
    raise SystemExit(Application().run(sys.argv))
