import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';

export const METRICS = {};
const uuid = 'miraie-control@ijaz.local';
const output = GLib.getenv('MIRAIETEST_OUTPUT');
const checks = [];
const assert = (value, message) => { if (!value) throw new Error(message); };
const sleep = ms => new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms,
    () => { resolve(); return GLib.SOURCE_REMOVE; }));
async function until(predicate, message) {
    const deadline = GLib.get_monotonic_time() + 12_000_000;
    while (!predicate()) {
        assert(GLib.get_monotonic_time() < deadline, message);
        await sleep(30);
    }
}
const fixture = () => ({connection: 'ready', busy: false, error: '', devices: [{
    id: 'fixture-ac-1', name: 'Bedroom AC', room: 'Bedroom', model: 'Mock Panasonic', online: true,
    state: {power: true, temperature: 24, roomTemperature: 28, mode: 'cool', fan: 'auto',
        verticalSwing: 'swing', horizontalSwing: 'fixed', nanoe: true, display: true, capacity: '100'},
    capabilities: {temperature: {min: 16, max: 30, step: 1}, modes: ['cool', 'dry', 'fan'],
        fanModes: ['auto', 'quiet', 'low', 'medium', 'high'], verticalSwing: ['swing', 'fixed'],
        horizontalSwing: ['swing', 'fixed'], nanoe: true, display: true, capacity: ['100', '80', '60', '40']},
}]});

function createMock() {
    assert(GLib.getenv('MIRAIETEST_ISOLATED') === '1' && output,
        'Refusing to export a controller outside the explicitly isolated test');
    assert(GLib.getenv('DBUS_SYSTEM_BUS_ADDRESS') === GLib.getenv('DBUS_SESSION_BUS_ADDRESS'),
        'System bus must also be isolated');
    const xml = `<node><interface name="io.github.ijaz.Miraie.Controller1">
      <method name="GetState"><arg type="s" direction="out"/></method>
      <method name="Command"><arg type="s" direction="in"/><arg type="s" direction="in"/>
        <arg type="s" direction="in"/><arg type="s" direction="out"/></method>
      <method name="Refresh"/><method name="Reload"/>
      <signal name="StateChanged"><arg type="s"/></signal>
    </interface></node>`;
    const commands = [];
    const mock = {state: {connection: 'signed-out', busy: false, error: '', devices: []},
        reads: 0, refreshes: 0, timers: new Set(), commands, delay: 60};
    const object = Gio.DBusExportedObject.wrapJSObject(xml, {
        GetState() { mock.reads++; return JSON.stringify(mock.state); },
        Command(deviceId, field, json) {
            const value = JSON.parse(json);
            commands.push({deviceId, field, value});
            const device = mock.state.devices.find(item => item.id === deviceId);
            assert(mock.state.connection === 'ready' && device?.online, 'Invalid mock command');
            mock.set({...mock.state, busy: true});
            const timer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, mock.delay, () => {
                mock.timers.delete(timer);
                device.state[field] = value;
                mock.set({...mock.state, busy: false});
                return GLib.SOURCE_REMOVE;
            });
            mock.timers.add(timer);
            return `mock-job-${commands.length}`;
        },
        Refresh() { mock.refreshes++; },
        Reload() {},
    });
    object.export(Gio.DBus.session, '/io/github/ijaz/Miraie');
    const owner = Gio.bus_own_name_on_connection(Gio.DBus.session, 'io.github.ijaz.Miraie',
        Gio.BusNameOwnerFlags.NONE, null, null);
    mock.set = state => {
        mock.state = state;
        object.emit_signal('StateChanged', new GLib.Variant('(s)', [JSON.stringify(state)]));
    };
    mock.stop = () => {
        for (const timer of mock.timers) GLib.Source.remove(timer);
        mock.timers.clear();
        object.unexport();
        Gio.bus_unown_name(owner);
    };
    return mock;
}

let mock;
let running;
let standardDriver = false;
export function init() {
    mock = createMock();
    Scripting._getPerfHelper().then(helper => { helper._autoExit = false; }).catch(logError);
    sleep(1500).then(async () => {
        running ??= execute();
        await running;
        if (!standardDriver) global.context.terminate();
    }).catch(error => { logError(error, 'MirAIe Shell smoke'); global.context.terminate(); });
}
export function run() {
    standardDriver = true;
    running ??= execute();
    return running;
}

