const CONNECTIONS = new Set(['signed-out', 'connecting', 'ready', 'error', 'offline']);
const BOOLEAN_FIELDS = new Set(['power', 'nanoe', 'display']);
const ENUM_FIELDS = new Set(['mode', 'fan', 'verticalSwing', 'horizontalSwing', 'capacity']);
const OPTIONS = {mode: 'modes', fan: 'fanModes', verticalSwing: 'verticalSwing', horizontalSwing: 'horizontalSwing', capacity: 'capacity'};
export const EMPTY_STATE = Object.freeze({connection: 'connecting', busy: false, error: '', devices: []});

function object(value) {
    return value !== null && typeof value === 'object' && !Array.isArray(value);
}
function text(value, limit = 160) {
    if (typeof value !== 'string' || value.length > limit || /[\x00-\x1f\x7f]/.test(value))
        throw new TypeError('Invalid text in controller state');
    return value;
}
function options(value) {
    if (value === undefined || value === null) return [];
    if (!Array.isArray(value) || value.length > 40) throw new TypeError('Invalid device capabilities');
    const entries = value.map(item => text(item, 80));
    if (entries.some(item => !item.length) || new Set(entries).size !== entries.length)
        throw new TypeError('Invalid device capabilities');
    return entries;
}

/** Reject malformed snapshots. Missing fields stay unavailable, never off or zero. */
export function parseState(json) {
    if (typeof json !== 'string' || json.length > 262144) throw new TypeError('Invalid controller response');
    const input = JSON.parse(json);
    if (!object(input) || !CONNECTIONS.has(input.connection) || typeof input.busy !== 'boolean' ||
        !Array.isArray(input.devices) || input.devices.length > 100)
        throw new TypeError('Invalid controller response');
    const ids = new Set();
    const devices = input.devices.map(source => {
        if (!object(source) || typeof source.online !== 'boolean' || !object(source.state) || !object(source.capabilities))
            throw new TypeError('Invalid device response');
        const id = text(source.id, 200);
        if (!id || ids.has(id)) throw new TypeError('Invalid device identity');
        ids.add(id);
        const state = {};
        for (const field of BOOLEAN_FIELDS) {
            const value = source.state[field];
            if (value === undefined || value === null) continue;
            if (typeof value !== 'boolean') throw new TypeError('Invalid device setting');
            state[field] = value;
        }
        for (const field of ENUM_FIELDS) {
            const value = source.state[field];
            if (value !== undefined && value !== null) state[field] = text(value, 80);
        }
        for (const field of ['temperature', 'roomTemperature']) {
            const value = source.state[field];
            if (value === undefined || value === null) continue;
            if (typeof value !== 'number' || !Number.isFinite(value) || value < -50 || value > 70)
                throw new TypeError('Invalid device temperature');
            state[field] = value;
        }
        const capabilities = {};
        for (const key of Object.values(OPTIONS)) capabilities[key] = options(source.capabilities[key]);
        for (const key of ['nanoe', 'display']) {
            const value = source.capabilities[key];
            if (value !== undefined && value !== null && typeof value !== 'boolean')
                throw new TypeError('Invalid device capabilities');
            capabilities[key] = value === true;
        }
        const bounds = source.capabilities.temperature;
        if (bounds !== undefined && bounds !== null) {
            if (!object(bounds) || ![bounds.min, bounds.max, bounds.step].every(Number.isFinite) ||
                bounds.min < 10 || bounds.max > 45 || bounds.min > bounds.max || bounds.step <= 0 || bounds.step > 5)
                throw new TypeError('Invalid temperature limits');
            capabilities.temperature = {min: bounds.min, max: bounds.max, step: bounds.step};
        }
        return {id, name: text(source.name ?? 'Air conditioner'), room: text(source.room ?? ''),
            model: text(source.model ?? ''), online: source.online, state, capabilities};
    });
    return {connection: input.connection, busy: input.busy, error: text(input.error ?? '', 800), devices};
}

export function selectedDevice(state, id) {
    return state.devices.find(device => device.id === id) ?? state.devices[0] ?? null;
}

export function validateCommand(state, deviceId, field, value) {
    const device = state.devices.find(item => item.id === deviceId);
    if (state.connection !== 'ready' || state.busy || !device?.online)
        throw new Error('Air conditioner is unavailable');
    if (device.state[field] === undefined) throw new Error('This control is unavailable');
    if (BOOLEAN_FIELDS.has(field)) {
        if (typeof value !== 'boolean' || (field !== 'power' && !device.capabilities[field]))
            throw new Error('Unsupported setting');
    } else if (field === 'temperature') {
        const bounds = device.capabilities.temperature;
        const steps = bounds ? (value - bounds.min) / bounds.step : NaN;
        if (!bounds || typeof value !== 'number' || !Number.isFinite(value) ||
            value < bounds.min || value > bounds.max || Math.abs(steps - Math.round(steps)) > 0.00001)
            throw new Error('Temperature is outside the supported range');
    } else if (!ENUM_FIELDS.has(field) || !device.capabilities[OPTIONS[field]].includes(value)) {
        throw new Error('Unsupported setting');
    }
    return device;
}

export function humanLabel(value) {
    const labels = {auto: 'Auto', cool: 'Cool', heat: 'Heat', dry: 'Dry', fan: 'Fan only', off: 'Off', on: 'On',
        low: 'Low', medium: 'Medium', high: 'High', quiet: 'Quiet', turbo: 'Turbo', normal: 'Normal',
        swing: 'Swing', stopped: 'Stopped', stop: 'Stopped', fixed: 'Fixed', full: 'Full',
        '100': '100%', '80': '80%', '60': '60%', '40': '40%'};
    return labels[value] ?? String(value).replace(/[_-]+/g, ' ').replace(/^./, first => first.toUpperCase());
}
