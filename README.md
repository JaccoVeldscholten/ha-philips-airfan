# Philips Air Fan for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/JaccoVeldscholten/ha-philips-airfan)](https://github.com/JaccoVeldscholten/ha-philips-airfan/releases)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=JaccoVeldscholten&repository=ha-philips-airfan&category=integration)

Custom Home Assistant integration for **Philips Air+ connected fans** (CX3550 and similar models using the Philips Air+ / Fogcloud platform).

> ⚠️ This integration is for Philips fans that use the **Philips Air+** app, NOT the older Philips Air Purifier app or the new Versuni/DA platform.

## Supported Devices

| Model | Name | Status |
|-------|------|--------|
| CX3550 | Philips Series 3000i Fan | ✅ Tested |
| CX5550 | Philips Series 5000i Fan | 🔄 Should work (untested) |

Other Philips fans using the Air+ app with Fogcloud/AWS IoT backend should work too. If you have a different model, please open an issue!

## Features

- ✅ **Fan control** — Turn on/off, preset modes (Speed 1-3, Sleep, Natural Wind)
- ✅ **Oscillation** — Enable/disable oscillation
- ✅ **Temperature sensor** — Built-in room temperature reading
- ✅ **Timer** — Timer remaining sensor
- ✅ **Real-time updates** — Uses MQTT push via AWS IoT (no polling!)
- ✅ **Diagnostic sensors** — All raw device properties exposed

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click the three dots menu → **Custom repositories**
3. Add `https://github.com/JaccoVeldscholten/ha-philips-airfan` with category **Integration**
4. Click **Install**
5. Restart Home Assistant

### Manual

1. Copy `custom_components/philips_airfan` to your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

### Step 1: Get your API token

The integration requires a JWT token from the Philips Air cloud API. The token is valid for **approximately 7 days**.

#### Token Helper Script

```bash
# Install dependencies
pip install playwright aiohttp
playwright install chromium

# Run the helper
python3 get_token.py
```

This will:
1. Open a browser window
2. You log in with your Philips Air+ account
3. The script automatically obtains and prints your JWT token

That's it! Copy the token and paste it into Home Assistant.

### Step 2: Add the integration

1. Go to **Settings** → **Devices & Services** → **Add Integration**
2. Search for "Philips Air Fan"
3. Paste your JWT token
4. The integration will discover your device automatically

### Token Renewal

When your token expires (after ~7 days), the integration will become unavailable. To fix:

1. Run `get_token.py` again to get a new token
2. Go to the integration in HA, click **Reconfigure**
3. Paste the new token

## How it Works

```
┌─────────────┐      OAuth2/PKCE       ┌──────────────────────┐
│  Your Phone │  ──────────────────────▶│  Philips Accounts    │
│  (get_token)│◀─────────────────────── │  (cdc.accounts.home) │
└─────────────┘     access_token        └──────────────────────┘
       │
       │  getToken (HMAC signed)
       ▼
┌──────────────────────┐     JWT token    ┌─────────────────────┐
│  Philips Air Cloud   │ ◀──────────────▶ │   Home Assistant    │
│  (api.air.philips)   │                  │   (this integration)│
└──────────────────────┘                  └─────────────────────┘
       │                                           │
       │  mqttInfo (presigned AWS URL)             │
       ▼                                           ▼
┌──────────────────────┐      WebSocket      ┌─────────────┐
│  AWS IoT Core        │ ◀═══════════════════▶│  MQTT Client │
│  (Device Shadow)     │    MQTT over WSS     └─────────────┘
└──────────────────────┘
       ▲
       │  MQTT (WiFi)
       │
┌──────────────────────┐
│  Philips CX3550 Fan  │
│  (MXChip IoT module) │
└──────────────────────┘
```

The integration connects directly to AWS IoT via WebSocket using a presigned URL (refreshed every ~58 minutes). Commands and state updates flow through the AWS IoT Device Shadow mechanism, providing near-instant response times.

## Device Properties

| Property | Description | Values |
|----------|-------------|--------|
| `D03102` | Power | 0=off, 1=on |
| `D0310C` | Mode | 1=Speed 1, 2=Speed 2, 3=Speed 3, 17=Sleep, -126=Natural Wind |
| `D0310D` | Current speed (read-only) | 1-3 |
| `D0320F` | Oscillation | 0=off, 23040=on (90°) |
| `D03110` | Timer setting | 0=off, N=(N-1) hours |
| `D03211` | Timer remaining (minutes) | 0-720 |
| `D0313B` | Temperature (°C) | Room temperature |
| `D03130` | Display brightness | 0-100 |
| `D03105` | Standby flag | 0=active, 100=standby |

## Contributing

Contributions are welcome! If you have a different Philips Air+ fan model, please:

1. Run the monitor script to capture your device's properties
2. Open an issue with the property mapping
3. Submit a PR to add support

## Disclaimer

This integration is not affiliated with, endorsed by, or connected to Philips or Versuni. It was created through reverse engineering of the Philips Air+ mobile app for personal home automation use. Use at your own risk.

## License

MIT License - see [LICENSE](LICENSE) for details.
