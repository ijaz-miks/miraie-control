"""MirAIe cloud transport and confirmed AC state, independent of GNOME."""
from __future__ import annotations

import copy
import json
import math
import queue
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

AUTH_URL = 'https://auth.miraie.in/simplifi/v1/userManagement/login'
API_BASE = 'https://app.miraie.in/simplifi/v1'
# Public application identifier, shared by clients; not an account credential.
CLIENT_ID = 'PBcMcfG19njNCL8AOgvRzIC8AjQa'
FIELDS = {'power': 'ps', 'temperature': 'actmp', 'mode': 'acmd', 'fan': 'acfs',
          'verticalSwing': 'acvs', 'horizontalSwing': 'achs', 'nanoe': 'acng',
          'display': 'acdc', 'capacity': 'cnv'}
BOOL_FIELDS = {'power', 'nanoe', 'display'}
KEY_INDICES = {'power': 1, 'mode': 0, 'temperature': 2, 'fan': 4,
               'verticalSwing': 5, 'horizontalSwing': 6, 'nanoe': 7, 'display': 8, 'capacity': 28}


class MirAIeError(Exception):
    pass


class AuthenticationError(MirAIeError):
    pass


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def boolean(value):
    if value is True or value == 1 or str(value).lower() in ('on', 'online', 'true'):
        return True
    if value is False or value == 0 or str(value).lower() in ('off', 'offline', 'false'):
        return False
    return None


def room_temperature(value):
    result = number(value)
    return result if result is not None and -20 <= result <= 80 else None


def unwrap_status(payload):
    if not isinstance(payload, dict):
        raise MirAIeError('The device returned an unsupported status response.')
    for key in ('data', 'status', 'state', 'params', 'control_params', 'deviceStatus'):
        nested = payload.get(key)
        if isinstance(nested, dict):
            return {**payload, **unwrap_status(nested)}
    return payload


def normalize_status(raw):
    raw = unwrap_status(raw)
    result = {}
    for field, wire in FIELDS.items():
        if wire not in raw:
            continue
        value = raw[wire]
        if field in BOOL_FIELDS:
            value = boolean(value)
        elif field == 'temperature':
            value = number(value)
        else:
            value = str(value).lower()
        result[field] = value
    if 'rmtmp' in raw:
        result['roomTemperature'] = room_temperature(raw['rmtmp'])
    return result


