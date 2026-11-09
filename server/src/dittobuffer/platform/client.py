"""Publish this server as a named HiveMQ room.

A room is just a name. The server signs a small snapshot with its RSA key and
publishes it as a *retained* MQTT document under ``dittobuffer/v1/<room>/room``.
The app subscribes to that name, verifies the signature and pins the key. No
domain, DNS record, hosted website or account is needed anywhere.

The HTTP platform remains optional: when ``url`` is configured the same signed
document is also registered there, but joining a room never depends on it.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request

from dittobuffer.paths import ROOT
from dittobuffer.platform import mqtt
from dittobuffer.platform import rsa_min

log = logging.getLogger('dittobuffer.platform')
CONFIG = ROOT / 'data/platform.json'
KEY = ROOT / 'data/platform-key.json'
STATE = ROOT / 'data/platform-room.json'
ROOM_TOPIC = 'dittobuffer/v1/{room}/room'
MAX_ROOM_CHARS = 48


def normalise_room(value) -> str:
    """Turn whatever the player typed into one MQTT-safe room name.

    Names are not domains: ``Fairview Community!`` becomes
    ``fairview-community``. There is no registration, no owner and no DNS.
    """
    raw = str(value or '').strip()
    if not raw:
        raise ValueError('Give the room a name')
    slug = re.sub(r'[^a-z0-9]+', '-', raw.lower()).strip('-')
    slug = re.sub(r'-{2,}', '-', slug)[:MAX_ROOM_CHARS].strip('-')
    if len(slug) < 2:
        raise ValueError('Use at least two letters or digits in the room name')
    return slug


# Older callers (and the admin form) still say "domain"; it means room name.
normalise_domain = normalise_room


def load_key() -> dict:
    try:
        value = json.loads(KEY.read_text(encoding='utf8'))
        if value.get('n') and value.get('d'):
            return value
    except (OSError, ValueError):
        pass
    key = rsa_min.generate()
    KEY.parent.mkdir(parents=True, exist_ok=True)
    KEY.write_text(json.dumps(key, indent=2), encoding='utf8')
    return key


def public_key() -> dict:
    return rsa_min.public(load_key())


def settings(config) -> dict:
    raw = config.get('platform') or {}
    room = str(raw.get('room') or raw.get('domain') or '').strip()
    return {
        'mode': str(raw.get('mode') or 'local'),
        'room': room,
        # Kept so existing configs and the admin tab keep working.
        'domain': room,
        'url': str(raw.get('url') or '').strip().rstrip('/'),
        'manage_token': str(raw.get('manage_token') or ''),
        'enabled': bool(raw.get('enabled', bool(room))),
        'publish_seconds': max(15, min(600, int(raw.get('publish_seconds', 60)))),
        'title': str(raw.get('title') or 'Dittobuffer Server'),
        'description': str(raw.get('description') or ''),
        'broker_host': str(raw.get('broker_host') or mqtt.HOST),
        'broker_port': int(raw.get('broker_port') or mqtt.PORT),
    }


def save_settings(config, value, config_path) -> dict:
    # Admin callers used to pass SimpleNamespace(config=...); accept both shapes
    # so saving a room name can never fail before it is written.
    config = getattr(config, 'config', config)
    if not isinstance(config, dict):
        raise ValueError('Platform settings need a configuration object')
    if not isinstance(value, dict):
        raise ValueError('Platform settings must be an object')
    current = settings(config)
    for key in ('mode', 'room', 'domain', 'url', 'manage_token', 'title', 'description',
                'broker_host'):
        if key in value:
            current[key] = str(value[key] or '')
    if 'room' in value or 'domain' in value:
        typed = value.get('room', value.get('domain'))
        current['room'] = normalise_room(typed) if str(typed or '').strip() else ''
        current['domain'] = current['room']
    if 'enabled' in value:
        current['enabled'] = bool(value['enabled'])
    if 'publish_seconds' in value:
        current['publish_seconds'] = max(15, min(600, int(value['publish_seconds'])))
    if 'broker_port' in value:
        current['broker_port'] = int(value['broker_port'])
    if current['mode'] not in ('local', 'host', 'player'):
        raise ValueError('Platform mode must be local, host or player')
    config['platform'] = current | {'enabled': current['enabled']}
    import pathlib
    path = pathlib.Path(config_path)
    path.write_text(json.dumps(config, indent=2) + '\n', encoding='utf8')
    return current


def _request(url: str, body=None, headers=None, method=None, timeout=12):
    data = None if body is None else rsa_min.canonical(body)
    request = urllib.request.Request(url, data=data, method=method or ('POST' if data else 'GET'),
                                     headers={'User-Agent': 'Dittobuffer-Platform/1.0',
                                              'Content-Type': 'application/json',
                                              **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode('utf8'))


def bootstrap(url: str, domain: str) -> dict:
    return _request(f'{url}/api/rooms/{domain}/bootstrap')


def platform_public(url: str) -> dict:
    return _request(f'{url}/api/status')['platform_public']


def verify_bootstrap(value: dict, url: str | None = None) -> dict:
    """Verify the platform signature on a bootstrap document."""
    key = value.get('platform_public') or (platform_public(url) if url else None)
    if not key:
        raise ValueError('Bootstrap has no platform key')
    unsigned = {name: item for name, item in value.items() if name != 'sig'}
    if not rsa_min.verify(key, rsa_min.canonical(unsigned), str(value.get('sig') or '')):
        raise ValueError('Bootstrap signature is invalid')
    host = value.get('host_public')
    state = value.get('state')
    if state and host:
        signed = state.get('signed') or {}
        body = state.get('body') or {}
        envelope = {**signed, 'body': body}
        if not rsa_min.verify(host, rsa_min.canonical({k: v for k, v in envelope.items() if k != 'sig'}),
                              str(envelope.get('sig') or '')):
            raise ValueError('Room state is not signed by the host')
    return value


def room_body(game, db=None) -> dict:
    from dittobuffer.gameplay import liveops
    value = liveops.snapshot(game, db)
    value['server'] = {
        'title': settings(game.config)['title'],
        'description': settings(game.config)['description'],
        'profile': game.config.get('profile', ''),
        'target': '0.429.2',
    }
    return value


def document(room: str, body: dict, title: str = '', description: str = '',
             key: dict | None = None) -> dict:
    """Build the retained, self-signed room document the app reads."""
    key = key or load_key()
    stamp = int(time.time() * 1000)
    value = {
        'v': 1,
        'kind': 'room',
        'room': room,
        'title': title or room,
        'description': description or '',
        'host_public': rsa_min.public(key),
        'fingerprint': rsa_min.fingerprint(key),
        'state': {'body': body, 'issued_ms': stamp},
        'issued_ms': stamp,
        'nonce': rsa_min.secrets.token_hex(12),
    }
    value['sig'] = rsa_min.sign(key, rsa_min.canonical(value))
    return value


def verify_room(value: dict, room: str) -> dict:
    """Reject anything that is not this room name signed by its host key."""
    if not isinstance(value, dict):
        raise ValueError('Room document must be an object')
    if str(value.get('room') or '') != room:
        raise ValueError('That document belongs to a different room')
    host = value.get('host_public') or {}
    if not host.get('n'):
        raise ValueError('Room document has no host key')
    signature = str(value.get('sig') or '')
    unsigned = {key: item for key, item in value.items() if key != 'sig'}
    if not rsa_min.verify(host, rsa_min.canonical(unsigned), signature):
        raise ValueError('Room signature is invalid')
    return value


def discover(room: str, timeout: float = 25.0, host: str = mqtt.HOST,
             port: int = mqtt.PORT) -> dict:
    """Read the retained room document straight from HiveMQ by name."""
    client = mqtt.MqttClient(host, port, client_id='dittobuffer-join-' + rsa_min.secrets.token_hex(4))
    topic = ROOM_TOPIC.format(room=room)
    try:
        client.connect()
        client.subscribe(topic)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for received, payload in client.poll(1.0):
                if received != topic:
                    continue
                try:
                    return json.loads(payload.decode('utf8'))
                except (UnicodeError, ValueError):
                    raise ValueError('The room document is not valid JSON') from None
    finally:
        client.close()
    raise ValueError(f'No room named "{room}" is online right now')


class Publisher:
    """Publishes the retained room document, reconnecting as needed."""

    def __init__(self, game, config_path):
        self.game = game
        self.config_path = config_path
        self.stop_event = threading.Event()
        self.thread = None
        self.client = None
        self.last_error = ''
        self.last_ms = 0
        self.room = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name='platform-publisher', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None

    def _connection(self):
        value = settings(self.game.config)
        if self.client and self.client.alive:
            return self.client
        self.client = mqtt.MqttClient(value['broker_host'], value['broker_port'],
                                      client_id='dittobuffer-host-' + rsa_min.secrets.token_hex(4))
        self.client.connect()
        return self.client

    def _run(self):
        while not self.stop_event.is_set():
            try:
                self.publish()
                self.last_error = ''
            except (OSError, ValueError, KeyError, mqtt.MqttError,
                    urllib.error.URLError) as error:
                self.last_error = str(error)[:200]
                log.warning('Platform publish failed: %s', error)
                if self.client:
                    try:
                        self.client.close()
                    except Exception:
                        pass
                    self.client = None
            interval = settings(self.game.config)['publish_seconds']
            self.stop_event.wait(interval)

    def publish(self):
        value = settings(self.game.config)
        if not value['enabled'] or value['mode'] != 'host' or not value['room']:
            return
        value['room'] = normalise_room(value['room'])
        key = load_key()
        with self.game.db.transaction() as db:
            body = room_body(self.game, db)
        doc = document(value['room'], body, value['title'], value['description'], key)
        self._connection().publish(ROOM_TOPIC.format(room=value['room']),
                                   json.dumps(doc, separators=(',', ':')).encode('utf8'),
                                   retain=True)
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({'room': value['room'], 'published_ms': int(time.time() * 1000),
                                     'body': body, 'fingerprint': doc['fingerprint']},
                                    indent=2), encoding='utf8')
        self.last_ms = int(time.time() * 1000)
        if value['url']:
            # Optional registry copy; failures never block the MQTT room.
            try:
                self.room = _request(f"{value['url']}/api/rooms/{value['room']}/room",
                                     {'document': doc},
                                     headers={'X-Platform-Token': value['manage_token']}
                                     if value['manage_token'] else {})
            except (OSError, urllib.error.URLError, ValueError) as error:
                log.info('Optional platform registry unavailable: %s', error)


_publisher: Publisher | None = None


def start(game, config_path):
    global _publisher
    value = settings(game.config)
    if not value['enabled'] or value['mode'] != 'host' or not value['room']:
        return None
    if _publisher and _publisher.game is not game:
        _publisher.stop()
    _publisher = Publisher(game, config_path)
    _publisher.start()
    return _publisher


def publish_now(config, db_path=None) -> dict:
    """One signed retained publish without constructing a Game (admin action)."""
    value = settings(config)
    if not str(value['room']).strip():
        raise ValueError('Give the room a name first')
    value['room'] = normalise_room(value['room'])
    if value['mode'] != 'host':
        raise ValueError('Switch the platform mode to host first')
    key = load_key()
    from dittobuffer.gameplay import liveops
    body = {'liveops': liveops.load(config), 'logical_ms': int(time.time() * 1000),
            'server': {'title': value['title'], 'description': value['description'],
                       'profile': config.get('profile', ''), 'target': '0.429.2'}}
    if db_path:
        import sqlite3
        try:
            with sqlite3.connect(str(db_path), timeout=5) as db:
                body['players'] = db.execute('SELECT COUNT(*) FROM players').fetchone()[0]
        except Exception:
            body['players'] = 0
    doc = document(value['room'], body, value['title'], value['description'], key)
    client = mqtt.MqttClient(value['broker_host'], value['broker_port'],
                             client_id='dittobuffer-host-' + rsa_min.secrets.token_hex(4))
    try:
        client.connect()
        client.publish(ROOM_TOPIC.format(room=value['room']),
                       json.dumps(doc, separators=(',', ':')).encode('utf8'), retain=True)
    finally:
        client.close()
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({'room': value['room'], 'published_ms': int(time.time() * 1000),
                                 'body': body, 'fingerprint': doc['fingerprint']},
                                indent=2), encoding='utf8')
    if value['url']:
        # The registry copy is optional: HiveMQ already carries the signed room.
        # An older registry without /room must never fail a successful publish.
        try:
            _request(f"{value['url']}/api/rooms/{value['room']}/room", {'document': doc},
                     headers={'X-Platform-Token': value['manage_token']} if value['manage_token'] else {})
        except (OSError, urllib.error.URLError, ValueError) as error:
            log.info('Optional platform registry unavailable: %s', error)
    return {'room': value['room'], 'fingerprint': doc['fingerprint'], 'broker': value['broker_host']}


def status(game) -> dict:
    value = settings(game.config)
    return {
        'settings': value,
        'public_key': public_key(),
        'fingerprint': rsa_min.fingerprint(public_key()),
        'publisher': {
            'running': bool(_publisher and _publisher.thread and _publisher.thread.is_alive()),
            'last_ms': _publisher.last_ms if _publisher else 0,
            'last_error': _publisher.last_error if _publisher else '',
        },
    }
