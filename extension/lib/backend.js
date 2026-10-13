import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import {EMPTY_STATE, parseState, validateCommand} from './state.js';

const BUS = 'io.github.ijaz.Miraie';
const PATH = '/io/github/ijaz/Miraie';
const INTERFACE = 'io.github.ijaz.Miraie.Controller1';

export class Backend {
    constructor() {
        this.state = {...EMPTY_STATE};
        this.pending = null;
        this.listeners = new Set();
        this.signals = [];
        this.proxy = null;
        this.cancellable = new Gio.Cancellable();
        this.dead = false;
        this.revision = 0;
        this.pendingTimer = 0;
    }
    subscribe(callback) {
        this.listeners.add(callback);
        callback();
        return () => this.listeners.delete(callback);
    }
    emit() { if (!this.dead) for (const callback of this.listeners) callback(); }
    async start() {
        try {
            const proxy = await new Promise((resolve, reject) => {
                Gio.DBusProxy.new_for_bus(Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES,
                    null, BUS, PATH, INTERFACE, this.cancellable, (_source, result) => {
                        try { resolve(Gio.DBusProxy.new_for_bus_finish(result)); } catch (error) { reject(error); }
                    });
            });
            if (this.dead) return;
            this.proxy = proxy;
            this.signals.push(proxy.connect('g-signal', (_proxy, _sender, name, parameters) => {
                if (this.dead || name !== 'StateChanged') return;
                this.accept(parameters.deep_unpack()[0]);
            }));
            this.signals.push(proxy.connect('notify::g-name-owner', () => {
                if (this.dead) return;
                if (proxy.g_name_owner) this.getState();
                else this.fail('The AC service is unavailable. Open Account Settings to reconnect.');
            }));
            await this.getState();
        } catch (error) { this.fail(error.message); }
    }
    call(method, parameters = null) {
        if (this.dead || !this.proxy) return Promise.reject(new Error('The AC service is unavailable'));
        return new Promise((resolve, reject) => {
            this.proxy.call(method, parameters, Gio.DBusCallFlags.NONE, 15000, this.cancellable, (proxy, result) => {
                try {
                    const reply = proxy.call_finish(result);
                    if (this.dead) { reject(new Error('Controller stopped')); return; }
                    resolve(reply.deep_unpack());
                } catch (error) { reject(error); }
            });
        });
    }
    accept(json) {
        if (this.dead) return;
        let state;
        try { state = parseState(json); } catch {
            this.fail('The AC service returned invalid status. Refresh to try again.');
            return;
        }
        this.revision++;
        this.state = state;
        if (this.pending) {
            const device = state.devices.find(item => item.id === this.pending.deviceId);
            if (state.busy) this.pending.sawBusy = true;
            if (!state.busy && (state.error || state.connection !== 'ready' || !device?.online ||
                device?.state[this.pending.field] === this.pending.value || this.pending.sawBusy))
                this.clearPending();
        }
        this.emit();
    }
    async getState() {
        const revision = this.revision;
        try {
            const [json] = await this.call('GetState');
            // Do not replace a newer MQTT state with a reply requested earlier.
            if (!this.dead && revision === this.revision) this.accept(json);
        } catch (error) { this.fail(error.message); }
    }
    async refresh() {
        try { await this.call('Refresh'); await this.getState(); }
        catch (error) { this.fail(error.message); }
    }
    async command(deviceId, field, value) {
        if (this.dead || this.pending) return;
        try {
            validateCommand(this.state, deviceId, field, value);
            this.pending = {deviceId, field, value, sawBusy: false};
            this.state = {...this.state, error: ''};
            this.emit();
            this.pendingTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 30000, () => {
                this.pendingTimer = 0;
                if (!this.dead && this.pending) {
                    this.clearPending();
                    this.state = {...this.state, error: 'Update sent; the AC did not confirm the requested setting. Refresh to check its state.'};
                    this.emit();
                }
                return GLib.SOURCE_REMOVE;
            });
            await this.call('Command', new GLib.Variant('(sss)', [deviceId, field, JSON.stringify(value)]));
            if (!this.dead) await this.getState();
        } catch (error) {
            if (this.dead) return;
            this.clearPending();
            this.state = {...this.state, error: String(error.message).replace(/[\x00-\x1f\x7f]/g, ' ').slice(0, 400)};
            this.emit();
        }
    }
    clearPending() {
        if (this.pendingTimer) GLib.Source.remove(this.pendingTimer);
        this.pendingTimer = 0;
        this.pending = null;
    }
    fail(message) {
        if (this.dead) return;
        this.clearPending();
        this.state = {...this.state, connection: 'error', busy: false,
            error: String(message).replace(/[\x00-\x1f\x7f]/g, ' ').slice(0, 400)};
        this.emit();
    }
    stop() {
        this.dead = true;
        this.cancellable.cancel();
        this.clearPending();
        for (const id of this.signals) this.proxy.disconnect(id);
        this.signals = [];
        this.proxy = null;
        this.listeners.clear();
    }
}
