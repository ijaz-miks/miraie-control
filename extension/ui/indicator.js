import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {humanLabel, selectedDevice} from '../lib/state.js';

const FIELDS = {power: 'power', temperature: 'temperature', mode: 'mode', fan: 'fan speed',
    verticalSwing: 'vertical swing', horizontalSwing: 'horizontal swing', nanoe: 'nanoe',
    display: 'display light', capacity: 'capacity'};
const STATUS = {'signed-out': 'Sign in to your MirAIe account', connecting: 'Connecting',
    ready: 'Online', error: 'Connection error', offline: 'Network unavailable'};

const StaySwitch = GObject.registerClass({GTypeName: 'MiraieControlStaySwitch'}, class StaySwitch extends PopupMenu.PopupSwitchMenuItem {
    activate(_event) { if (this._switch.mapped) this.toggle(); }
});
const StayItem = GObject.registerClass({GTypeName: 'MiraieControlStayItem', Signals: {triggered: {}}}, class StayItem extends PopupMenu.PopupMenuItem {
    activate(_event) { this.emit('triggered'); }
});

class ChoiceMenu {
    constructor(title, select) {
        this.title = title;
        this.select = select;
        this.item = new PopupMenu.PopupSubMenuMenuItem(`${title}: Unavailable`);
        this.values = [];
        this.rows = new Map();
        this.signals = [];
    }
    setOptions(values) {
        if (JSON.stringify(values) === JSON.stringify(this.values)) return;
        for (const [object, id] of this.signals) object.disconnect(id);
        this.signals = [];
        this.item.menu.removeAll();
        this.rows.clear();
        this.values = [...values];
        for (const value of values) {
            const row = new StayItem(humanLabel(value));
            this.signals.push([row, row.connect('triggered', () => { if (row.sensitive) this.select(value); })]);
            this.rows.set(value, row);
            this.item.menu.addMenuItem(row);
        }
    }
    render(value, sensitive, visible = true) {
        this.item.visible = visible;
        this.item.label.text = `${this.title}: ${value === undefined ? 'Unavailable' : humanLabel(value)}`;
        // Keep an open submenu expanded while the command is being confirmed.
        this.item.sensitive = Boolean(this.values.length);
        for (const [choice, row] of this.rows) {
            row.sensitive = sensitive && value !== undefined;
            row.setOrnament(choice === value ? PopupMenu.Ornament.DOT : PopupMenu.Ornament.NONE);
        }
    }
    stop() {
        for (const [object, id] of this.signals) object.disconnect(id);
        this.signals = [];
    }
}

