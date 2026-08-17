from .base import *

# add settings specific for tests/CI here
del REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"]
del REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]

SIMPLE_JWT["AUTH_COOKIE_SECURE"] = True
