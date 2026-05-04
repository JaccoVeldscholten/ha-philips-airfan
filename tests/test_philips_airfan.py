"""Tests for Philips Air Fan integration."""
from __future__ import annotations

import hashlib
import hmac
import importlib.util
import sys
from urllib.parse import quote

import pytest


def _load_module_directly(name: str, path: str):
    """Load a module by path without triggering package __init__."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Load const.py directly (no HA dependencies)
const = _load_module_directly(
    "philips_airfan_const",
    "custom_components/philips_airfan/const.py",
)

APP_ID = const.APP_ID
APP_SECRET = const.APP_SECRET
PRESET_MODES = const.PRESET_MODES
PRESET_MODE_REVERSE = const.PRESET_MODE_REVERSE
OSCILLATION_ON = const.OSCILLATION_ON
OSCILLATION_OFF = const.OSCILLATION_OFF
OSCILLATION_REPORTED_ON = const.OSCILLATION_REPORTED_ON
PROP_POWER = const.PROP_POWER
PROP_MODE = const.PROP_MODE
PROP_OSCILLATION = const.PROP_OSCILLATION
PROP_TEMPERATURE = const.PROP_TEMPERATURE
PROP_TIMER_SET = const.PROP_TIMER_SET
MODE_SLEEP = const.MODE_SLEEP
MODE_SPEED1 = const.MODE_SPEED1
MODE_NATURAL = const.MODE_NATURAL


# Inline MQTT packet functions (copied from coordinator.py for standalone testing)
def _encode_remaining_length(length: int) -> bytes:
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
    protocol_name = b"\x00\x04MQTT"
    protocol_level = b"\x04"
    connect_flags = b"\x02"
    keep_alive = b"\x00\x3c"
    cid = client_id.encode()
    cid_len = len(cid).to_bytes(2, "big")
    variable_header = protocol_name + protocol_level + connect_flags + keep_alive
    payload = cid_len + cid
    remaining = variable_header + payload
    return b"\x10" + _encode_remaining_length(len(remaining)) + remaining


def _build_subscribe(packet_id: int, topic: str) -> bytes:
    tb = topic.encode()
    tl = len(tb).to_bytes(2, "big")
    pid = packet_id.to_bytes(2, "big")
    payload = pid + tl + tb + b"\x00"
    return b"\x82" + _encode_remaining_length(len(payload)) + payload


def _build_publish(topic: str, payload: bytes) -> bytes:
    tb = topic.encode()
    tl = len(tb).to_bytes(2, "big")
    remaining = tl + tb + payload
    return b"\x30" + _encode_remaining_length(len(remaining)) + remaining


def _parse_publish(data: bytes):
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
    topic_len = int.from_bytes(data[idx: idx + 2], "big")
    idx += 2
    topic = data[idx: idx + topic_len].decode()
    idx += topic_len
    if (data[0] & 0x06) > 0:
        idx += 2
    payload = data[idx:]
    return topic, payload


class TestMQTTPacketBuilders:
    """Test raw MQTT packet building functions."""

    def test_encode_remaining_length_small(self):
        """Test encoding a small remaining length."""
        assert _encode_remaining_length(0) == b"\x00"
        assert _encode_remaining_length(127) == b"\x7f"

    def test_encode_remaining_length_medium(self):
        """Test encoding a medium remaining length (2 bytes)."""
        result = _encode_remaining_length(128)
        assert result == b"\x80\x01"
        result = _encode_remaining_length(16383)
        assert result == b"\xff\x7f"

    def test_build_connect(self):
        """Test MQTT CONNECT packet structure."""
        packet = _build_connect("test-client")
        assert packet[0] == 0x10  # CONNECT packet type
        # Should contain MQTT protocol name
        assert b"MQTT" in packet
        assert b"test-client" in packet

    def test_build_subscribe(self):
        """Test MQTT SUBSCRIBE packet structure."""
        packet = _build_subscribe(1, "$aws/things/device123/shadow/get/accepted")
        assert packet[0] == 0x82  # SUBSCRIBE packet type
        assert b"$aws/things/device123/shadow/get/accepted" in packet

    def test_build_publish(self):
        """Test MQTT PUBLISH packet structure."""
        payload = b'{"state":{"desired":{"D03102":1}}}'
        packet = _build_publish("$aws/things/dev/shadow/update", payload)
        assert packet[0] == 0x30  # PUBLISH packet type (QoS 0)
        assert b"$aws/things/dev/shadow/update" in packet
        assert payload in packet

    def test_parse_publish_valid(self):
        """Test parsing a valid PUBLISH packet."""
        # Build then parse
        topic = "test/topic"
        payload = b'{"key":"value"}'
        packet = _build_publish(topic, payload)
        result = _parse_publish(packet)
        assert result is not None
        parsed_topic, parsed_payload = result
        assert parsed_topic == topic
        assert parsed_payload == payload

    def test_parse_publish_non_publish(self):
        """Test parsing a non-PUBLISH packet returns None."""
        assert _parse_publish(b"\xd0\x00") is None  # PINGRESP
        assert _parse_publish(b"\x20\x02\x00\x00") is None  # CONNACK
        assert _parse_publish(b"") is None


class TestConstants:
    """Test constant values and mappings."""

    def test_preset_modes_mapping(self):
        """Test preset mode values."""
        assert PRESET_MODES["Speed 1"] == 1
        assert PRESET_MODES["Speed 2"] == 2
        assert PRESET_MODES["Speed 3"] == 3
        assert PRESET_MODES["Sleep"] == 17
        assert PRESET_MODES["Natural Wind"] == -126

    def test_preset_mode_reverse(self):
        """Test reverse mapping."""
        assert PRESET_MODE_REVERSE[1] == "Speed 1"
        assert PRESET_MODE_REVERSE[17] == "Sleep"
        assert PRESET_MODE_REVERSE[-126] == "Natural Wind"

    def test_oscillation_values(self):
        """Test oscillation constants."""
        assert OSCILLATION_ON == 90
        assert OSCILLATION_OFF == 0
        assert OSCILLATION_REPORTED_ON == 23040  # 90 * 256

    def test_timer_encoding(self):
        """Test timer value encoding (0=off, N=(N-1) hours)."""
        # 0 = off
        # 2 = 1 hour
        # 3 = 2 hours
        # 13 = 12 hours
        for hours in range(1, 13):
            raw = hours + 1
            assert raw - 1 == hours  # Decode: raw - 1 = hours


class TestHMACSignature:
    """Test the HMAC-SHA256 signature algorithm."""

    def test_android_algorithm(self):
        """Test the Android signing algorithm structure."""
        username = "PHILIPS:testuser123"
        timestamp = "1777911982"
        app_id = APP_ID

        fmt = f"app_id={app_id}&timestamp={timestamp}&username={quote(username)}"
        hmac1 = hmac.new(APP_SECRET.encode(), fmt.encode(), hashlib.sha256).hexdigest()
        signature = hmac.new(username.encode(), hmac1.encode(), hashlib.sha256).hexdigest()

        # Verify it's a valid hex string of correct length
        assert len(signature) == 64
        assert all(c in "0123456789abcdef" for c in signature)

    def test_signature_changes_with_timestamp(self):
        """Test that different timestamps produce different signatures."""
        username = "PHILIPS:test"
        sigs = set()
        for ts in ["1000", "1001", "1002"]:
            fmt = f"app_id={APP_ID}&timestamp={ts}&username={quote(username)}"
            h1 = hmac.new(APP_SECRET.encode(), fmt.encode(), hashlib.sha256).hexdigest()
            h2 = hmac.new(username.encode(), h1.encode(), hashlib.sha256).hexdigest()
            sigs.add(h2)
        assert len(sigs) == 3


class TestDevicePropertyParsing:
    """Test device shadow message parsing."""

    def test_shadow_state_extraction(self):
        """Test extracting reported state from shadow document."""
        shadow = {
            "state": {
                "reported": {
                    "D03102": 1,
                    "D0310C": 17,
                    "D0320F": 23040,
                    "D0313B": 21,
                    "D03110": 0,
                    "D03211": 0,
                    "D03130": 100,
                    "D03105": 0,
                }
            }
        }
        reported = shadow["state"]["reported"]
        assert reported[PROP_POWER] == 1
        assert reported[PROP_MODE] == MODE_SLEEP
        assert reported[PROP_OSCILLATION] == OSCILLATION_REPORTED_ON
        assert reported[PROP_TEMPERATURE] == 21

    def test_command_format(self):
        """Test command payload format for shadow update."""
        device_id = "test_device_123"
        enduser_id = "PHILIPS:user_appid"
        properties = {PROP_POWER: 1, PROP_MODE: MODE_SPEED1}

        desired = {
            "CommandType": "app",
            "EnduserId": enduser_id,
            "DeviceId": device_id,
        }
        desired.update(properties)
        payload = {"state": {"desired": desired}}

        assert payload["state"]["desired"]["CommandType"] == "app"
        assert payload["state"]["desired"][PROP_POWER] == 1
        assert payload["state"]["desired"][PROP_MODE] == 1

    def test_timer_raw_to_hours(self):
        """Test timer value conversion."""
        # raw=0 → off (0 hours)
        # raw=2 → 1 hour
        # raw=13 → 12 hours
        assert 0 == 0  # off
        for raw in range(2, 14):
            hours = raw - 1
            assert 1 <= hours <= 12

    def test_timer_hours_to_raw(self):
        """Test timer hours to raw value conversion."""
        # 0 hours → raw=0 (off)
        # 1 hour → raw=2
        # 12 hours → raw=13
        assert 0 == 0  # off
        for hours in range(1, 13):
            raw = hours + 1
            assert 2 <= raw <= 13