// Explicit names: GJS derives them from ui/indicator.js + class, which other extensions also use.
export const Indicator = GObject.registerClass({GTypeName: 'MiraieControlIndicator'}, class Indicator extends PanelMenu.Button {
    _init(backend, settings) {
        super._init(0.5, 'MirAIe AC Controls');
        this.backend = backend;
        this.settings = settings;
        this.dead = false;
        this.rendering = false;
        this.localError = '';
        this.signals = [];
        this.deviceSignals = [];
        this.deviceRows = new Map();
        this.deviceIdentity = '';
        this.choices = new Map();
        const connect = (object, signal, callback) => {
            this.signals.push([object, object.connect(signal, (...args) => { if (!this.dead) callback(...args); })]);
        };
        const panelBox = new St.BoxLayout({style_class: 'panel-status-menu-box'});
        panelBox.add_child(new St.Icon({icon_name: 'weather-snow-symbolic', style_class: 'system-status-icon'}));
        this.panelTemperature = new St.Label({text: '', y_align: Clutter.ActorAlign.CENTER});
        panelBox.add_child(this.panelTemperature);
        this.add_child(panelBox);
        this.menu.box.add_style_class_name('miraie-control-menu');
        this.header = new PopupMenu.PopupImageMenuItem('MirAIe AC Controls', 'weather-snow-symbolic', {reactive: false, can_focus: false});
        this.header.label.add_style_class_name('miraie-control-header');
        this.menu.addMenuItem(this.header);
        this.status = new PopupMenu.PopupMenuItem('Connecting', {reactive: false, can_focus: false});
        this.menu.addMenuItem(this.status);
        this.devices = new PopupMenu.PopupSubMenuMenuItem('Air conditioner');
        this.menu.addMenuItem(this.devices);
        this.room = new PopupMenu.PopupMenuItem('', {reactive: false, can_focus: false});
        this.menu.addMenuItem(this.room);
        this.controls = new PopupMenu.PopupMenuSection();
        this.scroll = new St.ScrollView({hscrollbar_policy: St.PolicyType.NEVER,
            vscrollbar_policy: St.PolicyType.AUTOMATIC, child: this.controls.box, y_expand: true});
        this.controls.actor = this.scroll;
        this.menu.addMenuItem(this.controls);
        this.power = new StaySwitch('Power', false);
        connect(this.power, 'toggled', (_item, value) => {
            if (!this.rendering && this.power.sensitive && value !== this.device?.state.power)
                this.command('power', value);
        });
        this.controls.addMenuItem(this.power);
        // Thermostat card: − target + with the measured room temperature underneath.
        this.temperatureCard = new PopupMenu.PopupBaseMenuItem({activate: false, can_focus: false,
            style_class: 'miraie-temperature-card'});
        const card = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, x_expand: true});
        const dial = new St.BoxLayout({x_expand: true});
        const step = (icon, name) => new St.Button({style_class: 'button miraie-temperature-step', can_focus: true,
            accessible_name: name, y_align: Clutter.ActorAlign.CENTER, child: new St.Icon({icon_name: icon})});
        this.minus = step('list-remove-symbolic', 'Decrease target temperature');
        this.plus = step('list-add-symbolic', 'Increase target temperature');
        const reading = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, x_expand: true,
            y_align: Clutter.ActorAlign.CENTER});
        this.temperature = new St.Label({text: '--', style_class: 'miraie-temperature-value', x_align: Clutter.ActorAlign.CENTER});
        this.temperatureCaption = new St.Label({text: 'Target temperature', style_class: 'miraie-temperature-caption',
            x_align: Clutter.ActorAlign.CENTER});
        for (const actor of [this.temperature, this.temperatureCaption]) reading.add_child(actor);
        for (const actor of [this.minus, reading, this.plus]) dial.add_child(actor);
        this.roomTemperature = new St.Label({text: 'Room temperature unavailable', style_class: 'miraie-room-temperature',
            x_align: Clutter.ActorAlign.CENTER});
        for (const actor of [dial, this.roomTemperature]) card.add_child(actor);
        this.temperatureCaption.opacity = this.roomTemperature.opacity = 170;
        this.temperatureCard.add_child(card);
        connect(this.minus, 'clicked', () => this.stepTemperature(-1));
        connect(this.plus, 'clicked', () => this.stepTemperature(1));
        this.controls.addMenuItem(this.temperatureCard);
        for (const [field, title] of [['mode', 'Mode'], ['fan', 'Fan speed'],
            ['verticalSwing', 'Vertical swing'], ['horizontalSwing', 'Horizontal swing']]) {
            const choice = new ChoiceMenu(title, value => this.command(field, value));
            this.choices.set(field, choice);
            this.controls.addMenuItem(choice.item);
        }
        this.advancedHeading = new PopupMenu.PopupSeparatorMenuItem('Additional controls');
        this.controls.addMenuItem(this.advancedHeading);
        this.nanoe = new StaySwitch('nanoe', false);
        this.display = new StaySwitch('Display light', false);
        for (const [field, item] of [['nanoe', this.nanoe], ['display', this.display]]) {
            connect(item, 'toggled', (_item, value) => {
                if (!this.rendering && item.sensitive && value !== this.device?.state[field]) this.command(field, value);
            });
            this.controls.addMenuItem(item);
        }
        const capacity = new ChoiceMenu('Capacity', value => this.command('capacity', value));
        this.choices.set('capacity', capacity);
        this.controls.addMenuItem(capacity.item);
        this.error = new PopupMenu.PopupMenuItem('', {reactive: false, can_focus: false});
        this.error.label.clutter_text.line_wrap = true;
        this.error.label.clutter_text.ellipsize = 0;
        this.error.label.x_expand = true;
        this.menu.addMenuItem(this.error);
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this.refresh = new StayItem('Refresh');
        connect(this.refresh, 'triggered', () => { this.localError = ''; this.backend.refresh(); });
        this.menu.addMenuItem(this.refresh);
        this.account = new PopupMenu.PopupMenuItem('Account Settings');
        connect(this.account, 'activate', () => this.openAccountSettings());
        this.menu.addMenuItem(this.account);
        connect(settings, 'changed::selected-device', () => this.render());
        connect(settings, 'changed::show-temperature', () => this.render());
        connect(this.menu, 'open-state-changed', (_menu, open) => {
            if (open) { this.fit(); this.backend.getState(); }
        });
        connect(Main.layoutManager, 'monitors-changed', () => this.fit());
        this.unsubscribe = backend.subscribe(() => this.render());
    }
    command(field, value) {
        if (!this.device || this.dead) return;
        this.localError = '';
        this.backend.command(this.device.id, field, value);
    }
    stepTemperature(direction) {
        const bounds = this.device?.capabilities.temperature;
        const value = this.device?.state.temperature;
        if (!bounds || value === undefined) return;
        const next = Math.round((value + direction * bounds.step) * 100) / 100;
        if (next >= bounds.min && next <= bounds.max) this.command('temperature', next);
    }
    openAccountSettings() {
        try {
            const executable = GLib.build_filenamev([GLib.get_home_dir(), '.local', 'bin', 'miraie-control-settings']);
            const file = Gio.File.new_for_path(executable);
            const info = file.query_info('access::can-execute,standard::type', Gio.FileQueryInfoFlags.NONE, null);
            if (info.get_file_type() !== Gio.FileType.REGULAR || !info.get_attribute_boolean('access::can-execute'))
                throw new Error('Account Settings is not installed');
            GLib.spawn_async(null, [executable], null, GLib.SpawnFlags.DEFAULT, null);
        } catch (error) { this.localError = String(error.message); this.render(); }
    }
    fit() {
        if (this.dead || !this.get_stage()) return;
        const monitor = Main.layoutManager.findMonitorForActor(this) ?? Main.layoutManager.primaryMonitor;
        if (!monitor) return;
        const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
        const height = Math.max(240, monitor.height - Main.panel.height - 48);
        this.menu.box.set_style(`max-height: ${Math.round(height / scale)}px; max-width: ${Math.round(Math.max(240, monitor.width - 48) / scale)}px;`);
        this.scroll.set_style(`min-height: 0; max-height: ${Math.round(Math.max(160, height - 260) / scale)}px;`);
    }
    rebuildDevices(devices) {
        const identity = JSON.stringify(devices.map(item => [item.id, item.name, item.room]));
        if (identity === this.deviceIdentity) return;
        this.deviceIdentity = identity;
        for (const [object, id] of this.deviceSignals) object.disconnect(id);
        this.deviceSignals = [];
        this.devices.menu.removeAll();
        this.deviceRows.clear();
        for (const device of devices) {
            const row = new StayItem(device.room ? `${device.name} · ${device.room}` : device.name);
            this.deviceSignals.push([row, row.connect('triggered', () => {
                if (!this.dead && row.sensitive) this.settings.set_string('selected-device', device.id);
            })]);
            this.deviceRows.set(device.id, row);
            this.devices.menu.addMenuItem(row);
        }
    }
    render() {
        if (this.dead) return;
        this.rendering = true;
        try {
            const state = this.backend.state;
            this.device = selectedDevice(state, this.settings.get_string('selected-device'));
            const device = this.device;
            const pending = this.backend.pending;
            const busy = state.busy || Boolean(pending);
            const available = state.connection === 'ready' && Boolean(device?.online);
            const sensitive = available && !busy;
            const values = device?.state ?? {};
            const capabilities = device?.capabilities ?? {};
            this.header.label.text = device?.name ?? 'MirAIe AC Controls';
            this.status.label.text = busy ? `Updating ${pending ? FIELDS[pending.field] : 'AC status'}…` :
                available ? 'Online' : state.connection === 'ready' ? (device ? 'AC offline' : 'No air conditioners found') : STATUS[state.connection];
            this.accessible_name = `MirAIe AC Controls, ${device?.name ?? 'no AC selected'}, ${this.status.label.text}`;
            const show = available && values.power === true && values.temperature !== undefined && this.settings.get_boolean('show-temperature');
            this.panelTemperature.visible = show;
            this.panelTemperature.text = show ? `${values.temperature}°` : '';
            this.room.label.text = [device?.room, device?.model].filter(Boolean).join(' · ');
            this.room.visible = Boolean(this.room.label.text);
            this.rebuildDevices(state.devices);
            this.devices.label.text = device ? `Air conditioner: ${device.name}` : 'Air conditioner: Unavailable';
            this.devices.visible = state.devices.length > 1;
            this.devices.sensitive = !busy;
            for (const [id, row] of this.deviceRows) {
                row.setOrnament(id === device?.id ? PopupMenu.Ornament.DOT : PopupMenu.Ornament.NONE);
                row.sensitive = !busy;
            }
            this.power.setToggleState(values.power === true);
            this.power.setStatus(!available ? (device ? 'Offline' : 'Unavailable') : values.power === undefined ? 'Unavailable' : null);
            this.power.sensitive = sensitive && values.power !== undefined;
            this.power.syncSensitive();
            // Preview the requested target while the AC confirms it.
            const setting = pending?.field === 'temperature' && pending.deviceId === device?.id;
            const target = setting ? pending.value : values.temperature;
            this.temperature.text = target === undefined ? '--' : `${target}°`;
            this.temperatureCaption.text = setting ? 'Setting target…' : !available ? (device ? 'Last known target' : 'Target temperature') :
                values.power === false ? 'Target · AC is off' : 'Target temperature';
            // Opacity rather than colour keeps the native light and dark Shell styles intact.
            this.temperature.opacity = setting || !available || values.power === false ? 128 : 255;
            const bounds = capabilities.temperature;
            for (const [button, enabled] of [[this.minus, values.temperature > bounds?.min], [this.plus, values.temperature < bounds?.max]]) {
                const active = Boolean(sensitive && bounds && values.temperature !== undefined && enabled);
                button.reactive = active;
                button.can_focus = active;
                button.opacity = active ? 255 : 128;
            }
            this.roomTemperature.text = values.roomTemperature === undefined ? 'Room temperature unavailable' :
                `${available ? 'Room' : 'Last known room'} ${values.roomTemperature} °C`;
            for (const [field, choice] of this.choices) {
                const key = {mode: 'modes', fan: 'fanModes', verticalSwing: 'verticalSwing', horizontalSwing: 'horizontalSwing', capacity: 'capacity'}[field];
                const options = capabilities[key] ?? [];
                choice.setOptions(options);
                choice.render(values[field], sensitive, ['mode', 'fan'].includes(field) || options.length > 0);
            }
            for (const [field, item] of [['nanoe', this.nanoe], ['display', this.display]]) {
                item.visible = capabilities[field] === true;
                item.setToggleState(values[field] === true);
                item.setStatus(!available ? 'Offline' : values[field] === undefined ? 'Unavailable' : null);
                item.sensitive = sensitive && capabilities[field] === true && values[field] !== undefined;
                item.syncSensitive();
            }
            this.advancedHeading.visible = Boolean(capabilities.nanoe || capabilities.display || capabilities.capacity?.length);
            const error = [this.localError, state.error].filter(Boolean).join('\n');
            this.error.label.text = error;
            this.error.visible = Boolean(error);
            this.refresh.sensitive = !busy;
            this.fit();
        } finally { this.rendering = false; }
    }
    destroy() {
        if (this.dead) return;
        this.dead = true;
        this.unsubscribe?.();
        for (const [object, id] of [...this.signals, ...this.deviceSignals]) object.disconnect(id);
        this.signals = [];
        this.deviceSignals = [];
        for (const choice of this.choices.values()) choice.stop();
        super.destroy();
    }
});