def capabilities(raw, detail):
    state = normalize_status(raw)
    model = detail.get('_model', {})
    by_field = {}
    capability_types = {}
    if isinstance(model, dict):
        for entry in model.get('controlCapabilities', []):
            if not isinstance(entry, dict) or not isinstance(entry.get('attributes'), list) or not entry['attributes']:
                continue
            attribute = entry['attributes'][0]
            if isinstance(attribute, dict):
                for field, wire in FIELDS.items():
                    if attribute.get('key') == wire:
                        by_field[field] = attribute
                        capability_types[field] = entry.get('capability')
    def allowed(field, fallback):
        attribute = by_field.get(field, {})
        declared = attribute.get('values', attribute.get('intValues'))
        if isinstance(declared, list):
            return [str(value).lower() for value in declared if str(value).lower() in fallback]
        if model.get('controlCapabilities') and field not in by_field:
            return []
        return fallback
    modes = ['auto', 'cool', 'dry', 'fan']
    if 'heat' in by_field.get('mode', {}).get('values', []):
        modes.append('heat')
    declared = detail.get('supportedModes')
    if isinstance(declared, list):
        modes = [value for value in declared if value in (*modes, 'heat')]
    elif state.get('mode') == 'heat':
        modes.append('heat')
    minimum = number(detail.get('minTemperature', 16)) or 16
    maximum = number(detail.get('maxTemperature', 30)) or 30
    if not 10 <= minimum < maximum <= 40:
        minimum, maximum = 16, 30
    step = number(detail.get('temperatureStep', 1))
    if step not in (0.5, 1):
        step = 1
    range_info = by_field.get('temperature', {}).get('range', {})
    if isinstance(range_info, dict):
        limits = []
        for key, default in [('min', minimum), ('max', maximum), ('increment', step)]:
            entries = range_info.get(key, [])
            entry = next((item for item in entries if isinstance(item, dict) and item.get('unit') in ('celcius', 'celsius', 'C')), {}) if isinstance(entries, list) else {}
            limits.append(number(entry.get('value')) or default)
        if 10 <= limits[0] < limits[1] <= 40 and limits[2] in (0.5, 1):
            minimum, maximum, step = limits
    known_model = bool(model.get('controlCapabilities'))
    capacity_levels = by_field.get('capacity', {}).get('intValues')
    if not isinstance(capacity_levels, list):
        capacity_levels = detail.get('supportedCapacity', [])
    return {
        'modes': allowed('mode', modes) if 'mode' in state else [],
        'fanModes': allowed('fan', ['auto', 'low', 'medium', 'high', 'quiet']) if 'fan' in state else [],
        'temperature': {'min': minimum, 'max': maximum, 'step': step},
        'verticalSwing': allowed('verticalSwing', [str(i) for i in range(6)]) if 'verticalSwing' in state else [],
        'horizontalSwing': allowed('horizontalSwing', [str(i) for i in range(6)]) if 'horizontalSwing' in state else [],
        'nanoe': 'nanoe' in state and capability_types.get('nanoe') == 'NanoeG',
        'display': 'display' in state and (not known_model or 'display' in by_field),
        'localWiFi': bool(detail.get('_localAvailable')),
        # A status value alone does not establish which capacity levels a model supports.
        'capacity': [str(i) for i in capacity_levels if str(i) in ('0', '40', '50', '55', '60', '70', '80', '90', '100', '110')]
                    if isinstance(capacity_levels, list) else [],
    }


def control_payload(field, value, device):
    if field not in FIELDS or field not in KEY_INDICES or field not in device['state'] or device['state'][field] is None:
        raise MirAIeError('This control is unavailable for the selected AC.')
    caps = device['capabilities']
    if field in BOOL_FIELDS:
        if type(value) is not bool:
            raise MirAIeError('A switch requires an on or off value.')
        if field != 'power' and not caps.get(field):
            raise MirAIeError('This feature is unavailable for the selected AC.')
        wire = 'on' if value else 'off'
    elif field == 'temperature':
        if isinstance(value, bool) or number(value) is None:
            raise MirAIeError('Enter a valid temperature.')
        value = float(value)
        limits = caps['temperature']
        if not limits['min'] <= value <= limits['max'] or not math.isclose((value - limits['min']) / limits['step'], round((value - limits['min']) / limits['step']), abs_tol=1e-7):
            raise MirAIeError('The temperature is outside the supported range or step.')
        wire = str(int(value)) if value.is_integer() else str(value)
    else:
        allowed = caps.get({'mode': 'modes', 'fan': 'fanModes'}.get(field, field), [])
        if not isinstance(value, str) or value not in allowed:
            raise MirAIeError('This setting is unavailable for the selected AC.')
        wire = int(value) if field in ('verticalSwing', 'horizontalSwing', 'capacity') else value
    payload = {'ki': KEY_INDICES[field], 'cnt': 'an', 'sid': '1', FIELDS[field]: wire}
    if field == 'capacity':
        payload.update(acem='off', acpm='off')
    return payload


