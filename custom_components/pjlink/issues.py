"""Repair issues for lamp, filter and projector errors."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import (
    CONF_FILTER_WARNING_HOURS,
    CONF_LAMP_WARNING_HOURS,
    DEFAULT_FILTER_WARNING_HOURS,
    DEFAULT_LAMP_WARNING_HOURS,
    DOMAIN,
)
from .coordinator import PJLinkCoordinator

ISSUE_KINDS = ("lamp_hours", "filter_hours", "projector_error")


def _issue_id(kind: str, entry_id: str) -> str:
    return f"{kind}_{entry_id}"


@callback
def async_delete_issues(hass: HomeAssistant, entry_id: str) -> None:
    """Remove every issue of an entry."""
    for kind in ISSUE_KINDS:
        ir.async_delete_issue(hass, DOMAIN, _issue_id(kind, entry_id))


@callback
def async_track_issues(
    hass: HomeAssistant, coordinator: PJLinkCoordinator
) -> Callable[[], None]:
    """Create or clear issues whenever the coordinator updates."""

    @callback
    def _check() -> None:
        _async_check(hass, coordinator)

    _check()
    return coordinator.async_add_listener(_check)


def _set_issue(
    hass: HomeAssistant,
    kind: str,
    entry_id: str,
    active: bool,
    placeholders: dict[str, str],
    severity: ir.IssueSeverity = ir.IssueSeverity.WARNING,
) -> None:
    issue_id = _issue_id(kind, entry_id)
    if not active:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=severity,
        translation_key=kind,
        translation_placeholders=placeholders,
    )


@callback
def _async_check(hass: HomeAssistant, coordinator: PJLinkCoordinator) -> None:
    state = coordinator.data
    if state is None:
        return
    entry = coordinator.config_entry
    options = entry.options
    name = entry.title

    lamp_limit = options.get(CONF_LAMP_WARNING_HOURS, DEFAULT_LAMP_WARNING_HOURS)
    if state.lamps is not None:
        hours = max((lamp.hours for lamp in state.lamps), default=0)
        _set_issue(
            hass,
            "lamp_hours",
            entry.entry_id,
            bool(lamp_limit) and hours >= lamp_limit,
            {
                "name": name,
                "hours": str(hours),
                "limit": str(lamp_limit),
                "model": coordinator.device.lamp_model or "-",
            },
        )

    filter_limit = options.get(CONF_FILTER_WARNING_HOURS, DEFAULT_FILTER_WARNING_HOURS)
    if state.filter_hours is not None:
        _set_issue(
            hass,
            "filter_hours",
            entry.entry_id,
            bool(filter_limit) and state.filter_hours >= filter_limit,
            {
                "name": name,
                "hours": str(state.filter_hours),
                "limit": str(filter_limit),
                "model": coordinator.device.filter_model or "-",
            },
        )

    if state.errors is not None:
        failing = [
            category
            for category, level in state.errors.as_dict().items()
            if level == "error"
        ]
        _set_issue(
            hass,
            "projector_error",
            entry.entry_id,
            bool(failing),
            {"name": name, "categories": ", ".join(failing)},
            ir.IssueSeverity.ERROR,
        )
