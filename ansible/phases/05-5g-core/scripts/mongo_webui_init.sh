#!/bin/bash
set -e

echo "[MongoDB][init] Starting MongoDB initialization..."

mkdir -p /var/lib/mongodb
mkdir -p /open5gs/install/var/log/open5gs

echo "[MongoDB][init] Starting MongoDB daemon..."
mongod --config /open5gs/install/etc/open5gs/mongodb.conf &
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
  # Read the JSON via Node's fs module inside mongosh to avoid shell-quoting
  # of snapshot contents. Single-quoted heredoc so nothing in the script body
  # is expanded by the shell.
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
  echo "[MongoDB][init] No subscriber snapshot present at $SNAP, skipping reconcile."
fi

echo "[MongoDB][init] Starting Open5GS WebUI..."
cd /open5gs/webui
DB_URI=mongodb://localhost/open5gs NODE_ENV=production node server/index.js &
WEBUI_PID=$!

# Keep container alive — exit if either process dies
wait -n $MONGO_PID $WEBUI_PID
echo "[MongoDB][init] A process exited unexpectedly, stopping container."
exit 1
