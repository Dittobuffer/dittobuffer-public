"""HiveMQ room bridge.

Subscribes to ``dittobuffer/v1/+/up/#`` and verifies the signed envelopes sent
by hosts. Accepted state is re-published on ``dittobuffer/v1/<domain>/state``
so app clients can follow the same room over MQTT. The bridge also publishes
the platform-hosted room as a retained, signed document on
``dittobuffer/v1/<domain>/room``; that retained document is what the phone
app discovers when a player types the room name. HTTP snapshots stay available
when the bridge is offline.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time

from . import mqtt_min

PREFIX = "dittobuffer/v1"
log = logging.getLogger("dittobuffer-platform.mqtt")


class Bridge:
    def __init__(self, platform, host: str = mqtt_min.HOST, port: int = mqtt_min.PORT,
                 tls: bool = True, enabled: bool = True, room_document=None):
        self.platform = platform
        self.host, self.port, self.tls = host, port, tls
        self.enabled = enabled
        self.room_document = room_document
        self.client: mqtt_min.MqttClient | None = None
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self._room_lock = threading.Lock()
        self._last_room_publish = 0.0
        self._announced_room = ""
        self._last_room_error = ""

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if not self.enabled or self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="platform-mqtt", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.client:
            self.client.close()

    def _set(self, connected: bool, error: str = "") -> None:
        self.platform.mqtt.update(connected=connected, error=error, last_ms=int(time.time() * 1000))

    def _run(self) -> None:
        delay = 2.0
        while not self.stop_event.is_set():
            try:
                # A fresh client id per reconnect: the public broker drops a
                # duplicate session id, which looked like a permanent outage.
                client = mqtt_min.MqttClient(self.host, self.port,
                                             client_id="dittobuffer-platform-" + secrets.token_hex(3))
                client.connect()
                # Hosts publish a retained, self-signed room document next to
                # their envelopes. Listening for it lets a one-click name bind
                # itself without any registration step.
                client.subscribe(PREFIX + "/+/up/#", PREFIX + "/+/room")
                self.client = client
                self._last_room_publish = 0.0
                self._set(True)
                log.info("HiveMQ room bridge connected to %s:%s", self.host, self.port)
                delay = 2.0
                self.publish_room_now()
                while not self.stop_event.is_set():
                    for topic, payload in client.poll(1.0):
                        self._receive(topic, payload)
                    self._publish_room()
            except (OSError, mqtt_min.MqttError, ValueError) as error:
                self._set(False, str(error)[:180])
                log.warning("HiveMQ bridge disconnected: %s", error)
            finally:
                if self.client:
                    self.client.close()
                    self.client = None
                self._announced_room = ""
            self.stop_event.wait(delay)
            delay = min(delay * 2, 60.0)

    # -------------------------------------------------------------- room presence
    def publish_room_now(self) -> bool:
        """Publish the retained room document immediately, if connected."""
        return self._publish_room(force=True)

    def _publish_room(self, force: bool = False) -> bool:
        if not self.room_document or not self.client or not self.client.alive:
            return False
        with self._room_lock:
            now = time.monotonic()
            if not force and now - self._last_room_publish < 30.0:
                return False
            try:
                document = self.room_document()
                if not document:
                    return False
                room = str(document.get("room") or "")
                if not room:
                    return False
                # Register locally first: an invalid or conflicting document
                # must never overwrite a good retained room on the broker.
                self.platform.register_room(room, document)
                self.client.publish(f"{PREFIX}/{room}/room",
                                    json.dumps(document, separators=(",", ":"),
                                               ensure_ascii=False).encode("utf-8"),
                                    retain=True)
                self._last_room_publish = now
                self.platform.mqtt["published"] = self.platform.mqtt.get("published", 0) + 1
                self.platform.mqtt["room_published_ms"] = int(time.time() * 1000)
                self.platform.mqtt["room_error"] = ""
                self._last_room_error = ""
                if self._announced_room != room:
                    self._announced_room = room
                    log.info("Room %s is published for app clients", room)
                return True
            except (OSError, mqtt_min.MqttError, ValueError, KeyError, TypeError) as error:
                message = str(error)[:200]
                self.platform.mqtt["room_error"] = message
                if message != self._last_room_error:
                    self._last_room_error = message
                    log.warning("Could not publish room document: %s", message)
                return False

    # ---------------------------------------------------------------- inbound
    def _receive(self, topic: str, payload: bytes) -> None:
        parts = topic.split("/")
        # dittobuffer / v1 / <room> / room   (retained, self-signed)
        # dittobuffer / v1 / <room> / up / <kind>
        if len(parts) < 4 or parts[:2] != ["dittobuffer", "v1"]:
            return
        domain = parts[2]
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeError, ValueError):
            log.warning("Dropped a non-JSON room payload for %s", domain)
            return
        self.platform.mqtt["received"] += 1
        if len(parts) == 4 and parts[3] == "room":
            try:
                self.platform.register_room(domain, value)
                log.info("Room %s is live from its host", domain)
            except KeyError:
                log.warning("Room document for an unknown room %s", domain)
            except ValueError as error:
                log.warning("Rejected room %s document: %s", domain, error)
            return
        if len(parts) < 5 or parts[3] != "up":
            return
        kind = parts[4]
        try:
            if kind == "state":
                room = self.platform.publish_state(domain, value)
                self._publish(domain, "state", value)
            elif kind == "settings":
                room = self.platform.publish_settings(domain, value)
                self._publish(domain, "settings", value)
            elif kind == "presence":
                room = self.platform.get(domain)
                if room is None:
                    return
                body = self.platform_presence(domain, value)
                if body:
                    self.platform.touch_presence(domain, body.get("client_id", ""), body.get("version", 0))
            else:
                return
            if room:
                log.info("Room %s accepted a signed %s publish", domain, kind)
        except KeyError:
            log.warning("Signed payload for an unknown room %s", domain)
        except ValueError as error:
            log.warning("Rejected room %s payload: %s", domain, error)

    def platform_presence(self, domain: str, value: dict) -> dict | None:
        from . import rsa_min

        room = self.platform.get(domain)
        if not room:
            return None
        try:
            return rsa_min.open_envelope(value, room["host_public"], domain=domain)
        except ValueError:
            return None

    # --------------------------------------------------------------- outbound
    def _publish(self, domain: str, kind: str, value: dict) -> None:
        if not self.client or not self.client.alive:
            return
        try:
            self.client.publish(f"{PREFIX}/{domain}/{kind}", json.dumps(value, separators=(",", ":")).encode("utf-8"))
            self.platform.mqtt["published"] += 1
        except (OSError, mqtt_min.MqttError) as error:
            log.warning("Room relay failed: %s", error)

    def publish(self, domain: str, kind: str, value: dict | bytes) -> None:
        if self.client and self.client.alive:
            body = value if isinstance(value, bytes) else json.dumps(value, separators=(",", ":")).encode("utf-8")
            try:
                self.client.publish(f"{PREFIX}/{domain}/{kind}", body)
            except (OSError, mqtt_min.MqttError):
                pass
