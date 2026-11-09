"""A ~200 line MQTT 3.1.1 client for the HiveMQ public broker.

Only what rooms need: TLS connect, subscribe, publish (QoS 0), ping and clean
disconnect. No third-party dependency, which keeps the platform installable on
a bare Python and inside the Dittobuffer mobile app.

Default endpoint: ``broker.hivemq.com:8883`` (TLS certificate checked with the
system trust store). The public broker is a transport only; every Dittobuffer
payload is signed and verified by the room, so an open broker cannot forge or
hijack a server.
"""

from __future__ import annotations

import socket
import ssl
import struct
import threading
import time

HOST = "broker.hivemq.com"
PORT = 8883
KEEPALIVE = 45


class MqttError(RuntimeError):
    pass


def _remaining_length(value: int) -> bytes:
    out = bytearray()
    while True:
        digit = value % 128
        value //= 128
        out.append(digit | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _string(value: str) -> bytes:
    raw = value.encode("utf-8")
    return struct.pack("!H", len(raw)) + raw


def _packet(kind: int, flags: int, payload: bytes) -> bytes:
    return bytes([(kind << 4) | flags]) + _remaining_length(len(payload)) + payload


def _read_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        part = sock.recv(size - len(chunks))
        if not part:
            raise MqttError("Broker closed the connection")
        chunks.extend(part)
    return bytes(chunks)


def _read_packet(sock: socket.socket) -> tuple[int, int, bytes]:
    header = _read_exact(sock, 1)[0]
    multiplier, value = 1, 0
    for _ in range(4):
        digit = _read_exact(sock, 1)[0]
        value += (digit & 0x7F) * multiplier
        multiplier *= 128
        if not digit & 0x80:
            break
    return header >> 4, header & 0x0F, _read_exact(sock, value)


class MqttClient:
    """Small blocking MQTT client. ``poll`` drives incoming traffic."""

    def __init__(self, host: str = HOST, port: int = PORT, client_id: str = "",
                 tls: bool = True, timeout: float = 20.0):
        self.host, self.port = host, port
        self.client_id = client_id or f"dittobuffer-{int(time.time())}"
        self.tls = tls
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self.lock = threading.RLock()
        self._buffer: list[tuple[str, bytes]] = []
        self._alive = False
        self._last_ping = 0.0

    # ------------------------------------------------------------- lifecycle
    def connect(self) -> None:
        self.close()
        raw = socket.create_connection((self.host, self.port), timeout=self.timeout)
        raw.settimeout(self.timeout)
        if self.tls:
            context = ssl.create_default_context()
            raw = context.wrap_socket(raw, server_hostname=self.host)
        self.sock = raw
        payload = _string("MQTT") + bytes([4]) + bytes([0x02]) + struct.pack("!H", KEEPALIVE)
        payload += _string(self.client_id)
        with self.lock:
            self.sock.sendall(_packet(1, 0, payload))
            kind, _, body = _read_packet(self.sock)
        if kind != 2 or len(body) < 2 or body[1] != 0:
            raise MqttError("HiveMQ rejected the connection")
        self._alive = True
        self._last_ping = time.monotonic()

    def close(self) -> None:
        self._alive = False
        if self.sock is not None:
            try:
                self.sock.sendall(_packet(14, 0, b""))
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    @property
    def alive(self) -> bool:
        return self._alive

    # --------------------------------------------------------------- outbound
    def subscribe(self, *topics: str) -> None:
        payload = struct.pack("!H", 1)
        for topic in topics:
            payload += _string(topic) + bytes([0])
        with self.lock:
            self.sock.sendall(_packet(8, 0x02, payload))
            kind, _, body = _read_packet(self.sock)
        if kind != 9:
            raise MqttError("Subscribe was not acknowledged")

    def publish(self, topic: str, payload: bytes, retain: bool = False) -> None:
        """Publish at QoS 0. ``retain`` keeps the document for new readers."""
        with self.lock:
            self.sock.sendall(_packet(3, 1 if retain else 0, _string(topic) + payload))

    # ---------------------------------------------------------------- inbound
    def poll(self, idle_timeout: float = 1.0) -> list[tuple[str, bytes]]:
        """Return newly received messages; reconnects are the caller's job."""
        if not self.sock:
            raise MqttError("Not connected")
        messages, self._buffer = self._buffer, []
        deadline = time.monotonic() + idle_timeout
        while time.monotonic() < deadline:
            remaining = max(0.05, deadline - time.monotonic())
            self.sock.settimeout(min(remaining, 1.0))
            try:
                kind, flags, body = _read_packet(self.sock)
            except (socket.timeout, TimeoutError):
                break
            except OSError as error:
                self._alive = False
                raise MqttError(str(error)) from error
            if kind == 3:
                topic, rest = _parse_string(body)
                if (flags >> 1) & 3:
                    rest = rest[2:]
                messages.append((topic, rest))
            elif kind == 13:
                pass
            elif kind == 14:
                self._alive = False
        if time.monotonic() - self._last_ping > KEEPALIVE / 2:
            with self.lock:
                self.sock.sendall(_packet(12, 0, b""))
            self._last_ping = time.monotonic()
        return messages


def _parse_string(raw: bytes) -> tuple[str, bytes]:
    size = struct.unpack("!H", raw[:2])[0]
    return raw[2:2 + size].decode("utf-8"), raw[2 + size:]
