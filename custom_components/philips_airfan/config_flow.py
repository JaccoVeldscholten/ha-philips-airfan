"""Config flow for Philips Air Fan."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import (
    API_HOST,
    APP_ID,
    CONF_DEVICE_ID,
    CONF_DEVICE_MODEL,
    CONF_DEVICE_NAME,
    CONF_ENDUSER_ID,
    CONF_TOKEN,
    CONF_USERNAME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class PhilipsAirFanConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Philips Air Fan."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step: user provides JWT token."""
        errors = {}

        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()

            # Validate token by calling deviceList
            try:
                device_info = await self._validate_token(token)
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during validation")
                errors["base"] = "unknown"
            else:
                device_id = device_info["device_id"]
                await self.async_set_unique_id(f"philips_airfan_{device_id}")
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=device_info.get("name", "Philips Air Fan"),
                    data={
                        CONF_TOKEN: token,
                        CONF_DEVICE_ID: device_id,
                        CONF_USERNAME: device_info["username"],
                        CONF_ENDUSER_ID: device_info["enduser_id"],
                        CONF_DEVICE_NAME: device_info.get("name", "Philips Air Fan"),
                        CONF_DEVICE_MODEL: device_info.get("model", "Unknown"),
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication with a new token."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle re-auth token input."""
        errors = {}

        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            try:
                await self._validate_token(token)
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "unknown"
            else:
                entry = self.hass.config_entries.async_get_entry(
                    self.context["entry_id"]
                )
                if entry:
                    self.hass.config_entries.async_update_entry(
                        entry, data={**entry.data, CONF_TOKEN: token}
                    )
                    await self.hass.config_entries.async_reload(entry.entry_id)
                    return self.async_abort(reason="reauth_successful")
                return self.async_abort(reason="unknown")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}),
            errors=errors,
        )

    async def _validate_token(self, token: str) -> dict[str, Any]:
        """Validate token and return device info."""
        headers = {
            "Authorization": f"jwt {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{API_HOST}/enduser/deviceList/",
                headers=headers,
            ) as resp:
                if resp.status == 401:
                    raise InvalidAuth
                if resp.status != 200:
                    raise CannotConnect
                data = await resp.json()

        if data.get("meta", {}).get("code") != 0:
            raise InvalidAuth

        devices = data.get("data", [])
        if not devices:
            raise NoDevices

        # Use first device
        device = devices[0]
        info = device.get("device_info", {})
        return {
            "device_id": device["device_id"],
            "enduser_id": device["enduser_id"],
            "username": device["enduser_id"].rsplit("_", 1)[0],
            "name": info.get("name", info.get("device_alias", "Philips Air Fan")),
            "model": info.get("modelid", "Unknown"),
        }


class InvalidAuth(Exception):
    """Error to indicate invalid auth."""


class CannotConnect(Exception):
    """Error to indicate we cannot connect."""


class NoDevices(Exception):
    """Error to indicate no devices found."""
