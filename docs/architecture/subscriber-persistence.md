# Subscriber Persistence

Subscribers defined in Open5GS live in a MongoDB collection (`open5gs.subscribers`). Two failure modes used to wipe them: (a) the MongoDB pod restarted and its `emptyDir` was lost, (b) subscribers added or edited via the dashboard UI never left MongoDB, so after any data loss they had to be recreated by hand. The testbed now persists them at two independent layers.

## Layer 1: MongoDB PersistentVolumeClaim

The MongoDB deployment mounts a `local-path` PVC on `/var/lib/mongodb`. Normal pod restarts, rollouts, and worker reboots do not touch subscriber data.

| Property | Value |
|----------|-------|
| PVC name | `mongodb-data` |
| StorageClass | `local-path` (K3s default dynamic provisioner) |
| Size | `1Gi` |
| Access mode | `ReadWriteOnce` |
| Deployment `strategy.type` | `Recreate` (one pod at a time for the single PVC) |

Defined in:

- `ansible/phases/05-5g-core/templates/mongodb-pvc.yaml.j2`
- `ansible/phases/05-5g-core/templates/mongodb-deployment.yaml.j2`
- `ansible/phases/05-5g-core/roles/nf_deployments/defaults/main.yml` (variables `mongodb_pvc_*`)

## Layer 2: `subscribers-snapshot` ConfigMap

The PVC covers pod restarts. It does not cover a fresh deploy, a manual PVC delete, or a node rebuild. For that, a `ConfigMap` named `subscribers-snapshot` in namespace `5g` is kept in sync with MongoDB. It holds the full subscriber list as a single JSON document under the key `snapshot.json`.

### Write path (dashboard backend)

The dashboard backend treats MongoDB as authoritative for the running session and mirrors every mutation into the ConfigMap:

1. `POST/PUT/DELETE /api/v1/subscribers` calls `MongoService` which, after the MongoDB write, calls `SubscriberSnapshotService.write(list_subscribers())`.
2. `POST /api/v1/subscribers/import` loops upserts and then forces one final snapshot sync.
3. `POST /api/v1/subscribers/sync` (exposed for operators) rebuilds the ConfigMap from the current MongoDB contents. Useful after a playbook re-run.
4. On backend startup (`app.main._sync_subscriber_snapshot_on_startup`), if MongoDB is reachable and has subscribers, the ConfigMap is re-aligned.

Writes are best-effort: Kubernetes API errors are logged but never fail the subscriber CRUD call, because MongoDB remains authoritative for the in-flight session.

Relevant files:

- `dashboard/backend/app/services/subscriber_snapshot.py`
- `dashboard/backend/app/services/mongo_service.py`
- `dashboard/backend/app/routers/subscribers.py`
- `dashboard/backend/app/main.py`

### Read path (MongoDB pod startup)

The MongoDB deployment mounts the ConfigMap read-only at `/etc/subscribers-snapshot/`. After `mongod` becomes reachable and before the WebUI starts, `mongo_webui_init.sh` runs a `mongosh` reconcile that:

1. Parses `snapshot.json` via `require('fs')` (quotes/escapes in subscriber fields are preserved).
2. Upserts every entry keyed by `imsi`.
3. Deletes every subscriber in the collection whose `imsi` is not in the snapshot (this is what propagates UI deletions across a PVC wipe).

The ConfigMap volume is marked `optional: true`, so the pod still comes up on a brand-new cluster where the `subscriber_import` Ansible role has not seeded it yet.

Relevant files:

- `ansible/phases/05-5g-core/scripts/mongo_webui_init.sh`
- `ansible/phases/05-5g-core/templates/mongodb-deployment.yaml.j2`

### Seed path (Ansible `subscriber_import`)

On first deploy the ConfigMap does not exist. The `subscriber_import` role seeds it from the repo JSON (`roles/subscriber_import/subscribers/subscribers.json`) only when missing, so subsequent playbook re-runs never overwrite UI-driven changes. Operators who want to force the repo version back in place can either delete the ConfigMap manually before re-running the playbook, or call `POST /api/v1/subscribers/sync` after the Ansible import Job writes to MongoDB.

Relevant files:

- `ansible/phases/05-5g-core/roles/subscriber_import/tasks/main.yml` (look up + seed tasks)

## Failure modes after the change

| Event | Outcome |
|-------|---------|
| MongoDB pod restart / reschedule on the same node | Data survives on the PVC. Reconcile re-applies the snapshot as a no-op. |
| MongoDB PVC is deleted | Reconcile on next pod start rebuilds the collection from the snapshot ConfigMap. UI-added and UI-deleted subscribers are both preserved. |
| Full `vagrant destroy && vagrant up` | Everything is recreated. The `subscriber_import` role seeds both MongoDB and the snapshot ConfigMap from the repo JSON. UI-only changes made before the destroy are lost (expected, the cluster state is gone). |
| Playbook re-run without destroy | Existing snapshot ConfigMap is left untouched. The import Job upserts repo subscribers into MongoDB; the dashboard backend will resync the snapshot on its next startup or on the next UI change. |
| Dashboard backend down | Subscriber CRUD is unavailable, but MongoDB keeps serving existing subscribers to the 5G core. |
| Kubernetes API down when dashboard writes a subscriber | MongoDB is updated; the snapshot sync is logged as failed and will converge on the next successful write or on backend restart. |

## Sizing notes

The ConfigMap has a hard limit of 1 MiB. A typical Open5GS subscriber document with one slice and two sessions serialises to about 1 KiB, so around 800 subscribers fit comfortably. For larger deployments this should be moved to a second PVC or to an external key-value store, but that is out of scope for a testbed.
