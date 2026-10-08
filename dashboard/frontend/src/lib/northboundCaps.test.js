import { test } from "node:test";
import assert from "node:assert/strict";
import { FRAMES, REPORTING, capsErrors, schemaFieldMigrations, migratedSchema } from "./northboundCaps.js";

test("frames are the three 0.19 frames", () => {
  assert.deepEqual(FRAMES.map((f) => f.value), ["room", "venue", "wgs84"]);
});

test("reporting models", () => {
  assert.deepEqual(REPORTING.map((r) => r.value), ["on_request", "periodic", "on_motion"]);
});

test("periodic and on_motion need an interval", () => {
  assert.equal(capsErrors({ reporting: "on_motion" }).length, 1);
  assert.equal(capsErrors({ reporting: "periodic", reportingInterval: 0 }).length, 1);
  assert.deepEqual(capsErrors({ reporting: "periodic", reportingInterval: 2 }), []);
  assert.deepEqual(capsErrors({ reporting: "on_request" }), []);
});

test("vertical accuracy needs z", () => {
  assert.equal(capsErrors({ nominalVerticalAccuracy: 0.5 }).length, 1);
  assert.deepEqual(capsErrors({ z: true, nominalVerticalAccuracy: 0.5 }), []);
});

test("local frame is reported", () => {
  assert.equal(capsErrors({ frame: "local" }).length, 1);
});

test("a 0.18 schema lists what 0.19 renamed or forbids", () => {
  const s = {
    mapping: { frame: { const: "local" }, y: { path: "a.height" } },
    discover: { mapping: { height: { const: 0 } } },
    diagnostics: { onDemand: [{ mapping: { lastSeen: { path: "lastSeen" } } }] },
  };
  const m = schemaFieldMigrations(s).map((x) => `${x.path}:${x.to}`);
  assert.deepEqual(m.sort(), [
    "diagnostics.onDemand[0].mapping.lastSeen:lastCommunicationTime",
    "discover.mapping.height:remove (no measured source)",
    "mapping.frame:venue",
    "mapping.y:z",
  ].sort());
});

test("a lastSeen in the diagnostics stream is renamed too", () => {
  const m = schemaFieldMigrations({ diagnostics: { stream: { lastSeen: { path: "lastSeen" }, moving: { path: "m" } } } });
  assert.deepEqual(m.map((x) => `${x.path}:${x.to}`), ["diagnostics.stream.lastSeen:lastCommunicationTime"]);
});

test("a 0.19 schema needs nothing", () => {
  const s = {
    mapping: { frame: { const: "wgs84" }, z: { path: "a.height" }, lastSeen: { path: "lastSeen" } },
    discover: { mapping: { z: { path: "fixedLocation.height" } } },
  };
  assert.deepEqual(schemaFieldMigrations(s), []);
});

test("y stays horizontal in a venue or room frame", () => {
  assert.deepEqual(schemaFieldMigrations({ mapping: { frame: { const: "venue" }, x: { path: "a" }, y: { path: "b" } } }), []);
});

test("a discover height read from the vendor becomes z", () => {
  const m = schemaFieldMigrations({ discover: { mapping: { height: { path: "fixedLocation.height" } } } });
  assert.deepEqual(m.map((x) => `${x.path}:${x.to}`), ["discover.mapping.height:z"]);
});

test("migratedSchema applies the renames on a copy", () => {
  const before = {
    vendor: "acme",
    mapping: { frame: { const: "wgs84" }, latitude: { path: "lat" }, y: { path: "h" } },
    discover: { mapping: { height: { const: 0 }, latitude: { path: "lat" } } },
    diagnostics: { stream: { moving: { path: "m" } }, onDemand: [{ path: "/d", mapping: { lastSeen: { path: "ls", format: "iso8601" } } }] },
  };
  const snap = JSON.stringify(before);
  const after = migratedSchema(before);
  assert.equal(JSON.stringify(before), snap, "input untouched");
  assert.deepEqual(after.mapping, { frame: { const: "wgs84" }, latitude: { path: "lat" }, z: { path: "h" } });
  assert.deepEqual(after.discover.mapping, { latitude: { path: "lat" } });
  assert.deepEqual(after.diagnostics.onDemand[0].mapping, { lastCommunicationTime: { path: "ls", format: "iso8601" } });
  assert.deepEqual(schemaFieldMigrations(after), []);
});

test("local becomes venue in the migrated copy", () => {
  assert.deepEqual(migratedSchema({ mapping: { frame: { const: "local" } } }).mapping.frame, { const: "venue" });
});

import { mappingLayout, alignCapabilities } from "./northboundCaps.js";

const KEYS = ["frame", "latitude", "longitude", "x", "y", "accuracy", "confidence", "z", "verticalAccuracy", "timestamp", "lastSeen"];

test("wgs84 shows latitude/longitude, not x/y, and requires them", () => {
  const l = mappingLayout({ keys: KEYS, required: ["frame", "timestamp"], frame: "wgs84", height: false });
  assert.ok(!l.visible.includes("x") && !l.visible.includes("y"));
  assert.ok(l.required.includes("latitude") && l.required.includes("longitude"));
});

test("venue shows x/y, not latitude/longitude", () => {
  const l = mappingLayout({ keys: KEYS, required: ["frame", "timestamp"], frame: "venue", height: false });
  assert.ok(!l.visible.includes("latitude") && l.required.includes("x") && l.required.includes("y"));
});

test("no height: z and verticalAccuracy hidden; height: z required", () => {
  const off = mappingLayout({ keys: KEYS, required: [], frame: "wgs84", height: false });
  assert.ok(!off.visible.includes("z") && !off.visible.includes("verticalAccuracy"));
  const on = mappingLayout({ keys: KEYS, required: [], frame: "wgs84", height: true });
  assert.ok(on.visible.includes("verticalAccuracy") && on.required.includes("z"));
});

test("frame and height are not mapping rows", () => {
  const l = mappingLayout({ keys: KEYS, required: ["frame", "timestamp"], frame: "wgs84", height: false });
  assert.ok(!l.visible.includes("frame") && !l.required.includes("frame"));
});

test("capabilities follow the mapper's frame and height", () => {
  const out = JSON.parse(alignCapabilities('{"source":"wittra","frame":"wgs84","z":true}', { frame: "wgs84", height: false }));
  assert.deepEqual(out, { source: "wittra", frame: "wgs84", z: false });
  assert.equal(alignCapabilities('{"source":"wittra","frame":"wgs84","z":false}', { frame: "wgs84", height: false }), null);
  assert.equal(JSON.parse(alignCapabilities("", { frame: "venue", height: true })).frame, "venue");
});

test("height not measured drops the vertical precision too", () => {
  const out = JSON.parse(alignCapabilities('{"source":"acme","frame":"wgs84","z":true,"nominalVerticalAccuracy":0.5}', { frame: "wgs84", height: false }));
  assert.equal(out.z, false);
  assert.ok(!("nominalVerticalAccuracy" in out));
  assert.deepEqual(capsErrors(out), []);
});