class CloudAPI:
    def __init__(self, opener=None):
        self.opener = opener or urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context()))
        self.token = None
        self.expiry = 0
        self.credentials = None

    def request(self, method, path, payload=None, authenticated=True):
        url = path if path.startswith('https://') else API_BASE + path
        body = None if payload is None else json.dumps(payload).encode()
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        if authenticated:
            if self.credentials and (not self.token or time.monotonic() >= self.expiry):
                self.login(*self.credentials)
            if not self.token:
                raise AuthenticationError('Sign in to your MirAIe account.')
            headers['Authorization'] = 'Bearer ' + self.token
        request = urllib.request.Request(url, body, headers, method=method)
        try:
            with self.opener.open(request, timeout=12) as response:
                data = response.read(2 * 1024 * 1024 + 1)
                if len(data) > 2 * 1024 * 1024:
                    raise MirAIeError('The cloud response was too large.')
                return json.loads(data) if data else {}
        except urllib.error.HTTPError as error:
            if error.code in (401, 406):
                self.token = None
                raise AuthenticationError('Your MirAIe session expired. Reconnect your account.') from None
            if error.code == 429:
                raise MirAIeError('MirAIe is limiting requests. Please wait before refreshing.') from None
            if not authenticated and error.code in (400, 403):
                raise AuthenticationError('MirAIe could not sign in. Check your account details.') from None
            raise MirAIeError(f'MirAIe returned HTTP {error.code}.') from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise MirAIeError('Cannot reach MirAIe. Check your Internet connection.') from None
        except (ValueError, TypeError):
            raise MirAIeError('MirAIe returned an unsupported response.') from None

    def login(self, username, password):
        username = username.strip()
        if not username or not password:
            raise AuthenticationError('Enter your MirAIe email or mobile number and password.')
        if '@' not in username:
            username = re.sub(r'[\s()-]', '', username)
            if re.fullmatch(r'\d{10}', username):
                username = '+91' + username
            if not re.fullmatch(r'\+\d{8,15}', username):
                raise AuthenticationError('Use your mobile number with its country code, or your email.')
        payload = {'clientId': CLIENT_ID, 'scope': 'an_14214235325', 'password': password,
                   'email' if '@' in username else 'mobile': username}
        result = self.request('POST', AUTH_URL, payload, authenticated=False)
        if not isinstance(result, dict) or not isinstance(result.get('accessToken'), str):
            raise AuthenticationError('MirAIe did not return a valid session.')
        self.token = result['accessToken']
        self.expiry = time.monotonic() + max(30, (number(result.get('expiresIn')) or 3600) - 120)
        self.credentials = (username, password)
        return result

    def homes(self):
        result = self.request('GET', '/homeManagement/homes')
        if isinstance(result, dict) and 'homeId' in result:
            result = [result]
        if not isinstance(result, list):
            raise MirAIeError('MirAIe did not return a list of homes.')
        return result

    def status(self, device_id):
        return self.request('GET', '/deviceManagement/devices/' + urllib.parse.quote(device_id, safe='') + '/mobile/status')

    def control(self, device_id, payload):
        return self.request('POST', '/deviceManagement/control', {'deviceId': device_id, 'control_params': payload})


