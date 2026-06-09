#!/bin/bash
set -e

echo "[MongoDB][init] Starting MongoDB..."

mkdir -p /var/lib/mongodb
mkdir -p /var/log/mongodb

mongod --config /etc/mongod.conf &
MONGO_PID=$!

echo "[MongoDB][init] Waiting for MongoDB to be ready..."
until mongosh --quiet --eval "db.adminCommand('ping')" > /dev/null 2>&1; do
    sleep 1
done

# Reconcile subscribers from the dashboard-managed snapshot (if mounted).
# Behaviour:
#   - upsert every subscriber from the snapshot (keyed by imsi);
#   - delete any subscriber in Mongo whose imsi is not in the snapshot;
#   - skip silently if the snapshot file is missing, empty, or unreadable,
#     so a fresh deploy (before the subscriber_import role runs) still comes up.
# See docs/architecture/subscriber-persistence.md
SNAP="${SUBSCRIBER_SNAPSHOT_PATH:-/etc/subscribers-snapshot/snapshot.json}"
if [ -s "$SNAP" ]; then
  echo "[MongoDB][init] Reconciling subscribers from snapshot: $SNAP"
  export SNAP
  if ! mongosh --quiet open5gs <<'MONGOSH_EOF'
const fs = require('fs');
const path = process.env.SNAP;
let snap;
try {
  snap = JSON.parse(fs.readFileSync(path, 'utf8'));
} catch (e) {
  print('[MongoDB][init] Snapshot reconcile: cannot parse ' + path + ': ' + e.message);
  quit(1);
}
const subs = Array.isArray(snap) ? snap : (snap.subscribers || []);
const imsis = [];
let upserts = 0;
for (const raw of subs) {
  if (!raw || !raw.imsi) { continue; }
  const s = Object.assign({}, raw, { imsi: String(raw.imsi) });
  imsis.push(s.imsi);
  db.subscribers.updateOne({ imsi: s.imsi }, { $set: s }, { upsert: true });
  upserts += 1;
}
const del = db.subscribers.deleteMany({ imsi: { $nin: imsis } });
print('[MongoDB][init] Snapshot reconcile: upserted=' + upserts +
      ' deleted=' + (del.deletedCount || 0));
MONGOSH_EOF
  then
    echo "[MongoDB][init] WARNING: snapshot reconcile failed, continuing startup."
  fi
else
  echo "[MongoDB][init] No subscriber snapshot at $SNAP, skipping reconcile."
fi

echo "[MongoDB][init] Ready."
wait $MONGO_PID
echo "[MongoDB][init] mongod exited unexpectedly."
exit 1
