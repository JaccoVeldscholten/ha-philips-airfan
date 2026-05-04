"""Constants for the Philips Air Fan integration."""

DOMAIN = "philips_airfan"

API_HOST = "https://www.api.air.philips.com"
APP_ID = "9fd505fa9c7111e9a1e3061302926720"

# OAuth / Token refresh constants
CLIENT_ID = "-XsK7O6iEkLml77yDGDUi0ku"
CLIENT_SECRET = "V34BlAhuilIdOx0Imo16rGQ2"  # noqa: S105  # Public app credential
TOKEN_URL = "https://cdc.accounts.home.id/oidc/op/v1.0/4_JGZWlP8eQHpEqkvQElolbA/oauth/token"
USERINFO_URL = "https://cdc.accounts.home.id/oidc/op/v1.0/4_JGZWlP8eQHpEqkvQElolbA/userinfo"
APP_SECRET = f"a_{APP_ID}"

# Config entry keys
CONF_TOKEN = "token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_TOKEN_EXPIRY = "token_expiry"
CONF_DEVICE_ID = "device_id"
CONF_USERNAME = "username"
CONF_ENDUSER_ID = "enduser_id"
CONF_DEVICE_NAME = "device_name"
CONF_DEVICE_MODEL = "device_model"

# Property keys
PROP_POWER = "D03102"
PROP_MODE = "D0310C"
PROP_SPEED = "D0310D"
PROP_OSCILLATION = "D0320F"
PROP_TIMER_SET = "D03110"
PROP_TIMER_REMAIN = "D03211"
PROP_BRIGHTNESS = "D03130"
PROP_TEMPERATURE = "D0313B"
PROP_STANDBY = "D03105"

# Mode values
MODE_SPEED1 = 1
MODE_SPEED2 = 2
MODE_SPEED3 = 3
MODE_SLEEP = 17
MODE_NATURAL = -126

PRESET_MODES = {
    "Speed 1": MODE_SPEED1,
    "Speed 2": MODE_SPEED2,
    "Speed 3": MODE_SPEED3,
    "Sleep": MODE_SLEEP,
    "Natural Wind": MODE_NATURAL,
}
PRESET_MODE_REVERSE = {v: k for k, v in PRESET_MODES.items()}

# Oscillation
OSCILLATION_ON = 90
OSCILLATION_OFF = 0
OSCILLATION_REPORTED_ON = 23040  # 90 * 256

PLATFORMS = ["fan", "sensor"]
