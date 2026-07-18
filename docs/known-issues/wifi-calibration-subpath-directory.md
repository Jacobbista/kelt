# Known Issue: subPath mount over an empty PVC creates a directory

A Kubernetes `volumeMount` with `subPath` pointing at a file that does not yet exist
in the backing volume makes the kubelet create that subPath entry as a **directory**.
A stateful adapter (e.g. `wifi-positioning`, which writes its calibration to the file
named by `WIFI_CONFIG_PATH`) then finds a directory where it expects a file and cannot
read or write it, so it degrades. The calibration store starts empty (no seed), which
is exactly the case that triggers this.

## How the testbed handles it

The dashboard PVC-backs the runtime-written document with a **whole-directory mount**,
not a subPath: the `<name>-data` PVC is mounted at `STORE_DIR` (`/data`) and the
service's config-path env var is redirected to a file inside it
(`WIFI_CONFIG_PATH=/data/wifi-config.json`). The directory mount over an empty PVC is
valid, and the service creates the file on its first write (an operator import),
started from empty by the adapter's own resilience (it tolerates a missing/empty
config and comes up ready). No subPath, no seed initContainer, so the footgun cannot
occur. The attach reads-modifies-replaces the deployment, stripping any earlier
subPath store so an instance created before this fix is migrated in place.

## Which files implement it

- `dashboard/backend/app/services/k8s_service.py` — `attach_dir_store()` (directory
  mount + env redirect via read-modify-replace).
- `dashboard/backend/app/services/northbound_service.py` — `_STATEFUL_DOCS`
  (`{env, path}` per adapter), `STORE_DIR`, `_ensure_writable_store()`,
  `_has_writable_store()` (recognises only the `/data` mount, so the old subPath shape
  reads as not-persistent and is migrated).
