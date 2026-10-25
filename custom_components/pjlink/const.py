"""Constants for the PJLink integration."""

from typing import Final

DOMAIN: Final = "pjlink"

CONF_ENCODING: Final = "encoding"
CONF_MAC: Final = "mac"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_LAMP_WARNING_HOURS: Final = "lamp_warning_hours"
CONF_FILTER_WARNING_HOURS: Final = "filter_warning_hours"
CONF_PUSH: Final = "push"

DEFAULT_PORT: Final = 4352
DEFAULT_ENCODING: Final = "utf-8"
DEFAULT_SCAN_INTERVAL: Final = 30
DEFAULT_LAMP_WARNING_HOURS: Final = 20000
DEFAULT_FILTER_WARNING_HOURS: Final = 0

# Poll every few seconds while the projector warms up or cools down.
TRANSITION_INTERVAL: Final = 5
# Errors, lamp and filter hours change slowly; read them this often (seconds).
SLOW_INTERVAL: Final = 300

# UDP port where Class 2 projectors send notifications and search replies.
PUSH_PORT: Final = 4352
SEARCH_DURATION: Final = 3.0
SEARCH_BROADCAST: Final = "255.255.255.255"
SEARCH_TARGET_PORT: Final = 4352

# Class 2 queries probed at setup; entities for them are skipped on ERR1.
PROBED_COMMANDS: Final = ("FREZ", "FILT", "IRES")
