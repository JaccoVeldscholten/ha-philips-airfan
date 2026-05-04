"""Fan platform for Philips Air Fan."""
from __future__ import annotations

from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_MODEL,
    CONF_DEVICE_NAME,
    DOMAIN,
    OSCILLATION_OFF,
    OSCILLATION_ON,
    OSCILLATION_REPORTED_ON,
    PRESET_MODE_REVERSE,
    PRESET_MODES,
    PROP_MODE,
    PROP_OSCILLATION,
    PROP_POWER,
)
from .coordinator import PhilipsAirFanCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the fan entity."""
    coordinator: PhilipsAirFanCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PhilipsAirFanEntity(coordinator, entry)])


class PhilipsAirFanEntity(CoordinatorEntity[PhilipsAirFanCoordinator], FanEntity):
    """Philips Air Fan entity."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = (
        FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
        | FanEntityFeature.PRESET_MODE
        | FanEntityFeature.OSCILLATE
    )
    _attr_preset_modes = list(PRESET_MODES.keys())

    def __init__(self, coordinator: PhilipsAirFanCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator)
        device_id = entry.data[CONF_DEVICE_ID]
        device_name = entry.data.get(CONF_DEVICE_NAME, "Philips Air Fan")
        device_model = entry.data.get(CONF_DEVICE_MODEL, "Unknown")

        self._attr_unique_id = f"philips_airfan_{device_id}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device_id)},
            "name": device_name,
            "manufacturer": "Philips",
            "model": device_model,
        }

    @property
    def is_on(self) -> bool | None:
        """Return true if fan is on."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get(PROP_POWER) == 1

    @property
    def preset_mode(self) -> str | None:
        """Return the current preset mode."""
        if not self.coordinator.data:
            return None
        mode_val = self.coordinator.data.get(PROP_MODE)
        return PRESET_MODE_REVERSE.get(mode_val)

    @property
    def oscillating(self) -> bool | None:
        """Return oscillation state."""
        if not self.coordinator.data:
            return None
        val = self.coordinator.data.get(PROP_OSCILLATION)
        return val in (OSCILLATION_ON, OSCILLATION_REPORTED_ON)

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.coordinator.connected

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn on the fan."""
        cmd: dict[str, Any] = {PROP_POWER: 1}
        if preset_mode and preset_mode in PRESET_MODES:
            cmd[PROP_MODE] = PRESET_MODES[preset_mode]
        await self.coordinator.async_send_command(cmd)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the fan."""
        await self.coordinator.async_send_command({PROP_POWER: 0})

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the preset mode."""
        if preset_mode in PRESET_MODES:
            cmd: dict[str, Any] = {PROP_MODE: PRESET_MODES[preset_mode]}
            if not self.is_on:
                cmd[PROP_POWER] = 1
            await self.coordinator.async_send_command(cmd)

    async def async_oscillate(self, oscillating: bool) -> None:
        """Set oscillation."""
        await self.coordinator.async_send_command(
            {PROP_OSCILLATION: OSCILLATION_ON if oscillating else OSCILLATION_OFF}
        )
