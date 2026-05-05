"""DataUpdateCoordinator for Philips Air Fan with MQTT over WebSocket."""
from __future__ import annotations

import asyncio
import hashlib
import hmac as hmac_mod
import json
import logging
import ssl
import time
from typing import Any
from urllib.parse import quote

import aiohttp
import websockets
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    API_HOST,
    APP_ID,
    APP_SECRET,
    CLIENT_ID,
    CLIENT_SECRET,
    CONF_DEVICE_ID,
    CONF_ENDUSER_ID,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN,
    CONF_TOKEN_EXPIRY,
    CONF_USERNAME,
    DOMAIN,
    TOKEN_URL,
    USERINFO_URL,
)

_LOGGER = logging.getLogger(__name__)

MQTT_RECONNECT_INTERVAL = 30
MQTT_URL_LIFETIME = 3500  # Refresh URL before 1h expiry (presigned URL valid 1h)
TOKEN_REFRESH_MARGIN = 86400  # Refresh when less than 24h remaining


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
        self._entry = entry
        self._token: str = entry.data[CONF_TOKEN]
        self._device_id: str = entry.data[CONF_DEVICE_ID]
        self._enduser_id: str = entry.data[CONF_ENDUSER_ID]
        self._refresh_token: str | None = entry.data.get(CONF_REFRESH_TOKEN)
        self._token_expiry: float = entry.data.get(CONF_TOKEN_EXPIRY, 0)
        self._username: str | None = entry.data.get(CONF_USERNAME)
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

    async def _ensure_valid_token(self) -> None:
        """Check if JWT is near expiry and refresh if possible."""
        if not self._refresh_token:
            _LOGGER.debug("No refresh token available, cannot auto-refresh")
            return

        now = time.time()
        if self._token_expiry and (self._token_expiry - now) > TOKEN_REFRESH_MARGIN:
            return  # Token still has more than 24h remaining

        _LOGGER.info("JWT token nearing expiry, attempting automatic refresh")
        try:
            new_token, new_expiry = await self._refresh_jwt()
            self._token = new_token
            self._token_expiry = new_expiry

            # Persist new tokens in config entry
            new_data = {**self._entry.data, CONF_TOKEN: new_token, CONF_TOKEN_EXPIRY: new_expiry}
            self.hass.config_entries.async_update_entry(self._entry, data=new_data)
            _LOGGER.info("JWT token refreshed successfully, valid until %s", time.ctime(new_expiry))
        except Exception:
            _LOGGER.exception(
                "Failed to refresh JWT token automatically. "
                "Will trigger re-authentication if token becomes invalid."
            )

    async def _refresh_jwt(self) -> tuple[str, float]:
        """Use OAuth refresh_token to obtain a fresh JWT.

        Flow:
        1. refresh_token → new access_token
        2. access_token → userinfo → sub → username
        3. serverTime → timestamp
        4. HMAC signature → POST getToken → new JWT
        """
        async with aiohttp.ClientSession() as session:
            # Step 1: Refresh OAuth access token
            data = {
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
            }
            async with session.post(TOKEN_URL, data=data) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise TokenRefreshError(f"OAuth token refresh failed ({resp.status}): {body}")
                tokens = await resp.json()
                access_token = tokens["access_token"]
                # Update refresh_token if a new one was issued
                if "refresh_token" in tokens:
                    self._refresh_token = tokens["refresh_token"]
                    new_data = {**self._entry.data, CONF_REFRESH_TOKEN: self._refresh_token}
                    self.hass.config_entries.async_update_entry(self._entry, data=new_data)

            # Step 2: Get username from userinfo
            headers = {"Authorization": f"Bearer {access_token}"}
            async with session.get(USERINFO_URL, headers=headers) as resp:
                if resp.status != 200:
                    raise TokenRefreshError(f"Userinfo request failed ({resp.status})")
                userinfo = await resp.json()
                sub = userinfo["sub"]
                username = f"PHILIPS:{sub}"

            # Step 3: Get server timestamp
            async with session.get(f"{API_HOST}/device/serverTime/") as resp:
                time_data = await resp.json()
                timestamp = time_data["data"]["timestamp2"]

            # Step 4: Compute HMAC signature and get JWT
            fmt = f"app_id={APP_ID}&timestamp={timestamp}&username={quote(username)}"
            hmac1 = hmac_mod.new(APP_SECRET.encode(), fmt.encode(), hashlib.sha256).hexdigest()
            signature = hmac_mod.new(username.encode(), hmac1.encode(), hashlib.sha256).hexdigest()

            fog_headers = {
                "Content-Type": "application/json; charset=utf-8",
                "Signature": signature,
            }
            fog_data = {"username": username, "timestamp": timestamp, "app_id": APP_ID}

            async with session.post(
                f"{API_HOST}/enduser/v2/getToken/", json=fog_data, headers=fog_headers
            ) as resp:
                result = await resp.json()

            if result.get("meta", {}).get("code") != 0:
                # Fallback: try login endpoint without signature
                _LOGGER.debug("getToken with signature failed, trying login endpoint")
                login_data = {"username": username, "app_id": APP_ID}
                async with session.put(
                    f"{API_HOST}/enduser/login/", json=login_data
                ) as resp:
                    result = await resp.json()
                if result.get("meta", {}).get("code") != 0:
                    raise TokenRefreshError(f"Both getToken and login endpoints failed: {result}")

            new_jwt = result["data"]["token"]
            # JWT valid for 7 days
            new_expiry = time.time() + 7 * 86400
            return new_jwt, new_expiry

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
                    "JWT token is invalid or expired. Triggering re-authentication."
                )
                self._connected = False
                self._entry.async_start_reauth(self.hass)
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
        await self._ensure_valid_token()
        mqtt_info = await self._get_mqtt_info()
        ws_url = mqtt_info["host"]
        client_id = mqtt_info["client_id"]
        endpoint = mqtt_info["endpoint"]

        _LOGGER.debug("Connecting to MQTT WebSocket at %s", endpoint)
        ssl_context = await self.hass.async_add_executor_job(ssl.create_default_context)

        async with websockets.connect(
            ws_url,
            subprotocols=["mqtt"],
            ssl=ssl_context,
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


class TokenRefreshError(Exception):
    """Raised when automatic token refresh fails."""
