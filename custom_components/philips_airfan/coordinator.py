"""DataUpdateCoordinator for Philips Air Fan with MQTT over WebSocket."""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
from typing import Any

import aiohttp
import websockets

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    API_HOST,
    APP_ID,
    CONF_DEVICE_ID,
    CONF_ENDUSER_ID,
    CONF_TOKEN,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

MQTT_RECONNECT_INTERVAL = 30
MQTT_URL_LIFETIME = 3500  # Refresh URL before 1h expiry (presigned URL valid 1h)


def _encode_remaining_length(length: int) -> bytes:
    """Encode MQTT remaining length field."""
    encoded = bytearray()
    while True:
        byte = length % 128
        length //= 128
        if length > 0:
            byte |= 0x80
        encoded.append(byte)
        if length == 0:
            break
    return bytes(encoded)


def _build_connect(client_id: str) -> bytes:
    """Build MQTT CONNECT packet."""
    protocol_name = b"\x00\x04MQTT"
    protocol_level = b"\x04"  # 3.1.1
    connect_flags = b"\x02"  # Clean session
    keep_alive = b"\x00\x3c"  # 60s
    cid = client_id.encode()
    cid_len = len(cid).to_bytes(2, "big")
    variable_header = protocol_name + protocol_level + connect_flags + keep_alive
    payload = cid_len + cid
    remaining = variable_header + payload
    return b"\x10" + _encode_remaining_length(len(remaining)) + remaining


def _build_subscribe(packet_id: int, topic: str) -> bytes:
    """Build MQTT SUBSCRIBE packet."""
    tb = topic.encode()
    tl = len(tb).to_bytes(2, "big")
    pid = packet_id.to_bytes(2, "big")
    payload = pid + tl + tb + b"\x00"  # QoS 0
    return b"\x82" + _encode_remaining_length(len(payload)) + payload


def _build_publish(topic: str, payload: bytes) -> bytes:
    """Build MQTT PUBLISH packet (QoS 0)."""
    tb = topic.encode()
    tl = len(tb).to_bytes(2, "big")
    remaining = tl + tb + payload
    return b"\x30" + _encode_remaining_length(len(remaining)) + remaining


def _parse_publish(data: bytes) -> tuple[str, bytes] | None:
    """Parse incoming MQTT PUBLISH packet, return (topic, payload) or None."""
    if not data or (data[0] & 0xF0) != 0x30:
        return None
    idx = 1
    multiplier = 1
    remaining_length = 0
    while True:
        byte = data[idx]
        remaining_length += (byte & 0x7F) * multiplier
        multiplier *= 128
        idx += 1
        if (byte & 0x80) == 0:
            break
    topic_len = int.from_bytes(data[idx : idx + 2], "big")
    idx += 2
    topic = data[idx : idx + topic_len].decode()
    idx += topic_len
    if (data[0] & 0x06) > 0:
        idx += 2  # Skip packet ID for QoS > 0
    payload = data[idx:]
    return topic, payload


class PhilipsAirFanCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that maintains MQTT WebSocket connection to Philips cloud."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=None)
        self._token: str = entry.data[CONF_TOKEN]
        self._device_id: str = entry.data[CONF_DEVICE_ID]
        self._enduser_id: str = entry.data[CONF_ENDUSER_ID]
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._task: asyncio.Task | None = None
        self._ping_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._connected = False

    @property
    def device_id(self) -> str:
        """Return device ID."""
        return self._device_id

    @property
    def connected(self) -> bool:
        """Return whether MQTT is connected."""
        return self._connected

    async def _async_update_data(self) -> dict[str, Any]:
        """Return current data (populated by MQTT push)."""
        return self.data or {}

    async def _get_mqtt_info(self) -> dict[str, Any]:
        """Get MQTT WebSocket info from API."""
        headers = {
            "Authorization": f"jwt {self._token}",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "HomeAssistant/PhilipsAirFan",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{API_HOST}/enduser/v2/mqttInfo/",
                json={"device_id": [self._device_id]},
                headers=headers,
            ) as resp:
                if resp.status == 401:
                    raise InvalidToken("JWT token expired or invalid")
                resp.raise_for_status()
                data = await resp.json()

        if data.get("meta", {}).get("code") != 0:
            raise ConnectionError(f"mqttInfo error: {data}")

        infos = data.get("data", {}).get("mqttinfos", [])
        if not infos:
            raise ConnectionError("No MQTT info returned")
        return infos[0]

    async def _mqtt_loop(self) -> None:
        """Main MQTT connection loop with auto-reconnect."""
        while not self._stop_event.is_set():
            try:
                await self._run_mqtt_session()
            except InvalidToken:
                _LOGGER.error(
                    "JWT token is invalid or expired. Please reconfigure the integration with a new token."
                )
                self._connected = False
                return  # Stop retrying on auth error
            except Exception:
                _LOGGER.exception(
                    "MQTT session error, reconnecting in %ss", MQTT_RECONNECT_INTERVAL
                )
                self._connected = False
            if not self._stop_event.is_set():
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=MQTT_RECONNECT_INTERVAL
                    )
                except asyncio.TimeoutError:
                    pass

    async def _run_mqtt_session(self) -> None:
        """Single MQTT session."""
        mqtt_info = await self._get_mqtt_info()
        ws_url = mqtt_info["host"]
        client_id = mqtt_info["client_id"]
        endpoint = mqtt_info["endpoint"]

        _LOGGER.debug("Connecting to MQTT WebSocket at %s", endpoint)
        ssl_context = ssl.create_default_context()

        async with websockets.connect(
            ws_url,
            subprotocols=["mqtt"],
            ssl=ssl_context,
            extra_headers={"Host": endpoint},
            ping_interval=None,
            close_timeout=5,
        ) as ws:
            self._ws = ws

            # CONNECT
            await ws.send(_build_connect(client_id))
            connack = await asyncio.wait_for(ws.recv(), timeout=10)
            if len(connack) < 4 or connack[0] != 0x20 or connack[3] != 0x00:
                raise ConnectionError(f"MQTT CONNACK failed: {connack.hex()}")

            _LOGGER.info("MQTT connected to Philips cloud for device %s", self._device_id)
            self._connected = True

            # SUBSCRIBE
            shadow_get_accepted = f"$aws/things/{self._device_id}/shadow/get/accepted"
            shadow_update_accepted = f"$aws/things/{self._device_id}/shadow/update/accepted"
            shadow_update_rejected = f"$aws/things/{self._device_id}/shadow/update/rejected"

            topics = [shadow_get_accepted, shadow_update_accepted, shadow_update_rejected]
            for i, topic in enumerate(topics, start=1):
                await ws.send(_build_subscribe(i, topic))
                await asyncio.wait_for(ws.recv(), timeout=10)

            # Request initial state
            shadow_get = f"$aws/things/{self._device_id}/shadow/get"
            await ws.send(_build_publish(shadow_get, b""))

            # Start keepalive ping task
            self._ping_task = asyncio.create_task(self._ping_loop(ws))

            # Read messages until disconnect or URL expiry
            try:
                start = asyncio.get_event_loop().time()
                while not self._stop_event.is_set():
                    elapsed = asyncio.get_event_loop().time() - start
                    if elapsed > MQTT_URL_LIFETIME:
                        _LOGGER.debug("MQTT URL nearing expiry, reconnecting")
                        break
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=50)
                    except asyncio.TimeoutError:
                        continue
                    if isinstance(raw, str):
                        raw = raw.encode()
                    self._handle_message(raw)
            finally:
                self._connected = False
                if self._ping_task:
                    self._ping_task.cancel()
                    self._ping_task = None
                self._ws = None

    async def _ping_loop(self, ws) -> None:
        """Send MQTT PINGREQ every 45s to keep connection alive."""
        try:
            while True:
                await asyncio.sleep(45)
                await ws.send(b"\xc0\x00")  # PINGREQ
        except (asyncio.CancelledError, websockets.exceptions.ConnectionClosed):
            pass

    def _handle_message(self, data: bytes) -> None:
        """Handle incoming MQTT message."""
        # Handle PINGRESP
        if data == b"\xd0\x00":
            return

        parsed = _parse_publish(data)
        if parsed is None:
            return

        topic, payload_bytes = parsed
        try:
            payload = json.loads(payload_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError):
            _LOGGER.debug("Non-JSON message on %s", topic)
            return

        if "get/accepted" in topic or "update/accepted" in topic:
            reported = payload.get("state", {}).get("reported", {})
            if reported:
                current = dict(self.data) if self.data else {}
                current.update(reported)
                self.async_set_updated_data(current)
                _LOGGER.debug(
                    "State updated: %s",
                    {k: v for k, v in reported.items() if k.startswith("D0")},
                )
        elif "update/rejected" in topic:
            _LOGGER.warning("Shadow update rejected: %s", payload)

    async def async_send_command(self, properties: dict[str, Any]) -> None:
        """Send a command to the device via MQTT shadow update."""
        desired = {
            "CommandType": "app",
            "EnduserId": self._enduser_id,
            "DeviceId": self._device_id,
        }
        desired.update(properties)
        payload = json.dumps({"state": {"desired": desired}}).encode()

        shadow_update = f"$aws/things/{self._device_id}/shadow/update"
        if self._ws:
            try:
                await self._ws.send(_build_publish(shadow_update, payload))
                _LOGGER.debug("Sent command: %s", properties)
            except Exception:
                _LOGGER.exception("Failed to send command")
        else:
            _LOGGER.error("MQTT not connected, cannot send command")

    async def async_start(self) -> None:
        """Start the MQTT loop."""
        self._stop_event.clear()
        self._task = asyncio.create_task(self._mqtt_loop())

    async def async_stop(self) -> None:
        """Stop the MQTT loop."""
        self._stop_event.set()
        if self._ws:
            await self._ws.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None


class InvalidToken(Exception):
    """Raised when JWT token is expired or invalid."""
