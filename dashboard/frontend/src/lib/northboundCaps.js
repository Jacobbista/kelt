// The 0.19 adapter declaration (5g-northbound schema/adapter-announcement.schema.json).
// The engine answers 422 to an announcement that breaks these rules, so the
// Configure form checks them before saving.
export const FRAMES = [
  { value: "room", label: "metres in a room (room)" },
  { value: "venue", label: "metres in the venue (venue)" },
  { value: "wgs84", label: "latitude / longitude (wgs84)" },
];

export const REPORTING = [
  { value: "on_request", label: "a fix at each poll (on_request)" },
  { value: "periodic", label: "a fix at least every interval (periodic)" },
  { value: "on_motion", label: "a fix on motion, a communication every interval (on_motion)" },
];

const needsInterval = (reporting) => reporting === "periodic" || reporting === "on_motion";

export function capsErrors(caps) {
  const out = [];
  if (caps.frame && !FRAMES.some((f) => f.value === caps.frame))
    out.push(`frame "${caps.frame}" is not room, venue or wgs84`);
  if (needsInterval(caps.reporting) && !(Number(caps.reportingInterval) > 0))
    out.push(`reporting "${caps.reporting}" needs a reportingInterval in seconds`);
  if (caps.nominalVerticalAccuracy != null && caps.nominalVerticalAccuracy !== "" && caps.z !== true)
    out.push("nominalVerticalAccuracy needs z: true");
  return out;
}

// What a vendor-adapter schema written for 0.18 has to change to load on 0.19
// (northbound's release statement, "Mapping tool"). In 0.18 the height was `y` in
// both the wgs84 and the local frame; in `venue` and `room` (0.19 only) `y` is
// horizontal and is left alone.
const heightAsY = (mapping) => {
  const f = mapping?.frame?.const;
  return !!mapping?.y && !mapping?.z && (f === "wgs84" || f === "local");
};

export function schemaFieldMigrations(schema) {
  const out = [];
  const m = schema?.mapping;
  if (m?.frame?.const === "local") out.push({ path: "mapping.frame", from: "local", to: "venue" });
  if (heightAsY(m)) out.push({ path: "mapping.y", from: "y", to: "z" });
  const h = schema?.discover?.mapping?.height;
  if (h) out.push({ path: "discover.mapping.height", from: "height", to: "const" in h ? "remove (no measured source)" : "z" });
  if (schema?.diagnostics?.stream?.lastSeen)
    out.push({ path: "diagnostics.stream.lastSeen", from: "lastSeen", to: "lastCommunicationTime" });
  (schema?.diagnostics?.onDemand || []).forEach((d, i) => {
    if (d.mapping?.lastSeen)
      out.push({ path: `diagnostics.onDemand[${i}].mapping.lastSeen`, from: "lastSeen", to: "lastCommunicationTime" });
  });
  return out;
}

const rename = (obj, from, to) => {
  if (!obj || !(from in obj)) return obj;
  const { [from]: v, ...rest } = obj;
  return { ...rest, [to]: v };
};

// A copy of the schema with the renames above applied, for the MappingStudio to
// open on. The input is not modified.
export function migratedSchema(schema) {
  const s = JSON.parse(JSON.stringify(schema || {}));
  if (s.mapping) {
    if (heightAsY(s.mapping)) s.mapping = rename(s.mapping, "y", "z");
    if (s.mapping.frame?.const === "local") s.mapping.frame = { const: "venue" };
  }
  const dm = s.discover?.mapping;
  if (dm?.height) {
    if ("const" in dm.height) delete dm.height;
    else s.discover.mapping = rename(dm, "height", "z");
  }
  if (s.diagnostics?.stream) s.diagnostics.stream = rename(s.diagnostics.stream, "lastSeen", "lastCommunicationTime");
  (s.diagnostics?.onDemand || []).forEach((d) => { d.mapping = rename(d.mapping, "lastSeen", "lastCommunicationTime"); });
  return s;
}

// Which mapping targets the MappingStudio shows and requires, from the frame and
// the height declaration chosen at its top (vendor-adapter 0.19 Mapping rules: a
// constant frame requires its horizontal pair and admits only that pair;
// verticalAccuracy needs z). `frame` and the height switch are not rows.
const PAIRS = { wgs84: ["latitude", "longitude"], venue: ["x", "y"] };

export function mappingLayout({ keys, required, frame, height }) {
  const other = Object.entries(PAIRS).filter(([f]) => f !== frame).flatMap(([, pair]) => pair);
  const hidden = new Set(["frame", ...(PAIRS[frame] ? other : []), ...(height ? [] : ["z", "verticalAccuracy"])]);
  const visible = keys.filter((k) => !hidden.has(k));
  const req = new Set([...required, ...(PAIRS[frame] || []), ...(height ? ["z"] : [])]);
  return { visible, required: visible.filter((k) => req.has(k)) };
}

// ADAPTER_CAPABILITIES aligned with the mapper's frame and height: the adapter
// refuses a schema that contradicts them. null when nothing changes.
export function alignCapabilities(capsJson, { frame, height }) {
  let caps = {};
  try { const j = capsJson ? JSON.parse(capsJson) : {}; if (j && typeof j === "object" && !Array.isArray(j)) caps = j; } catch { caps = {}; }
  const next = { ...caps, ...(frame ? { frame } : {}), z: !!height };
  if (!height) delete next.nominalVerticalAccuracy; // needs z: true
  return JSON.stringify(next) === JSON.stringify(caps) ? null : JSON.stringify(next);
}
