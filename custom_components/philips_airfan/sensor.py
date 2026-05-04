"""Sensor platform for Philips Air Fan."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_MODEL,
    CONF_DEVICE_NAME,
    DOMAIN,
    PROP_BRIGHTNESS,
    PROP_MODE,
    PROP_OSCILLATION,
    PROP_POWER,
    PROP_SPEED,
    PROP_STANDBY,
    PROP_TEMPERATURE,
    PROP_TIMER_REMAIN,
    PROP_TIMER_SET,
)
from .coordinator import PhilipsAirFanCoordinator

MAIN_SENSORS: list[SensorEntityDescription] = [
    SensorEntityDescription(
        key=PROP_TEMPERATURE,
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    SensorEntityDescription(
        key=PROP_TIMER_REMAIN,
        translation_key="timer_remaining",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        icon="mdi:timer-outline",
    ),
]

DIAGNOSTIC_SENSORS: list[SensorEntityDescription] = [
    SensorEntityDescription(
        key=PROP_POWER,
        translation_key="power_state",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:power",
    ),
    SensorEntityDescription(
        key=PROP_MODE,
        translation_key="mode_raw",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:fan",
    ),
    SensorEntityDescription(
        key=PROP_SPEED,
        translation_key="speed_level",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:speedometer",
    ),
    SensorEntityDescription(
        key=PROP_OSCILLATION,
        translation_key="oscillation_raw",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:rotate-3d-variant",
    ),
    SensorEntityDescription(
        key=PROP_TIMER_SET,
        translation_key="timer_setting",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:timer-cog-outline",
    ),
    SensorEntityDescription(
        key=PROP_BRIGHTNESS,
        translation_key="display_brightness",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:brightness-6",
    ),
    SensorEntityDescription(
        key=PROP_STANDBY,
        translation_key="standby_flag",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:power-standby",
    ),
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up sensor entities."""
    coordinator: PhilipsAirFanCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[PhilipsAirFanSensor] = []

    for desc in MAIN_SENSORS:
        entities.append(PhilipsAirFanSensor(coordinator, entry, desc))
    for desc in DIAGNOSTIC_SENSORS:
        entities.append(PhilipsAirFanSensor(coordinator, entry, desc))

    async_add_entities(entities)


class PhilipsAirFanSensor(CoordinatorEntity[PhilipsAirFanCoordinator], SensorEntity):
    """A sensor for a Philips Air Fan property."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: PhilipsAirFanCoordinator,
        entry: ConfigEntry,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self.entity_description = description
        device_id = entry.data[CONF_DEVICE_ID]
        device_name = entry.data.get(CONF_DEVICE_NAME, "Philips Air Fan")
        device_model = entry.data.get(CONF_DEVICE_MODEL, "Unknown")

        self._attr_unique_id = f"philips_airfan_{device_id}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device_id)},
            "name": device_name,
            "manufacturer": "Philips",
            "model": device_model,
        }

    @property
    def native_value(self):
        """Return sensor value."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get(self.entity_description.key)
