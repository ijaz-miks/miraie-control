#!/usr/bin/python3
"""Session D-Bus service for the MirAIe desktop controls."""
import json
import pathlib
import signal
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'vendor'))
import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio, GLib
import paho.mqtt.client as mqtt
from core import Controller, MirAIeError
from credentials import load
from local import LocalTransport

BUS = 'io.github.ijaz.Miraie'
PATH = '/io/github/ijaz/Miraie'
INTERFACE = BUS + '.Controller1'
XML = '''<node><interface name="io.github.ijaz.Miraie.Controller1">
<method name="GetState"><arg name="state" type="s" direction="out"/></method>
<method name="Command"><arg name="device" type="s" direction="in"/><arg name="field" type="s" direction="in"/><arg name="value" type="s" direction="in"/><arg name="job" type="s" direction="out"/></method>
<method name="Refresh"/><method name="Reload"/>
<signal name="StateChanged"><arg name="state" type="s"/></signal>
</interface></node>'''

def main():
    loop = GLib.MainLoop()
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    def changed(state):
        def emit():
            connection.emit_signal(None, PATH, INTERFACE, 'StateChanged', GLib.Variant('(s)', (json.dumps(state),)))
            return GLib.SOURCE_REMOVE
        GLib.idle_add(emit)
    controller = Controller(load, changed, mqtt_factory=lambda identity:
        mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=identity, protocol=mqtt.MQTTv311), local_factory=LocalTransport)
    def method(_connection, _sender, _path, _interface, name, parameters, invocation):
        try:
            if name == 'GetState':
                invocation.return_value(GLib.Variant('(s)', (json.dumps(controller.snapshot()),)))
            elif name == 'Command':
                identity, field, value = parameters.unpack()
                if len(value) > 1024:
                    raise MirAIeError('Unsupported command value.')
                job = controller.command(identity, field, json.loads(value))
                invocation.return_value(GLib.Variant('(s)', (job,)))
            elif name in ('Refresh', 'Reload'):
                (controller.refresh if name == 'Refresh' else controller.reload)()
                invocation.return_value(GLib.Variant('()', ()))
            else:
                invocation.return_dbus_error(INTERFACE + '.UnknownMethod', 'Unknown method.')
        except (MirAIeError, ValueError, TypeError) as error:
            invocation.return_dbus_error(INTERFACE + '.Failed', str(error))
        except Exception:
            invocation.return_dbus_error(INTERFACE + '.Failed', 'The controller could not complete this request.')
    registration = connection.register_object(PATH, Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0], method, None, None)
    owner = Gio.bus_own_name_on_connection(connection, BUS, Gio.BusNameOwnerFlags.NONE, None, lambda *_args: loop.quit())
    for signum in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, lambda: (loop.quit(), GLib.SOURCE_REMOVE)[1])
    try:
        loop.run()
    finally:
        controller.close()
        connection.unregister_object(registration)
        Gio.bus_unown_name(owner)

if __name__ == '__main__':
    main()
