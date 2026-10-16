#!/usr/bin/python3
"""Exercise the actual GTK controls with a fake proxy, without account access."""
import copy
import json
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'backend'))

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk
from settings import Application

class Proxy:
    def __init__(self):
        self.calls = []
    def call(self, method, parameters, *_args):
        self.calls.append((method, parameters.unpack() if parameters else ()))

Adw.init()
app = Application()
app.window = Adw.Window()
app.stack = Gtk.Stack()
app.window.set_content(app.stack)
app._login_page()
app._controls_page()
app.proxy = Proxy()
device = {'id': 'test-ac', 'name': 'Test AC', 'room': 'Test room', 'model': 'Fixture', 'online': True,
          'state': {'power': False, 'temperature': 24.0, 'roomTemperature': 27.5, 'mode': 'cool', 'fan': 'auto', 'verticalSwing': '0', 'display': True},
          'capabilities': {'modes': ['cool', 'dry'], 'fanModes': ['auto', 'low'], 'verticalSwing': ['0', '1'], 'horizontalSwing': [], 'nanoe': False, 'display': True, 'capacity': [], 'temperature': {'min': 16, 'max': 30, 'step': 1}}}
state = {'connection': 'ready', 'busy': False, 'error': '', 'devices': [device]}
app._render(state)
assert app.stack.get_visible_child_name() == 'controls'
assert app.group.get_sensitive()
assert app.temperature.get_value() == 24.0
assert app.room.get_subtitle() == '27.5 °C'
assert not app.rows['horizontalSwing'].get_visible()
assert not app.rows['nanoe'].get_visible()
assert app.rows['display'].get_visible()
assert not app.proxy.calls, 'Rendering must not issue AC commands'
app.power.set_active(True)
assert len(app.proxy.calls) == 1
method, parameters = app.proxy.calls[0]
assert method == 'Command' and parameters == ('test-ac', 'power', 'true')
assert not app.power.get_active() and not app.group.get_sensitive(), 'Unconfirmed changes show the confirmed value as pending'
assert app.status_label.get_label() == 'Updating…'
app._render(state)
busy = copy.deepcopy(state)
busy['busy'] = True
app._render(busy)
assert not app.group.get_sensitive()
offline = copy.deepcopy(state)
offline['devices'][0]['online'] = False
offline['devices'][0]['state']['power'] = True
app._render(offline)
assert not app.group.get_sensitive()
assert not app.power.get_visible(), 'Offline cached power must not show an active switch'
assert app.power_unavailable.get_visible()
assert 'offline' in app.power_unavailable.get_subtitle()
assert app.temperature_row.get_title() == 'Last known target temperature'
app._set('temperature', 25)
assert len(app.proxy.calls) == 1, 'Offline controls must not send commands'
app._render({'connection': 'signed-out', 'busy': False, 'devices': [], 'error': ''})
assert app.stack.get_visible_child_name() == 'login'
app.window.destroy()
print('PASS: native GTK rendering, feature gating, confirmed display, user intent, busy/offline guards, and sign-out')
