import os

# Settings without a default (app/config.py), so modules that read them import.
# Dummy values: the tests run with `env -i` and never reach these services.
for _key, _value in {
    "DASHBOARD_N6M_NAD_NAME": "n6m-test",
    "DASHBOARD_N6M_STATIC_NAD_NAME": "n6m-static-test",
    "DASHBOARD_N6M_NAD_NAMESPACE": "test",
    "DASHBOARD_PROMETHEUS_URL": "http://prometheus.invalid",
    "DASHBOARD_MONGODB_URL": "mongodb://mongodb.invalid/test",
    "DASHBOARD_KEYCLOAK_URL": "http://keycloak.invalid",
}.items():
    os.environ.setdefault(_key, _value)