class Controller:
    def __init__(self, credentials, changed, mqtt_factory=None, api=None, local_factory=None):
        self.credentials = credentials
        self.changed = changed
        self.api = api or CloudAPI()
        self.mqtt_factory = mqtt_factory
        self.local_factory = local_factory
        self.local = None
        self.lock = threading.Condition(threading.RLock())
        self.tasks = queue.Queue()
        self.clients = {}
        self.internal = {}
        self.sequence = {}
        self.field_sequence = {}
        self.epoch = 0
        self.command_sequence = 0
        self.running = True
        self.state = {'connection': 'signed-out', 'busy': False, 'error': '', 'devices': []}
        self.worker = threading.Thread(target=self._run, name='miraie-cloud', daemon=True)
        self.worker.start()
        self.tasks.put(('reload', None))

    def snapshot(self):
        with self.lock:
            snapshot = copy.deepcopy(self.state)
            for device in snapshot['devices']:
                if not device['online']:
                    previous = device['state'].get('power')
                    if isinstance(previous, bool):
                        device['lastKnownPower'] = previous
                    # Disconnected hardware has no confirmed current power state.
                    device['state']['power'] = None
            return snapshot

    def emit(self):
        self.changed(self.snapshot())

    def refresh(self):
        with self.lock:
            if self.state['busy']:
                return
            self.state.update(busy=True, error='')
            self.tasks.put(('refresh', None))
        self.emit()

    def reload(self):
        with self.lock:
            self.state.update(connection='connecting', busy=True, error='')
            self.tasks.put(('reload', None))
        self.emit()

    def command(self, device_id, field, value):
        with self.lock:
            device = next((d for d in self.state['devices'] if d['id'] == device_id), None)
            if self.state['busy']:
                raise MirAIeError('Another AC change is still being confirmed.')
            if self.state['connection'] != 'ready' or not device or not device['online']:
                raise MirAIeError('The selected AC is unavailable. Refresh before changing it.')
            if time.time() - device.get('updatedAt', 0) > 180:
                raise MirAIeError('The displayed status is stale. Refresh before changing the AC.')
            payload = control_payload(field, value, device)
            job = uuid.uuid4().hex
            self.state.update(busy=True, error='')
            self.tasks.put(('command', (device_id, field, value, payload)))
        self.emit()
        return job

    def _disconnect(self):
        self.epoch += 1
        clients, self.clients = self.clients, {}
        for client in clients.values():
            client.disconnect()
            client.loop_stop()

    def close(self):
        self.running = False
        self.tasks.put(('stop', None))
        with self.lock:
            self.lock.notify_all()
        self.worker.join(timeout=15)
        self._disconnect()
        if self.local:
            self.local.close()

    def _run(self):
        while self.running:
            try:
                kind, args = self.tasks.get(timeout=60)
            except queue.Empty:
                kind, args = 'refresh', None
                with self.lock:
                    if self.state['busy']:
                        continue
                    self.state['busy'] = bool(self.api.credentials)
                if self.api.credentials:
                    self.emit()
            if kind == 'stop':
                break
            try:
                if kind == 'reload':
                    self._load()
                elif kind == 'refresh':
                    if self.api.credentials:
                        self._refresh()
                elif kind == 'command':
                    self._command(*args)
            except Exception as error:
                with self.lock:
                    self.state.update(error=str(error) if isinstance(error, MirAIeError) else 'The AC controller could not complete this request.')
                    if kind != 'command':
                        local_available = False
                        for device in self.state['devices']:
                            recent_local = device.get('transport') == 'local' and time.time() - device.get('updatedAt', 0) < 180
                            device['online'] = bool(recent_local and self.local and self.local.discover(device['id']))
                            local_available = local_available or device['online']
                        self.state['connection'] = 'ready' if local_available else 'error' if isinstance(error, AuthenticationError) else 'offline'
                        if local_available:
                            self.state['error'] = 'Cloud unavailable. Recent local Wi-Fi controls remain available.'
            finally:
                with self.lock:
                    self.state['busy'] = not self.tasks.empty()
                self.emit()

    def _load(self):
        self._disconnect()
        if self.local:
            self.local.close()
        self.local = self.local_factory(lambda identity, payload: self._update(identity, payload, source='local')) if self.local_factory else None
        with self.lock:
            self.state.update(connection='connecting', error='', devices=[], busy=True)
            self.internal.clear()
            self.sequence.clear()
            self.field_sequence.clear()
        self.emit()
        credentials = self.credentials()
        if not credentials:
            self.api.credentials = None
            self.api.token = None
            with self.lock:
                self.state.update(connection='signed-out', busy=False)
            return
        self.api.login(*credentials)
        discovered = []
        for home in self.api.homes():
            if not isinstance(home, dict):
                continue
            for room in home.get('spaces', []):
                if not isinstance(room, dict):
                    continue
                for source in room.get('devices', []):
                    if not isinstance(source, dict) or source.get('category', '').upper() not in ('AC', 'ODMIRB-AC') or not source.get('deviceId'):
                        continue
                    identity = str(source['deviceId'])
                    if identity in self.internal:
                        continue
                    topic = source.get('topic', [])
                    topic = topic[0] if isinstance(topic, list) and topic else topic if isinstance(topic, str) else ''
                    if not isinstance(topic, str) or any(c in topic for c in ('#', '+', '\x00')):
                        topic = ''
                    self.internal[identity] = {'home': str(home.get('homeId', '')), 'topic': topic, 'detail': source.copy(), 'raw': {}}
                    discovered.append({'id': identity, 'name': str(source.get('deviceName', 'Air conditioner')),
                                       'room': str(room.get('spaceName', '')), 'model': str(source.get('modelName') or source.get('modelId') or ''),
                                       'online': False, 'state': {}, 'capabilities': capabilities({}, source), 'updatedAt': 0})
        if discovered:
            try:
                ids = ','.join(self.internal)
                details = self.api.request('GET', '/deviceManagement/devices/deviceId/' + urllib.parse.quote(ids, safe=','))
                if isinstance(details, list):
                    for detail in details:
                        if isinstance(detail, dict) and detail.get('deviceId') in self.internal:
                            self.internal[detail['deviceId']]['detail'].update(detail)
            except MirAIeError:
                pass  # Basic controls can work without optional model metadata.
            models = {}
            for item in self.internal.values():
                detail = item['detail']
                model_id = detail.get('modelId')
                if not model_id:
                    continue
                if model_id not in models:
                    try:
                        model = self.api.request('GET', '/deviceManagement/deviceModels/modelId/' + urllib.parse.quote(str(model_id), safe=''))
                        models[model_id] = model if isinstance(model, dict) else {}
                    except MirAIeError:
                        models[model_id] = {}
                detail['_model'] = models[model_id]
            if self.local:
                for identity, item in self.internal.items():
                    item['detail']['_localAvailable'] = self.local.add(identity, item['detail'])
        with self.lock:
            self.state['devices'] = discovered
        self._refresh()
        self._connect_mqtt()

    def _refresh(self):
        old_token = self.api.token
        if time.monotonic() >= self.api.expiry:
            self.api.login(*self.api.credentials)
        successes = 0
        failure = None
        for identity in list(self.internal):
            try:
                status = self.api.status(identity)
                self._update(identity, status, source='rest')
                successes += 1
            except AuthenticationError:
                self.api.login(*self.api.credentials)
                self._update(identity, self.api.status(identity), source='rest')
                successes += 1
            except MirAIeError as error:
                failure = error
                with self.lock:
                    item = next(d for d in self.state['devices'] if d['id'] == identity)
                    item['online'] = False
        if not successes and self.internal and failure:
            raise failure
        with self.lock:
            self.state.update(connection='ready', error='' if failure is None else 'Some ACs could not be refreshed.')
        if old_token != self.api.token and self.clients:
            self._disconnect()
            self._connect_mqtt()

    def _update(self, identity, payload, source, retained=False):
        raw = unwrap_status(payload)
        with self.lock:
            internal = self.internal.get(identity)
            device = next((d for d in self.state['devices'] if d['id'] == identity), None)
            if not internal or not device:
                return
            cached = source == 'mqtt' and retained
            if cached:
                # Subscription replays contain last-known settings, even for offline ACs.
                # They may fill unknown fields but cannot replace a fresh snapshot.
                for key, value in raw.items():
                    internal['raw'].setdefault(key, value)
            else:
                internal['raw'].update(raw)
            detail = internal['detail']
            device['state'] = normalize_status(internal['raw'])
            device['capabilities'] = capabilities(internal['raw'], detail)
            device['model'] = str(detail.get('modelName') or detail.get('modelNumber') or device['model'])
            if source == 'rest':
                explicit = boolean(raw.get('onlineStatus', raw.get('isOnline')))
                if explicit is not None:
                    device['online'] = explicit
            elif source == 'local':
                # A direct authenticated response from the discovered AC proves LAN reachability.
                device['online'] = True
            # MQTT telemetry is independent of availability. Only /connectionStatus
            # supplies MQTT connection evidence, never /status (retained or live).
            if not cached:
                device['transport'] = 'local' if source == 'local' else 'cloud'
                device['updatedAt'] = time.time()
                self.sequence[identity] = self.sequence.get(identity, 0) + 1
                generations = self.field_sequence.setdefault(identity, {})
                for field, wire in FIELDS.items():
                    if wire in raw:
                        generations[field] = self.sequence[identity]
            self.lock.notify_all()
        self.emit()

    def _connect_mqtt(self):
        if self.mqtt_factory is None:
            return
        for home in set(item['home'] for item in self.internal.values()):
            if not home:
                continue
            client = self.mqtt_factory('miraie-gnome-' + uuid.uuid4().hex)
            epoch = self.epoch
            client.username_pw_set(home, self.api.token)
            client.tls_set_context(ssl.create_default_context())
            client.reconnect_delay_set(1, 60)
            def connected(instance, _userdata, _flags, reason, _properties, selected_home=home, generation=epoch):
                if reason.is_failure or generation != self.epoch:
                    return
                for item in list(self.internal.values()):
                    if item['home'] == selected_home and item['topic']:
                        instance.subscribe([(item['topic'] + '/status', 1), (item['topic'] + '/connectionStatus', 1)])
            def message(_instance, _userdata, received, generation=epoch):
                if generation != self.epoch or len(received.payload) > 128 * 1024:
                    return
                try:
                    payload = json.loads(received.payload)
                    match = next((identity for identity, item in list(self.internal.items()) if received.topic in (item['topic'] + '/status', item['topic'] + '/connectionStatus')), None)
                    if match is None:
                        return
                    if received.topic.endswith('/connectionStatus'):
                        self._connection_status(match, payload, retained=received.retain)
                    else:
                        self._update(match, payload, source='mqtt', retained=received.retain)
                except (ValueError, TypeError, MirAIeError):
                    return
            client.on_connect = connected
            client.on_message = message
            client.connect_async('mqtt.miraie.in', 8883, 30)
            client.loop_start()
            self.clients[home] = client

    def _connection_status(self, identity, payload, retained=False):
        value = payload
        if isinstance(payload, dict):
            value = payload.get('onlineStatus', payload.get('isOnline', payload.get('status', payload.get('connectionStatus'))))
        online = boolean(value)
        if online is None or (retained and online):
            # A historical positive flag cannot overrule a fresh REST offline result.
            return
        with self.lock:
            device = next((d for d in self.state['devices'] if d['id'] == identity), None)
            if device:
                device['online'] = online
                self.lock.notify_all()
        self.emit()

    def _command(self, identity, field, value, payload):
        internal = self.internal[identity]
        with self.lock:
            device = next(d for d in self.state['devices'] if d['id'] == identity)
            if self.state['connection'] != 'ready' or not device['online']:
                raise MirAIeError('The selected AC is unavailable. Refresh before changing it.')
            baseline = self.field_sequence.get(identity, {}).get(field, 0)
            self.command_sequence = getattr(self, 'command_sequence', 0) + 1
            payload['sid'] = str(self.command_sequence)
        def confirmed():
            device = next(d for d in self.state['devices'] if d['id'] == identity)
            actual = device['state'].get(field)
            matches = math.isclose(actual, float(value), abs_tol=0.01) if field == 'temperature' and isinstance(actual, (float, int)) else actual == value
            return device['online'] and self.field_sequence.get(identity, {}).get(field, 0) > baseline and matches
        client = self.clients.get(internal['home'])
        local_sent = self.local.send(identity, payload) if self.local else False
        if local_sent:
            pass
        elif client and client.is_connected() and internal['topic']:
            result = client.publish(internal['topic'] + '/control', json.dumps(payload), qos=1, retain=False)
            if result.rc != 0:
                raise MirAIeError('The command could not be sent. Refresh and try again.')
            result.wait_for_publish(timeout=4)
            # Never retry an uncertain write through another transport.
            if not result.is_published():
                raise MirAIeError('The command was not acknowledged. Refresh to check the AC.')
        else:
            self.api.control(identity, payload)
        deadline = time.monotonic() + 12
        while self.running and time.monotonic() < deadline:
            with self.lock:
                if confirmed():
                    return
                self.lock.wait(timeout=1)
                if confirmed():
                    return
            if not local_sent and time.monotonic() < deadline:
                self._update(identity, self.api.status(identity), source='rest')
        raise MirAIeError('The AC did not confirm this setting. Refresh to check its current state.')
