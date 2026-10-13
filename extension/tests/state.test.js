import GLib from 'gi://GLib';
import {parseState, selectedDevice, validateCommand} from '../lib/state.js';
import {Backend} from '../lib/backend.js';

let assertions = 0;
function assert(condition, message) {
    assertions++;
    if (!condition) throw new Error(message);
}
function rejects(callback, message) {
    let rejected = false;
    try { callback(); } catch { rejected = true; }
    assert(rejected, message);
}
function snapshot(overrides = {}) {
    return {connection: 'ready', busy: false, error: '', devices: [{id: 'ac-1', name: 'Living room AC',
        room: 'Living room', model: 'Panasonic', online: true,
        state: {power: true, temperature: 24, roomTemperature: null, mode: 'cool', fan: 'auto', nanoe: null},
        capabilities: {modes: ['cool', 'dry'], fanModes: ['auto', 'high'], verticalSwing: [],
            horizontalSwing: [], temperature: {min: 16, max: 30, step: 1}, nanoe: false,
            display: false, capacity: []}}], ...overrides};
}
const parsed = parseState(JSON.stringify(snapshot()));
assert(parsed.devices[0].state.roomTemperature === undefined, 'Missing temperatures must not become zero');
assert(parsed.devices[0].state.nanoe === undefined, 'Missing switches must not become false');
assert(selectedDevice(parsed, 'removed-ac').id === 'ac-1', 'A removed device selection must resolve safely');
validateCommand(parsed, 'ac-1', 'temperature', 25);
rejects(() => validateCommand(parsed, 'ac-1', 'temperature', 31), 'Reject excessive temperatures');
rejects(() => validateCommand(parsed, 'ac-1', 'temperature', 24.5), 'Reject unsupported temperature steps');
rejects(() => validateCommand(parsed, 'ac-1', 'nanoe', true), 'Reject unsupported nanoe');
rejects(() => validateCommand(parsed, 'ac-1', 'mode', 'heat'), 'Reject unsupported operating modes');
rejects(() => validateCommand({...parsed, busy: true}, 'ac-1', 'power', false), 'Reject writes while a transaction is pending');
rejects(() => validateCommand({...parsed, connection: 'offline'}, 'ac-1', 'power', false), 'Reject writes while disconnected');
const offline = snapshot(); offline.devices[0].online = false;
rejects(() => validateCommand(parseState(JSON.stringify(offline)), 'ac-1', 'power', false), 'Reject writes to an offline device');
const malformed = snapshot(); malformed.devices[0].state.power = 'false';
rejects(() => parseState(JSON.stringify(malformed)), 'Reject string booleans from malformed status');
const duplicate = snapshot(); duplicate.devices.push({...duplicate.devices[0]});
rejects(() => parseState(JSON.stringify(duplicate)), 'Reject duplicate device identities');
const invalidBounds = snapshot(); invalidBounds.devices[0].capabilities.temperature.step = 0;
rejects(() => parseState(JSON.stringify(invalidBounds)), 'Reject invalid temperature capabilities');

const client = new Backend();
client.accept(JSON.stringify(snapshot()));
let resolveState;
client.call = () => new Promise(resolve => { resolveState = resolve; });
const earlier = client.getState();
const fresh = snapshot(); fresh.devices[0].state.temperature = 26;
client.accept(JSON.stringify(fresh));
resolveState([JSON.stringify(snapshot())]);
await earlier;
assert(client.state.devices[0].state.temperature === 26, 'An earlier GetState reply must not overwrite a newer update');
client.accept('{broken');
assert(client.state.connection === 'error', 'A malformed response must disable controls');
client.stop();

const commandClient = new Backend();
commandClient.accept(JSON.stringify(snapshot()));
let sent;
commandClient.call = async (method, parameters) => {
    if (method === 'Command') {
        sent = parameters.deep_unpack();
        return ['job-1'];
    }
    return [JSON.stringify(snapshot())];
};
await commandClient.command('ac-1', 'temperature', 25);
assert(sent.join('|') === 'ac-1|temperature|25', 'Send typed JSON values through the explicit D-Bus contract');
assert(commandClient.pending !== null, 'A broker acknowledgement must not claim a device-state confirmation');
assert(commandClient.state.devices[0].state.temperature === 24, 'Display only the confirmed temperature');
const confirmed = snapshot(); confirmed.devices[0].state.temperature = 25;
commandClient.accept(JSON.stringify(confirmed));
assert(commandClient.pending === null, 'Release pending state once the requested value is confirmed');
assert(commandClient.pendingTimer === 0, 'Cancel the confirmation deadline after completion');
commandClient.stop();

const stoppedClient = new Backend();
stoppedClient.accept(JSON.stringify(snapshot()));
let lateReject;
stoppedClient.call = () => new Promise((_resolve, reject) => { lateReject = reject; });
let notifications = 0;
stoppedClient.subscribe(() => { notifications++; });
const pendingWrite = stoppedClient.command('ac-1', 'power', false);
stoppedClient.stop();
const stoppedNotifications = notifications;
lateReject(new Error('Late failure'));
await pendingWrite;
assert(notifications === stoppedNotifications, 'Callbacks after disable must not update destroyed UI');
assert(stoppedClient.pendingTimer === 0, 'Disable must release command timers');
assert(stoppedClient.listeners.size === 0, 'Disable must release UI subscriptions');
print(`PASS: ${assertions} state, command and lifecycle assertions`);
