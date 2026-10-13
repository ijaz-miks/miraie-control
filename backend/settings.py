#!/usr/bin/python3
"""Native account setup and standalone AC controls."""
import json
import threading

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk
from core import CloudAPI, MirAIeError
import credentials

BUS = 'io.github.ijaz.Miraie'
PATH = '/io/github/ijaz/Miraie'
INTERFACE = BUS + '.Controller1'
LABELS = {'auto': 'Auto', 'cool': 'Cool', 'dry': 'Dry', 'fan': 'Fan only', 'heat': 'Heat',
          'low': 'Low', 'medium': 'Medium', 'high': 'High', 'quiet': 'Quiet',
          '0': 'Automatic swing', '1': 'Position 1', '2': 'Position 2', '3': 'Position 3',
          '4': 'Position 4', '5': 'Position 5'}


class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id='io.github.ijaz.Miraie.Settings')
        self.window = None
        self.proxy = None
        self.rendering = False
        self.current = None
        self.state = {'connection': 'signed-out', 'devices': [], 'busy': False, 'error': ''}
        self.connect('activate', self.activate)

    def activate(self, *_args):
        if self.window:
            self.window.present()
            return
        self.window = Adw.ApplicationWindow(application=self, title='MirAIe AC Controls', default_width=460, default_height=640)
        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        container.append(Adw.HeaderBar())
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        container.append(self.stack)
        self.window.set_content(container)
        self._login_page()
        self._controls_page()
        self.stack.set_visible_child_name('login')
        Gio.DBusProxy.new_for_bus(Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None, BUS, PATH, INTERFACE, None, self._connected)
        self.window.present()

    def _page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_start=24, margin_end=24, margin_top=18, margin_bottom=24)
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(box)
        return box, scroll

    def _login_page(self):
        box, scroll = self._page()
        icon = Gtk.Image(icon_name='weather-snow-symbolic', pixel_size=64)
        box.append(icon)
        title = Gtk.Label(label='Your AC, from your desktop', wrap=True)
        title.add_css_class('title-2')
        box.append(title)
        description = Gtk.Label(label='Sign in with the same account you use in the Panasonic MirAIe app. Your account details are saved in GNOME Keyring.', wrap=True, xalign=0)
        description.add_css_class('dim-label')
        box.append(description)
        group = Adw.PreferencesGroup(title='MirAIe account')
        self.username = Adw.EntryRow(title='Email or mobile number')
        self.password = Adw.PasswordEntryRow(title='Password')
        group.add(self.username)
        group.add(self.password)
        box.append(group)
        self.connect_button = Gtk.Button(label='Connect account', css_classes=['suggested-action', 'pill'])
        self.connect_button.connect('clicked', self._login)
        box.append(self.connect_button)
        self.login_status = Gtk.Label(label='', wrap=True, xalign=0, selectable=True)
        box.append(self.login_status)
        self.stack.add_named(scroll, 'login')

    def _controls_page(self):
        box, scroll = self._page()
        self.status_label = Gtk.Label(label='Connecting…', xalign=0, wrap=True)
        self.status_label.add_css_class('heading')
        box.append(self.status_label)
        self.devices = Gtk.DropDown(model=Gtk.StringList.new([]))
        self.devices.connect('notify::selected', lambda *_args: self._render(self.state))
        self.identities = []
        box.append(self.devices)
        self.details = Gtk.Label(label='', xalign=0, wrap=True)
        self.details.add_css_class('dim-label')
        box.append(self.details)
        self.group = Adw.PreferencesGroup(title='Air conditioner')
        box.append(self.group)
        self.rows = {}
        self.power = Adw.SwitchRow(title='Power')
        self.power.connect('notify::active', lambda *_args: self._set('power', self.power.get_active()))
        self.group.add(self.power)
        self.power_unavailable = Adw.ActionRow(title='Power', subtitle='Unavailable while this AC is offline.')
        self.group.add(self.power_unavailable)
        self.temperature = Gtk.SpinButton(adjustment=Gtk.Adjustment(lower=16, upper=30, step_increment=1), digits=0, valign=Gtk.Align.CENTER)
        self.temperature.connect('value-changed', lambda *_args: self._set('temperature', self.temperature.get_value()))
        self.temperature_row = Adw.ActionRow(title='Target temperature', subtitle='Degrees Celsius')
        self.temperature_row.add_suffix(self.temperature)
        self.group.add(self.temperature_row)
        self.room = Adw.ActionRow(title='Room temperature', subtitle='Unavailable')
        self.group.add(self.room)
        for field, label in [('mode', 'Mode'), ('fan', 'Fan speed'), ('verticalSwing', 'Vertical swing'), ('horizontalSwing', 'Horizontal swing'), ('capacity', 'Capacity')]:
            row = Adw.ComboRow(title=label)
            row._values = []
            row.connect('notify::selected', lambda widget, _param, key=field: self._set(key, widget._values[widget.get_selected()] if widget.get_selected() < len(widget._values) else None))
            self.rows[field] = row
            self.group.add(row)
        for field, label in [('nanoe', 'nanoe air purification'), ('display', 'AC display light')]:
            row = Adw.SwitchRow(title=label)
            row.connect('notify::active', lambda widget, _param, key=field: self._set(key, widget.get_active()))
            self.rows[field] = row
            self.group.add(row)
        self.error_label = Gtk.Label(label='', xalign=0, wrap=True, selectable=True)
        self.error_label.add_css_class('error')
        box.append(self.error_label)
        actions = Gtk.Box(spacing=12, homogeneous=True)
        self.refresh_button = Gtk.Button(label='Refresh')
        self.refresh_button.connect('clicked', lambda *_args: self._call('Refresh'))
        self.signout_button = Gtk.Button(label='Sign out')
        self.signout_button.connect('clicked', self._signout)
        actions.append(self.refresh_button)
        actions.append(self.signout_button)
        box.append(actions)
        self.stack.add_named(scroll, 'controls')

    def _connected(self, _source, result):
        try:
            self.proxy = Gio.DBusProxy.new_for_bus_finish(result)
            self.proxy.connect('g-signal', self._signal)
            self._call('GetState', callback=lambda value: self._render(json.loads(value.unpack()[0])))
        except Exception:
            self.login_status.set_label('The AC controller could not start. Open the application again to retry.')

    def _signal(self, _proxy, _sender, name, parameters):
        if name == 'StateChanged':
            try:
                self._render(json.loads(parameters.unpack()[0]))
            except (ValueError, TypeError):
                self.error_label.set_label('The controller returned an unsupported status.')

    def _call(self, name, parameters=None, callback=None):
        if not self.proxy:
            self.login_status.set_label('The AC controller is starting. Try again shortly.')
            return
        def done(proxy, result):
            try:
                value = proxy.call_finish(result)
                if callback:
                    callback(value)
            except GLib.Error as error:
                Gio.DBusError.strip_remote_error(error)
                self.error_label.set_label(error.message)
                self._call('GetState', callback=lambda value: self._render(json.loads(value.unpack()[0]))) if name == 'Command' else None
        self.proxy.call(name, parameters, Gio.DBusCallFlags.NONE, 20000, None, done)

    def _login(self, *_args):
        username, password = self.username.get_text().strip(), self.password.get_text()
        self.connect_button.set_sensitive(False)
        self.login_status.set_label('Signing in…')
        def work():
            try:
                api = CloudAPI()
                api.login(username, password)
                homes = api.homes()
                credentials.save(username, password)
                result = None
                count = sum(len(room.get('devices', [])) for home in homes for room in home.get('spaces', []))
            except Exception as error:
                result = str(error) if isinstance(error, MirAIeError) else 'Sign-in could not complete. Check that GNOME Keyring is unlocked.'
                count = 0
            GLib.idle_add(self._login_finished, result, count)
        threading.Thread(target=work, name='miraie-sign-in', daemon=True).start()

    def _login_finished(self, error, count):
        self.connect_button.set_sensitive(True)
        if error:
            self.login_status.set_label(error)
        else:
            self.password.set_text('')
            self.login_status.set_label(f'Account connected. Found {count} device(s).')
            self._call('Reload')
        return GLib.SOURCE_REMOVE

    def _signout(self, *_args):
        self.signout_button.set_sensitive(False)
        def work():
            try:
                credentials.clear()
                GLib.idle_add(lambda: (self._call('Reload'), GLib.SOURCE_REMOVE)[1])
            except Exception:
                GLib.idle_add(lambda: (self.error_label.set_label('GNOME Keyring could not sign out.'), GLib.SOURCE_REMOVE)[1])
        threading.Thread(target=work, name='miraie-sign-out', daemon=True).start()

    def _set(self, field, value):
        if self.rendering or value is None or not self.current or not self.proxy:
            return
        if self.state.get('busy') or self.state.get('connection') != 'ready' or not self.current.get('online') or self.current['state'].get(field) == value:
            return
        self.group.set_sensitive(False)
        self._call('Command', GLib.Variant('(sss)', (self.current['id'], field, json.dumps(value))))

    def _render(self, state):
        if self.rendering:
            return
        self.state = state
        self.rendering = True
        try:
            connection = state.get('connection', 'error')
            self.login_status.set_label(state.get('error', ''))
            if connection == 'signed-out':
                self.stack.set_visible_child_name('login')
                return
            if connection == 'error' and not state.get('devices'):
                self.stack.set_visible_child_name('login')
                return
            self.stack.set_visible_child_name('controls')
            items = state.get('devices', [])
            identities = [d['id'] for d in items]
            selected = self.current['id'] if self.current else None
            if identities != self.identities:
                self.identities = identities
                self.devices.set_model(Gtk.StringList.new([d['name'] for d in items]))
                self.devices.set_selected(identities.index(selected) if selected in identities else 0)
            index = self.devices.get_selected()
            self.current = items[index] if index < len(items) else None
            device = self.current
            busy = state.get('busy', False)
            ready = connection == 'ready' and device is not None and device['online']
            self.status_label.set_label('Updating…' if busy else 'Connected' if ready else 'AC offline' if device else 'No air conditioners found' if connection == 'ready' else 'Connecting…' if connection == 'connecting' else 'Connection unavailable')
            self.group.set_sensitive(bool(ready and not busy))
            self.refresh_button.set_sensitive(not busy)
            self.signout_button.set_sensitive(not busy)
            self.error_label.set_label(state.get('error', ''))
            if not device:
                self.details.set_label('Add your air conditioner in the MirAIe mobile app, then refresh here.')
                return
            connection_label = 'Local Wi-Fi' if device.get('transport') == 'local' else 'MirAIe cloud'
            self.details.set_label(' · '.join(filter(None, [device['room'], device['model'], connection_label])))
            values, caps = device['state'], device['capabilities']
            self.power.set_active(values.get('power') is True)
            self.power.set_visible(bool(ready))
            self.power_unavailable.set_visible(not ready)
            self.power.set_sensitive(isinstance(values.get('power'), bool))
            limits = caps['temperature']
            adjustment = self.temperature.get_adjustment()
            adjustment.set_lower(limits['min'])
            adjustment.set_upper(limits['max'])
            adjustment.set_step_increment(limits['step'])
            self.temperature.set_digits(1 if limits['step'] == 0.5 else 0)
            temperature = values.get('temperature')
            self.temperature_row.set_title('Target temperature' if ready else 'Last known target temperature')
            if isinstance(temperature, (int, float)):
                self.temperature.set_value(temperature)
            self.temperature_row.set_sensitive(temperature is not None)
            self.room.set_title('Room temperature' if ready else 'Last known room temperature')
            self.room.set_subtitle(f"{values['roomTemperature']:g} °C" if isinstance(values.get('roomTemperature'), (int, float)) else 'Unavailable')
            for field, row in self.rows.items():
                if isinstance(row, Adw.SwitchRow):
                    row.set_visible(bool(caps.get(field)))
                    row.set_active(values.get(field) is True)
                    row.set_sensitive(isinstance(values.get(field), bool))
                    continue
                options = caps.get({'mode': 'modes', 'fan': 'fanModes'}.get(field, field), [])
                row.set_visible(bool(options))
                if row._values != options:
                    row._values = options
                    row.set_model(Gtk.StringList.new([('Off' if value == '0' else value + '%') if field == 'capacity' else LABELS.get(value, value) for value in options]))
                if values.get(field) in options:
                    row.set_selected(options.index(values[field]))
                row.set_sensitive(values.get(field) in options)
        finally:
            self.rendering = False


if __name__ == '__main__':
    Adw.init()
    Application().run()
