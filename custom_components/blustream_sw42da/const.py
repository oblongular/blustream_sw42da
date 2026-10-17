from datetime import timedelta

DOMAIN = "blustream_sw42da"
MANUFACTURER = "Blustream"
MODEL = "SW42DA"

DEFAULT_HOST = "sw42da.local"
SCAN_INTERVAL = timedelta(seconds=30)

# Set in the config entry once the default display settings have been applied.
CONF_DEFAULTS_APPLIED = "defaults_applied"