async function activate(item) {
    assert(item.mapped && item.reactive, 'Menu control is not mapped and reactive');
    item.grab_key_focus();
    item._clickGesture.emit('recognize');
    await sleep(120);
}
async function expectCommand(action, field, value) {
    const before = mock.commands.length;
    await action();
    await until(() => mock.commands.length === before + 1, `${field} activation did not issue one Command`);
    await until(() => !Main.extensionManager.lookup(uuid).stateObj.backend.pending && !mock.state.busy,
        `${field} command did not settle`);
    assert(mock.commands.length === before + 1, `${field} activation issued duplicate Commands`);
    const command = mock.commands.at(-1);
    assert(command.field === field && command.value === value && command.deviceId === 'fixture-ac-1',
        `${field} activation sent the wrong arguments`);
}

async function execute() {
    try {
        await until(() => Main.extensionManager.lookup(uuid)?.stateObj?.indicator,
            'MirAIe extension failed to enable');
        const extension = Main.extensionManager.lookup(uuid).stateObj;
        await until(() => extension.backend.state.connection === 'signed-out', 'Signed-out state not received');
        let ui = extension.indicator;
        ui.menu.open(); await sleep(200);
        assert(ui.status.label.text === 'Sign in to your MirAIe account' && !ui.power.sensitive &&
            !ui.power.reactive && !ui.plus.reactive && !ui.panelTemperature.visible,
            'Signed-out controls were active or status was incorrect');
        checks.push('Signed-out panel renders with disabled AC controls');
        mock.set(fixture());
        await until(() => extension.backend.state.connection === 'ready', 'Ready state not received');
        await sleep(150);
        assert(ui.header.label.text === 'Bedroom AC' && ui.status.label.text === 'Online' &&
            ui.temperature.text === '24 °C' && ui.panelTemperature.text === '24°' &&
            ui.roomTemperature.label.text === 'Room temperature: 28 °C', 'Ready panel labels were incorrect');
        assert(ui.power.reactive && ui.plus.reactive && ui.nanoe.visible && ui.display.visible &&
            ui.choices.get('capacity').item.visible, 'Expected model controls missing');
        for (const field of ['mode', 'fan', 'verticalSwing', 'horizontalSwing', 'capacity'])
            assert(ui.choices.get(field).rows.size > 0, `${field} choices missing`);
        assert(ui.menu.box.height <= Main.layoutManager.primaryMonitor.height,
            'Menu exceeded the isolated monitor height');
        checks.push('Online device, temperature, room temperature and advanced capability controls render');

        const beforeRender = mock.commands.length;
        const changed = fixture();
        Object.assign(changed.devices[0].state, {power: false, nanoe: false, display: false,
            mode: 'dry', fan: 'high', temperature: 27, capacity: '60'});
        mock.set(changed); await sleep(100); mock.set(fixture()); await sleep(150);
        ui.render(); ui.render();
        assert(mock.commands.length === beforeRender, 'Rendering or state signals wrote to the controller');
        checks.push('Repeated rendering and changed status signals issue no Command');

        await expectCommand(() => activate(ui.power), 'power', false);
        assert(ui.menu.isOpen, 'Power activation closed the menu');
        await expectCommand(() => { ui.plus.emit('clicked', 1); }, 'temperature', 25);
        const keyboard = global.stage.context.get_backend().get_default_seat()
            .create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
        ui.power.grab_key_focus();
        await expectCommand(async () => {
            keyboard.notify_keyval(GLib.get_monotonic_time(), Clutter.KEY_space, Clutter.KeyState.PRESSED);
            keyboard.notify_keyval(GLib.get_monotonic_time(), Clutter.KEY_space, Clutter.KeyState.RELEASED);
            await sleep(100);
        }, 'power', true);
        for (const [field, value] of [['mode', 'dry'], ['fan', 'quiet'], ['verticalSwing', 'fixed'],
            ['horizontalSwing', 'swing'], ['capacity', '80']]) {
            const choice = ui.choices.get(field);
            choice.item.setSubmenuShown(true); await sleep(100);
            await expectCommand(() => activate(choice.rows.get(value)), field, value);
            assert(ui.menu.isOpen, `${field} selection closed the menu`);
            choice.item.setSubmenuShown(false);
        }
        await expectCommand(() => activate(ui.nanoe), 'nanoe', false);
        await expectCommand(() => activate(ui.display), 'display', false);
        const beforeRefresh = mock.commands.length;
        await activate(ui.refresh);
        await until(() => mock.refreshes === 1, 'Refresh did not call the service');
        assert(mock.commands.length === beforeRefresh, 'Refresh issued Command');
        checks.push('Pointer activation, keyboard Space, temperature, mode, fan, both swing axes, capacity, nanoe and display each call Command once');
        checks.push('Refresh requests fresh state and keeps the menu open');

        const cachedOffline = fixture();
        cachedOffline.devices[0].online = false;
        cachedOffline.devices[0].state.power = true;
        mock.set(cachedOffline); await sleep(100);
        assert(ui.status.label.text === 'AC offline' && ui.power._statusBin.child === ui.power._statusLabel &&
            ui.power._statusLabel.text === 'Offline' &&
            !ui.power.reactive && ui.temperature.text.includes('last known') && !ui.panelTemperature.visible,
            'Cached power-on state was presented as live for an offline AC');
        checks.push('Offline AC hides cached power switch and labels old temperatures as last known');
        mock.set({...mock.state, connection: 'offline'}); await sleep(100);
        assert(!ui.power.reactive && !ui.plus.reactive && !ui.nanoe.reactive &&
            !ui.choices.get('mode').rows.get('cool').reactive && !ui.panelTemperature.visible,
            'Offline AC controls remained reactive');
        mock.set({...mock.state, connection: 'error', error: 'Mock network failure'}); await sleep(100);
        assert(ui.error.visible && ui.error.label.text === 'Mock network failure' &&
            ui.status.label.text === 'Connection error', 'Error state was not shown');
        checks.push('Offline and error states disable controls and show the reason');
        mock.set(fixture()); await sleep(150);
        const screenshot = new Shell.Screenshot();
        const stream = Gio.File.new_for_path(`${output}/menu.png`).create(Gio.FileCreateFlags.NONE, null);
        try { await screenshot.screenshot(false, stream); } finally { stream.close(null); }
        checks.push('Menu screenshot captured');

        const oldBackend = extension.backend;
        const oldUi = extension.indicator;
        const menuActor = oldUi.menu.actor;
        let callbacks = 0;
        oldBackend.subscribe(() => { callbacks++; });
        mock.delay = 250;
        oldUi.command('temperature', 25);
        await until(() => mock.state.busy, 'Delayed command did not begin');
        extension.disable();
        const disabledCallbacks = callbacks;
        mock.set({...mock.state, error: 'Late mock status'});
        await sleep(350);
        assert(callbacks === disabledCallbacks && oldBackend.dead && oldBackend.listeners.size === 0 &&
            oldBackend.signals.length === 0 && !oldBackend.pendingTimer && !oldBackend.proxy,
            'Backend callback or timer survived teardown');
        assert(oldUi.dead && !Main.panel.statusArea[uuid] &&
            !Main.layoutManager._trackedActors.some(record => record.actor === menuActor),
            'Panel actor or popup survived disable');
        mock.set(fixture());
        extension.enable();
        await until(() => extension.backend.state.connection === 'ready', 'Re-enabled backend did not become ready');
        await sleep(150);
        ui = extension.indicator;
        ui.menu.open(); await sleep(150);
        assert(Main.panel.statusArea[uuid] === ui && ui.power.reactive, 'Re-enable leaked or missed its panel icon');
        extension.disable(); await sleep(150);
        assert(!Main.panel.statusArea[uuid], 'Final disable left a panel icon');
        checks.push('Disable during an in-flight command prevents late callbacks and releases signals, timers and popup chrome');
        checks.push('Re-enable establishes fresh backend and panel, final teardown is clean');
        GLib.file_set_contents(`${output}/result.json`, JSON.stringify({passed: true, checks,
            commands: mock.commands, reads: mock.reads, refreshes: mock.refreshes}));
    } catch (error) {
        GLib.file_set_contents(`${output}/result.json`, JSON.stringify({passed: false, checks,
            error: error.message, stack: error.stack, commands: mock.commands}));
        throw error;
    } finally { mock.stop(); }
}
