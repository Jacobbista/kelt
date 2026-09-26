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

## Layer 2: `subscribers-snapshot` Secret

The PVC covers pod restarts. It does not cover a fresh deploy, a manual PVC delete, or a node rebuild. For that, a `Secret` named `subscribers-snapshot` (`subscriber_snapshot_secret` in `all.yml`) in namespace `5g` is kept in sync with MongoDB. It is a Secret, not a ConfigMap, because it holds the SIM keys. It holds the full subscriber list as a single JSON document under the key `snapshot.json`.

### Write path (dashboard backend)

The dashboard backend treats MongoDB as authoritative for the running session and mirrors every mutation into the Secret:

1. `POST/PUT/DELETE /api/v1/subscribers` calls `MongoService` which, after the MongoDB write, calls `SubscriberSnapshotService.write(list_subscribers())`.
2. `POST /api/v1/subscribers/import` loops upserts and then forces one final snapshot sync.
3. `POST /api/v1/subscribers/sync` (exposed for operators) rebuilds the Secret from the current MongoDB contents. Useful after a playbook re-run.
4. On backend startup (`app.main._sync_subscriber_snapshot_on_startup`), if MongoDB is reachable and has subscribers, the Secret is re-aligned.

Writes are best-effort: Kubernetes API errors are logged but never fail the subscriber CRUD call, because MongoDB remains authoritative for the in-flight session.

Relevant files:

- `dashboard/backend/app/services/subscriber_snapshot.py`
- `dashboard/backend/app/services/mongo_service.py`
- `dashboard/backend/app/routers/subscribers.py`
- `dashboard/backend/app/main.py`

### Read path (MongoDB pod startup)

The MongoDB deployment mounts the Secret read-only at `/etc/subscribers-snapshot/`. After `mongod` becomes reachable, `mongo_init.sh` runs a `mongosh` reconcile that:

1. Parses `snapshot.json` via `require('fs')` (quotes/escapes in subscriber fields are preserved).
2. Upserts every entry keyed by `imsi`.
3. Deletes every subscriber in the collection whose `imsi` is not in the snapshot (this is what propagates UI deletions across a PVC wipe).

The Secret volume is marked `optional: true`, so the pod still comes up on a brand-new cluster where the `subscriber_import` Ansible role has not seeded it yet.

Relevant files:

- `ansible/phases/05-5g-core/scripts/mongo_init.sh`
- `ansible/phases/05-5g-core/templates/mongodb-deployment.yaml.j2`

### Seed path (Ansible `subscriber_import`)

SIM keys (K, OPc) never live in the repository. The seed is a local, gitignored file, `.testbed.subscribers.json` in the repo root (`subscribers_file` in `all.yml`). The `testbed` CLI generates it the first time it provisions or runs a phase, from the repo template `roles/subscriber_import/subscribers/subscribers.example.json` (IMSIs and profiles, no keys), with a random K and OPc per subscriber. A real SIM's keys go in that file, or are set from the dashboard. The file is never regenerated once it exists, because new keys would lock the SIMs out.

The `subscriber_import` role:

1. seeds the snapshot Secret from that file only when the Secret is missing;
2. runs a Job that **inserts** the file's subscribers MongoDB does not have, and leaves every existing subscriber unchanged. MongoDB is authoritative once a subscriber exists, so a playbook re-run can never revert a key changed from the dashboard.

To push a changed file entry into a running core, delete that subscriber from the dashboard (or edit it there) and re-run the phase.

Relevant files:

- `ansible/phases/05-5g-core/roles/subscriber_import/tasks/main.yml` (look up + seed tasks)
- `ansible/phases/05-5g-core/roles/subscriber_import/templates/import_subscribers.py.j2` (insert-only import)
- `testbed-config` (`ensure_subscribers_file`)

## Failure modes after the change

| Event | Outcome |
|-------|---------|
| MongoDB pod restart / reschedule on the same node | Data survives on the PVC. Reconcile re-applies the snapshot as a no-op. |
| MongoDB PVC is deleted | Reconcile on next pod start rebuilds the collection from the snapshot Secret. UI-added and UI-deleted subscribers are both preserved. |
| Full `vagrant destroy && vagrant up` | The cluster is recreated. The local subscribers file lives on the host, so it survives; `subscriber_import` seeds MongoDB and the snapshot Secret from it. Subscribers added only from the dashboard are lost unless they were also added to the file. |
| Playbook re-run without destroy | Existing snapshot Secret is left untouched. The import Job inserts only subscribers MongoDB does not have; existing ones, keys included, are not touched. |
| Dashboard backend down | Subscriber CRUD is unavailable, but MongoDB keeps serving existing subscribers to the 5G core. |
| Kubernetes API down when dashboard writes a subscriber | MongoDB is updated; the snapshot sync is logged as failed and will converge on the next successful write or on backend restart. |

## Sizing notes

The Secret has a hard limit of 1 MiB. A typical Open5GS subscriber document with one slice and two sessions serialises to about 1 KiB, so around 800 subscribers fit comfortably. For larger deployments this should be moved to a second PVC or to an external key-value store, but that is out of scope for a testbed.
