DOMAIN = "pella_insynctive"

CONF_HOST = "host"
CONF_PORT = "port"

DEFAULT_PORT = 23

# Options
OPT_RECONNECT_MIN_SECONDS = "reconnect_min_seconds"
OPT_RECONNECT_MAX_SECONDS = "reconnect_max_seconds"
OPT_SCAN_ALL_128 = "scan_all_128"
OPT_POLL_INTERVAL_SECONDS = "poll_interval_seconds"
OPT_BATTERY_POLL_MINUTES = "battery_poll_minutes"

DEFAULT_RECONNECT_MIN_SECONDS = 2
DEFAULT_RECONNECT_MAX_SECONDS = 60

DEFAULT_POLL_INTERVAL_SECONDS = 300
DEFAULT_BATTERY_POLL_MINUTES = 180
DEFAULT_SCAN_ALL_128 = False

DEVICE_WINDOW_DOOR = 0x01
DEVICE_GARAGE = 0x03
DEVICE_LOCK = 0x0D
DEVICE_SHADE = 0x13


# Per-device overrides stored in config entry options.
# Keys are device_name_001, device_area_001, etc.
OPT_DEVICE_NAME_PREFIX = "device_name_"
OPT_DEVICE_AREA_PREFIX = "device_area_"


