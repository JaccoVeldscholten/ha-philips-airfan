"""Number platform for Philips Air Fan timer control."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_MODEL,
    CONF_DEVICE_NAME,
    DOMAIN,
    PROP_TIMER_SET,
)
from .coordinator import PhilipsAirFanCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up number entities."""
    coordinator: PhilipsAirFanCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PhilipsAirFanTimerNumber(coordinator, entry)])


class PhilipsAirFanTimerNumber(CoordinatorEntity[PhilipsAirFanCoordinator], NumberEntity):
    """Timer control for Philips Air Fan (0=off, 1-12 hours)."""

    _attr_has_entity_name = True
    _attr_translation_key = "timer"
    _attr_native_min_value = 0
    _attr_native_max_value = 12
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:timer-outline"

    def __init__(self, coordinator: PhilipsAirFanCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator)
        device_id = entry.data[CONF_DEVICE_ID]
        device_name = entry.data.get(CONF_DEVICE_NAME, "Philips Air Fan")
        device_model = entry.data.get(CONF_DEVICE_MODEL, "Unknown")

        self._attr_unique_id = f"philips_airfan_{device_id}_timer"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device_id)},
            "name": device_name,
            "manufacturer": "Philips",
            "model": device_model,
        }

    @property
    def native_value(self) -> float | None:
        """Return current timer setting in hours."""
        if not self.coordinator.data:
            return None
        raw = self.coordinator.data.get(PROP_TIMER_SET)
        if raw is None:
            return None
        # Device uses: 0=off, 2=1h, 3=2h, ..., 13=12h → hours = raw - 1
        if raw == 0:
            return 0
        return max(0, raw - 1)

    async def async_set_native_value(self, value: float) -> None:
        """Set timer (0=off, 1-12 hours)."""
        hours = int(value)
        # Device expects: 0=off, 2=1h, 3=2h, ..., 13=12h → raw = hours + 1
        raw = 0 if hours == 0 else hours + 1
        await self.coordinator.async_send_command({PROP_TIMER_SET: raw})
