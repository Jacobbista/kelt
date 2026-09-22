/**
 * Field mapping studio: a dedicated surface for pointing an adapter's schema mapping
 * (and its diagnostics block) at the vendor's own payload.
 *
 * Entirely contract-driven, nothing hardcoded:
 *  - the location targets come from the adapter's /contract/schema mapping grammar,
 *  - the diagnostics core targets and their tier from the gateway vocabulary,
 *  - the FieldSpec grammar (const | path, transform, format) from the same schema,
 *  - the candidate paths from a live /discover?raw=1 sample.
 * Save writes the merged document back to the schema file and rolls the adapter.
 *
 * Suggestions rank by NAME (exact leaf name beats a looser alias) and by STRUCTURE:
 * a generic field (a timestamp, a motion flag) only counts as a strong match when it
 * sits in the same subtree the position itself came from, so the accelerometer's
 * timestamp does not rank beside the location's. A weak match is confirmed before it
 * binds; a strong one binds outright.
 */
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { btn } from "./ui";
import { useToast } from "../context/ToastContext";
import {
  getNorthboundDiscoverRaw,
  getNorthboundContractSchema,
  getNorthboundDiagnosticsVocabulary,
  getNorthboundServiceConfig,
  getNorthboundServiceFile,
  applyNorthboundServiceFile,
} from "../api";

// ---- generic path helpers ----
const resolvePath = (obj, path) =>
  (path || "").split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
function flatten(obj, prefix = "", out = []) {
  if (obj && typeof obj === "object" && !Array.isArray(obj)) {
    for (const [k, v] of Object.entries(obj)) {
      const p = prefix ? `${prefix}.${k}` : k;
      if (v === null || typeof v !== "object") out.push({ path: p, value: v });
      else flatten(v, p, out);
    }
  }
  return out;
}
// Follow a JSON Schema node to the object that actually declares `properties`.
// Pydantic emits a property inline, as {"$ref": "#/$defs/X"}, or wrapped in
// allOf/anyOf (the latter when the field is optional, paired with a null branch),
// and it does not guarantee the branch order. Resolving through the ref keeps us
// bound to the schema's shape rather than to the generated $defs names.
function resolveRef(root, node, depth = 0) {
  if (!node || typeof node !== "object" || depth > 5) return null;
  if (node.$ref) {
    const segs = String(node.$ref).replace(/^#\//, "").split("/");
    let cur = root;
    for (const s of segs) cur = cur?.[decodeURIComponent(s).replace(/~1/g, "/").replace(/~0/g, "~")];
    return resolveRef(root, cur, depth + 1);
  }
  if (node.properties) return node;
  for (const key of ["allOf", "anyOf", "oneOf"]) {
    for (const branch of node[key] || []) {
      const hit = resolveRef(root, branch, depth + 1);
      if (hit) return hit;
    }
  }
  return null;
}

const looksIso = (v) => typeof v === "string" && /^\d{4}-\d\d-\d\dT/.test(v);
const fmt = (v) => (v === undefined ? "—" : v === null ? "null" : typeof v === "string" ? `"${v}"` : String(v));
const valColor = (v) =>
  v === null ? "text-slate-500" : typeof v === "string" ? "text-sky-300" : typeof v === "number" ? "text-emerald-300" : typeof v === "boolean" ? "text-amber-300" : "text-slate-200";

// ---- name + structure expectations, keyed by the well-known target names ----
// `exact` = leaf names that are a precise hit; anything else matching `re` is a looser
// alias. `contextual` = generic enough that WHERE it sits decides a strong match.
const MATCH = {
  frame:      { re: /(frame|crs|srid|epsg|reference|datum)/i, type: "string", exact: ["frame", "crs", "srid"] },
  latitude:   { re: /(^|[._])lat(itude)?$/i, type: "number", range: [-90, 90], exact: ["latitude", "lat"] },
  longitude:  { re: /(^|[._])lon(g|gitude)?$/i, type: "number", range: [-180, 180], exact: ["longitude", "lon", "lng", "long"] },
  accuracy:   { re: /(accuracy|hdop|precision|radius|error)/i, type: "number", exact: ["accuracy", "acc"] },
  confidence: { re: /(confidence|conf|quality|reliab)/i, type: "number", range: [0, 1], exact: ["confidence", "conf"] },
  y:          { re: /(local[._]?y|position[._]?y|pos[._]?y|coord\w*[._]?y)/i, type: "number", exact: ["y"] },
  timestamp:  { re: /(time(stamp)?|[._]ts$|date)/i, iso: true, exact: ["timestamp", "time", "ts"], contextual: true },
  battery:    { re: /(batter|charge|percentage|[._]soc$)/i, type: "number", range: [0, 100], exact: ["percentage", "battery", "charge", "soc"] },
  lastSeen:   { re: /(last[._]?seen|last[._]?update|last[._]?contact|[._]seen$)/i, iso: true, exact: ["lastseen", "lastcontact"] },
  moving:     { re: /(mov(ing|e)|motion|speed|velocity|activity)/i, exact: ["motion", "moving", "move"], contextual: true },
};
// A target the heuristics don't know about still matches on its own exact name.
const matchOf = (key) => MATCH[key] || { re: new RegExp(`(^|[._])${key.replace(/[^a-z0-9]/gi, "")}$`, "i"), exact: [key] };

// ---- FieldSpec <-> editor state ----
function specToState(spec) {
  if (spec && typeof spec === "object" && "const" in spec)
    return { mode: "const", value: String(spec.const ?? ""), path: "", transform: null, format: null, default: "" };
  if (spec && typeof spec === "object")
    return { mode: "path", value: "", path: spec.path || "", transform: spec.transform || null, format: spec.format || null, default: spec.default ?? "" };
  return { mode: "path", value: "", path: "", transform: null, format: null, default: "" };
}
function stateToSpec(s) {
  if (s.mode === "const") {
    const n = Number(s.value);
    return { const: s.value !== "" && !Number.isNaN(n) ? n : s.value };
  }
  const out = { path: (s.path || "").trim() };
  if (s.format) out.format = s.format;
  if (s.transform) out.transform = s.transform;
  if (s.default !== "" && s.default != null) out.default = s.default;
  return out;
}
const isSet = (s) => (s ? (s.mode === "const" ? String(s.value).trim() !== "" : (s.path || "").trim() !== "") : false);
function resolvedOf(s, sample) {
  if (!s) return undefined;
  if (s.mode === "const") return s.value;
  let v = resolvePath(sample, s.path);
  if (s.transform?.type === "bool") return (s.transform.truthy || []).includes(v);
  if (s.transform?.type === "linear") return typeof v === "number" ? v * (s.transform.scale ?? 1) + (s.transform.offset ?? 0) : v;
  return v;
}

// ---- suggestion / confidence engine ----
const norm = (s) => String(s).toLowerCase().replace(/[_\s]/g, "");
function commonPrefix(paths) {
  const segs = paths.map((p) => p.split("."));
  const out = [];
  for (let i = 0; ; i++) {
    const s = segs[0][i];
    if (s === undefined) break;
    if (segs.every((a) => a[i] === s)) out.push(s);
    else break;
  }
  return out;
}
// The subtree the position came from: the common container of the bound coordinates.
function coordAnchor(binds) {
  const ps = ["latitude", "longitude", "accuracy"]
    .map((k) => binds[k])
    .filter((b) => b && b.mode === "path" && b.path)
    .map((b) => b.path);
  if (ps.length < 2) return null;
  const cp = commonPrefix(ps);
  if (cp[cp.length - 1] === "value") cp.pop();
  return cp.length ? cp : null;
}
const inSubtree = (path, anchor) => {
  if (!anchor) return true;
  const s = path.split(".");
  return anchor.every((a, i) => s[i] === a);
};
function nameHit(path, spec, key) {
  if (!spec.re.test(path)) return 0;
  const seg = norm(path.split(".").pop());
  return (spec.exact || [key]).map(norm).includes(seg) ? 2 : 1;
}
function scoreType(value, spec) {
  if (value === null || value === undefined) return null; // unknown, not a failure
  let t = null;
  if (spec.type) t = typeof value === spec.type;
  if (spec.iso) t = looksIso(value);
  if (spec.range && typeof value === "number") t = t !== false && value >= spec.range[0] && value <= spec.range[1];
  return t;
}
// The verdict for pointing `key` at `path`. level 2 = strong, 1 = same-name-but-check, 0 = none.
function tier(path, value, key, binds) {
  const spec = matchOf(key);
  const nh = nameHit(path, spec, key);
  if (!nh) return { level: 0, rank: -1, nh: 0 };
  const typeOk = scoreType(value, spec) !== false;
  const anchor = spec.contextual ? coordAnchor(binds) : null;
  const ctx = inSubtree(path, anchor);
  const best = nh >= 2 && ctx && typeOk;
  return { level: best ? 2 : 1, rank: (best ? 100 : 0) + nh * 10 + (ctx ? 5 : 0) + (typeOk ? 1 : 0), nh, ctx, typeOk, contextual: !!anchor };
}
function confidence(state, key, binds, sample) {
  if (!state || state.mode !== "path") return null;
  const t = tier(state.path, resolvePath(sample, state.path), key, binds);
  if (t.level >= 2) return { lvl: "ok", msg: "exact name, in the position's own subtree" };
  if (t.nh === 0) return { lvl: "warn", msg: `no vendor field named like “${key}”` };
  if (!t.typeOk) return { lvl: "warn", msg: `the value type isn't what “${key}” expects` };
  if (t.contextual && !t.ctx) return { lvl: "warn", msg: "other fields share this name; this one isn't beside the position reading" };
  return { lvl: "warn", msg: "a looser name match, verify it's the right field" };
}
function weakReason(path, key, binds, sample) {
  const t = tier(path, resolvePath(sample, path), key, binds);
  if (t.nh === 0) return `no vendor field is named like “${key}”`;
  if (!t.typeOk) return `the value type isn't what “${key}” expects`;
  if (t.contextual && !t.ctx) return `several fields are named this; this one isn't beside the position reading`;
  return `this is a looser name match`;
}
function suggestionsFor(key, leaves, binds) {
  return leaves
    .map((l) => ({ l, t: tier(l.path, l.value, key, binds) }))
    .filter((x) => x.t.level > 0)
    .sort((a, b) => b.t.rank - a.t.rank)
    .map((x) => x.l);
}
// Which record to lay out in the tree. Prefer a device that actually carries a
// position so the operator has coordinates to aim the location fields at. Purely
// structural: reuses the name/type heuristics (a plausible latitude+longitude pair
// is the strongest signal), and otherwise ranks the more fully populated record
// higher. No vendor-specific path. Falls back to the first record.
function pickSampleIdx(records) {
  const latM = matchOf("latitude");
  const lonM = matchOf("longitude");
  const has = (leaves, m, key) => leaves.some((l) => nameHit(l.path, m, key) && scoreType(l.value, m) !== false);
  let best = 0;
  let bestScore = -1;
  (records || []).forEach((r, i) => {
    const leaves = flatten(r);
    const pos = has(leaves, latM, "latitude") && has(leaves, lonM, "longitude");
    const score = (pos ? 1000 : 0) + leaves.length;
    if (score > bestScore) { bestScore = score; best = i; }
  });
  return best;
}

// A vendorSpecific extra field's storage id (see MappingStudio's xrowId/matchNameFor):
// module-level because Tree, a sibling top-level component, needs to tell one apart
// from a real mapKey/core key without a matching name, not just inside the studio.
const isXrowId = (id) => typeof id === "string" && id.startsWith("__x:");

// A first-guess name for a new extra field, from the vendor path the operator just
// clicked - e.g. "latest.data.usage.value.moving" -> "usageMoving". Only a
// suggestion: the caller always leaves it selected in the name box, ready to keep
// or type straight over. "value"/"data"/"latest" are dropped first: they are
// generic containers (Wittra's own shape is <metric>.value.<field>), not part of
// what the field actually is.
function suggestNameFromPath(path, taken) {
  const NOISE = new Set(["value", "data", "latest"]);
  const segs = path.split(".").filter((s) => !NOISE.has(s));
  const tail = (segs.length ? segs : path.split(".")).slice(-2);
  const words = tail.flatMap((s) => s.replace(/[^a-zA-Z0-9]+/g, " ").trim().split(/\s+/).filter(Boolean));
  let name = words.map((w, i) => (i === 0 ? w.toLowerCase() : w[0].toUpperCase() + w.slice(1).toLowerCase())).join("") || "field";
  if (!taken.includes(name)) return name;
  for (let n = 2; ; n++) if (!taken.includes(`${name}${n}`)) return `${name}${n}`;
}

// ==================================================================================
export default function MappingStudio({ service, path, initial, onClose, onSaved }) {
  const toast = useToast();
  const [act, setAct] = useState(null); // consequential-action confirmation { title, body, ok, danger, run }
  const [shown, setShown] = useState(false); // drives the open/close fade+scale
  const escRef = useRef(null);

  const [raw, setRaw] = useState(null);
  const [grammar, setGrammar] = useState(null);
  const [vocab, setVocab] = useState(null);
  const [loadErr, setLoadErr] = useState(null);
  const [loading, setLoading] = useState(true);

  // Working document: the parsed schema, plus the editable binds/extras layered on top.
  const [doc, setDoc] = useState(null);
  const [binds, setBinds] = useState({});       // key -> editor state (location + core diagnostics)
  const [xrows, setXrows] = useState([]);       // [{ key, tier, state }] non-core diagnostics
  const [focused, setFocused] = useState(null);
  const [advOpen, setAdvOpen] = useState(() => new Set());
  const [pending, setPending] = useState(null); // { key, path } weak bind awaiting confirm
  const [flashKey, setFlashKey] = useState(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState("");       // last-saved snapshot
  const seeded = useRef(false);
  // The schema doc: given as props when launched from the file field, else discovered from
  // the service config (its *_FILE schema field) when launched from the service row.
  const [docPath, setDocPath] = useState(path || null);
  const [docText, setDocText] = useState(initial ?? null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setLoadErr(null);
    const resolveDoc = (path && initial != null)
      ? Promise.resolve({ p: path, t: initial })
      : getNorthboundServiceConfig(service).then((c) => {
          const env = c?.env || {};
          const fields = [...(env.required || []), ...(env.recommended || []), ...(env.optional || [])];
          // The contract says which entry holds the schema document (role, 0.15.1+).
          const f = fields.find((x) => x.role === "schema" && x.file_state);
          const p = f ? (f.file_path || f.value || f.default) : null;
          return p ? getNorthboundServiceFile(service, p).then((r) => ({ p, t: r.content || "" })) : { p: null, t: "" };
        });
    Promise.all([
      getNorthboundDiscoverRaw(service),
      getNorthboundContractSchema(service),
      getNorthboundDiagnosticsVocabulary(),
      resolveDoc,
    ])
      .then(([d, g, v, doc]) => {
        if (!alive) return;
        setRaw(Array.isArray(d?.raw) ? d.raw : []);
        setGrammar(g);
        setVocab(v);
        setDocPath(doc.p);
        setDocText(doc.t);
      })
      .catch((e) => alive && setLoadErr(e.message))
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [service]);  // eslint-disable-line react-hooks/exhaustive-deps

  // All location-fix fields come from the contract's mapping grammar; only `required`
  // gates Save (v0.12.2 made confidence/y optional). Never hardcode the set.
  // Reached by following the top-level `mapping` property's $ref, not by indexing
  // $defs by name: the grammar is Pydantic-generated JSON Schema, so the names inside
  // $defs are an implementation detail and northbound asks consumers to bind to the
  // semantics ($ref, required, anyOf) instead.
  const mapSchema = resolveRef(grammar, grammar?.properties?.mapping);
  const mapProps = mapSchema?.properties || {};
  const mapKeys = Object.keys(mapProps);
  const requiredKeys = mapSchema?.required || [];
  const core = vocab?.core || {};
  const extBag = vocab?.extensionBag || "vendorSpecific";
  // Lay out a device that actually carries a position (see pickSampleIdx); fall back
  // to the first record.
  const sampleIdx = (raw || []).length ? pickSampleIdx(raw) : 0;
  const sample = (raw && (raw[sampleIdx] || raw[0])) || {};
  const leaves = flatten(sample);

  // Seed the editors once the contracts and the current document are in.
  useEffect(() => {
    if (seeded.current || !grammar || !vocab || docText == null) return;
    seeded.current = true;
    let parsed = {};
    try { parsed = docText ? JSON.parse(docText) : {}; } catch { parsed = {}; }
    setDoc(parsed);
    const b = {};
    for (const k of mapKeys) b[k] = specToState(parsed?.mapping?.[k]);
    const diag = parsed?.diagnostics || {};
    const stream = diag.stream || {};
    const onDemand = (diag.onDemand && diag.onDemand[0] && diag.onDemand[0].mapping) || {};
    for (const k of Object.keys(core)) b[k] = specToState(core[k].tierDefault === "stream" ? stream[k] : onDemand[k]);
    setBinds(b);
    const xr = [];
    for (const [k, val] of Object.entries(stream)) if (!(k in core)) xr.push({ key: k, tier: "stream", state: specToState(val) });
    for (const [k, val] of Object.entries(onDemand)) if (!(k in core)) xr.push({ key: k, tier: "onDemand", state: specToState(val) });
    setXrows(xr);
    const snap = JSON.stringify({ binds: b, xrows: xr });
    setSaved(snap);
    // Nothing pre-selected: a value clicked before the operator has picked a
    // target would silently overwrite whatever landed here first. They choose a
    // target (a field on the right, or + add field), then click a value.
  }, [grammar, vocab, docText]); // eslint-disable-line react-hooks/exhaustive-deps

  const snapshot = () => JSON.stringify({ binds, xrows });
  const dirty = () => snapshot() !== saved;

  const flash = (key) => { setFlashKey(key); setTimeout(() => setFlashKey((k) => (k === key ? null : k)), 1100); };
  const setBind = (key, state) => setBinds((b) => ({ ...b, [key]: state }));

  // A vendorSpecific extra field has no fixed name (the operator types it), so it
  // cannot live in `binds` keyed by that name: the key changes as they type, and
  // two rows could momentarily share one. It keeps its own `state` inline on the
  // xrows entry instead (see xrows: [{ key, tier, state }]), addressed here by
  // POSITION (`__x:<index>`), which stays stable while the name is edited. Every
  // click-to-bind path below (commit/tryBind/clearBind) goes through this pair so
  // Row/SourceEditor work unmodified for a location field, a core diagnostic, or
  // an extra one.
  const xrowId = (i) => `__x:${i}`;
  const xrowIndex = (id) => Number(id.slice(4));
  const stateFor = (id) => (isXrowId(id) ? xrows[xrowIndex(id)]?.state : binds[id]);
  const setStateFor = (id, state) => {
    if (isXrowId(id)) {
      const i = xrowIndex(id);
      setXrows((rs) => rs.map((x, j) => (j === i ? { ...x, state } : x)));
    } else {
      setBind(id, state);
    }
  };

  // The match heuristics (matchOf/tier, for the strong-match auto-bind and the ISO
  // guess) key off the field's NAME, e.g. "timestamp" or an operator-typed
  // "usageMoving" - never the storage id, which for an extra field is `__x:<i>`
  // and would never name-match anything.
  const matchNameFor = (id) => (isXrowId(id) ? (xrows[xrowIndex(id)]?.key || "") : id);
  const nameInputRefs = useRef({});
  function commit(id, p) {
    const name = matchNameFor(id);
    setStateFor(id, { mode: "path", path: p, transform: null, format: (p.includes("timestamp") || matchOf(name).iso) ? "iso8601" : null, value: "", default: "" });
    setPending(null);
    flash(id);
  }
  // A brand-new extra field has no name yet: clicking a value for it both binds
  // AND names the field, from that same path (suggestNameFromPath) - no separate
  // "type a name first" step. The name lands selected in its box so the operator
  // can immediately keep it (click away) or type straight over it (rename).
  // An extra field has no fixed identity the way `latitude` does - its name is
  // only ever a suggestion tied to whatever path it last pointed at. So EVERY
  // leaf click on one re-suggests the name from that path, not just the first:
  // re-picking the source is exactly when an old suggestion (named after the
  // PREVIOUS path) is most likely wrong, and silently keeping it would be the
  // "normal mode" behaviour (a fixed target that just changes source) leaking
  // into a row that has no fixed target to begin with.
  function nameAndBindXrow(id, p) {
    const i = xrowIndex(id);
    const taken = [...Object.keys(core), ...xrows.filter((_, j) => j !== i).map((r) => r.key).filter(Boolean)];
    const name = suggestNameFromPath(p, taken);
    setXrows((rs) => rs.map((x, j) => (j === i ? {
      ...x, key: name,
      state: { mode: "path", path: p, transform: null, format: (p.includes("timestamp") || matchOf(name).iso) ? "iso8601" : null, value: "", default: "" },
    } : x)));
    setPending(null);
    flash(id);
    // One-shot: this gesture is done. Leaving the row "focused" would route the
    // very next left-click into the NORMAL flow (a selected target takes whatever
    // you click next) and silently rebind the field just added instead of doing
    // nothing until the operator deliberately picks a new target - press + add
    // field again for another one, or click this row's own value box to re-edit it.
    setFocused(null);
    requestAnimationFrame(() => { const el = nameInputRefs.current[i]; el?.focus(); el?.select(); });
  }
  function tryBind(id, p) {
    if (isXrowId(id)) { nameAndBindXrow(id, p); return; }
    if (tier(p, resolvePath(sample, p), matchNameFor(id), binds).level >= 2) commit(id, p);
    else setPending({ key: id, path: p });
  }
  const clearBind = (id) => { setStateFor(id, specToState(null)); setAdvOpen((s) => { const n = new Set(s); n.delete(id); return n; }); setFocused(id); };
  const toggleAdv = (key) => setAdvOpen((s) => { const n = new Set(s); n.has(key) ? n.delete(key) : n.add(key); return n; });
  const addXrow = () => {
    const id = xrowId(xrows.length); // stable: the row this pushes into that position
    setXrows((rs) => [...rs, { key: "", tier: "onDemand", state: specToState(null) }]);
    setFocused(id); // routes the next leaf click on the left to this row (nameAndBindXrow)
    setPending(null);
  };
  // A saved extra field needs a name, must not collide with a core vocabulary key
  // (it would silently overwrite that entry on save - both land in the same
  // stream/onDemand object, see assemble()) or with another extra field's name.
  const xrowIssue = (i) => {
    const r = xrows[i];
    if (!isSet(r.state)) return null;
    const key = (r.key || "").trim();
    if (!key) return "name this field to save it";
    if (core[key]) return `"${key}" is a core diagnostic, use Diagnostics above or a different name`;
    if (xrows.some((o, j) => j !== i && (o.key || "").trim() === key)) return `duplicate name "${key}", only one is kept`;
    return null;
  };

  // ---- assemble + persist ----
  function assemble() {
    const next = JSON.parse(JSON.stringify(doc || {}));
    next.mapping = {};
    // Write every mapped field; omit an unset optional one so the adapter defaults it (0),
    // instead of stuffing an empty path. Required fields are always set (Save is gated).
    for (const k of mapKeys) if (isSet(binds[k])) next.mapping[k] = stateToSpec(binds[k]);
    const stream = {}, demand = {};
    for (const k of Object.keys(core)) {
      if (!isSet(binds[k])) continue;
      (core[k].tierDefault === "stream" ? stream : demand)[k] = stateToSpec(binds[k]);
    }
    for (const r of xrows) {
      const key = (r.key || "").trim();
      // A name shadowing a core key would silently overwrite the binding just
      // written above (same stream/demand object) instead of raising anything;
      // xrowIssue() already warns the operator inline, this is the save-time
      // backstop. A duplicate xrow name is allowed through (last one wins, same
      // as any JS object literal) since that is what the warning already says.
      if (!key || !isSet(r.state) || core[key]) continue;
      (r.tier === "stream" ? stream : demand)[key] = stateToSpec(r.state);
    }
    const diagnostics = {};
    if (Object.keys(stream).length) diagnostics.stream = stream;
    if (Object.keys(demand).length) {
      const existing = doc?.diagnostics?.onDemand?.[0] || {};
      diagnostics.onDemand = [{
        path: existing.path || doc?.path || "",
        ...(existing.listPath ? { listPath: existing.listPath } : {}),
        ...(existing.pathVars ? { pathVars: existing.pathVars } : doc?.pathVars ? { pathVars: doc.pathVars } : {}),
        mapping: demand,
      }];
    }
    next.diagnostics = diagnostics;
    return next;
  }

  // Consequential actions open a confirmation over the studio (its own layer, so it can't
  // be occluded by an ancestor modal). `act.run` fires on confirm.
  function saveNow() {
    setBusy(true);
    const content = JSON.stringify(assemble(), null, 2);
    applyNorthboundServiceFile(service, docPath, content)
      .then(() => {
        onSaved?.(content);   // parent refreshes cache, activates the file, shows the rollout, toasts
        onClose();            // close on success, the expected outcome; the rollout shows in the list
      })
      .catch((e) => { toast.error(`Could not save: ${e.message}`); setBusy(false); });
  }
  const restoreSnap = (snap) => { const s = JSON.parse(snap); setBinds(s.binds); setXrows(s.xrows); setPending(null); };
  function doSave() {
    const unmapped = Object.keys(core).filter((k) => !isSet(binds[k]));
    const body = unmapped.length
      ? `The mapping is written and ${service} restarts to load it. ${unmapped.join(", ")} ${unmapped.length > 1 ? "are" : "is"} still unmapped and won't be reported.`
      : `The mapping is written and ${service} restarts to load it. Positions pause briefly during the rollout.`;
    setAct({ title: "Save and restart the adapter?", body, ok: "Save & restart", run: saveNow });
  }
  const doRevert = () => setAct({ title: "Discard changes?", body: "Your unsaved edits are removed and the mapping returns to the last saved version.", ok: "Discard changes", danger: true, run: () => restoreSnap(saved) });
  const doClear = () => setAct({ title: "Clear the whole mapping?", body: "Every field is emptied. Nothing is written until you save, so you can still close without saving.", ok: "Clear all", danger: true, run: () => { const b = {}; for (const k of [...mapKeys, ...Object.keys(core)]) b[k] = specToState(null); setBinds(b); setXrows([]); setPending(null); } });
  // Animate out, then unmount. Used by Close, the backdrop, Esc, and a confirmed discard.
  const finish = () => { setShown(false); setTimeout(onClose, 160); };
  const doClose = () => { if (dirty()) setAct({ title: "Leave without saving?", body: "Your changes are lost and the mapping stays as it was last saved.", ok: "Discard & close", danger: true, run: finish }); else finish(); };

  // Open with a fade+scale, lock body scroll, and take Escape (dismiss the confirm if one is
  // open, else close). Capture phase + stopImmediatePropagation so this wins over a parent
  // modal's own Escape when the studio is launched from inside it.
  escRef.current = () => { if (act) setAct(null); else doClose(); };
  useEffect(() => {
    const raf = requestAnimationFrame(() => setShown(true));
    const onKey = (e) => { if (e.key === "Escape") { e.stopImmediatePropagation(); escRef.current?.(); } };
    window.addEventListener("keydown", onKey, true);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { cancelAnimationFrame(raf); window.removeEventListener("keydown", onKey, true); document.body.style.overflow = prev; };
  }, []);

  // ---- render ----
  const anySet = [...mapKeys, ...Object.keys(core)].some((k) => isSet(binds[k])) || xrows.some((r) => isSet(r.state));
  const locDone = requiredKeys.filter((k) => isSet(binds[k])).length;
  const diagKeys = Object.keys(core);
  const diagDone = diagKeys.filter((k) => isSet(binds[k])).length;
  const locComplete = requiredKeys.every((k) => isSet(binds[k]));
  const diagGap = diagKeys.filter((k) => !isSet(binds[k]));
  const d = dirty();

  return createPortal(
    <>
    <div className={`fixed inset-0 z-[60] flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm transition-opacity duration-150 ${shown ? "opacity-100" : "opacity-0"}`} onMouseDown={(e) => { if (e.target === e.currentTarget) doClose(); }}>
      <div className={`flex max-h-[92vh] w-full max-w-6xl flex-col overflow-hidden rounded-xl border border-slate-700 bg-slate-900 shadow-2xl transition duration-150 ${shown ? "scale-100 opacity-100" : "scale-95 opacity-0"}`}>
        {/* header toolbar: actions live here so they never compete with a bottom Save */}
        <div className="flex items-center gap-3 border-b border-slate-800 bg-slate-900/80 px-5 py-3">
          <span className={`h-2 w-2 shrink-0 rounded-full ${d ? "bg-amber-400" : "bg-emerald-400"}`} />
          <div className="flex items-baseline gap-2">
            <h3 className="text-sm font-semibold text-slate-100">Field mapping</h3>
            <span className="font-mono text-[11px] text-slate-500">{service}</span>
          </div>
          <span className={`rounded px-2 py-0.5 text-[10px] font-semibold ${d ? "bg-amber-950/50 text-amber-300" : "bg-slate-800 text-slate-400"}`}>{d ? "● Unsaved changes" : "Editing saved mapping"}</span>
          <div className="ml-auto flex items-center gap-3">
            {d && <button type="button" onClick={doRevert} className="text-[11px] font-semibold text-slate-300 underline decoration-slate-600 underline-offset-2 hover:text-slate-100">Revert changes</button>}
            {anySet && <button type="button" onClick={doClear} className="text-[11px] font-semibold text-slate-300 underline decoration-slate-600 underline-offset-2 hover:text-slate-100">Clear all</button>}
            <button type="button" onClick={doClose} className={btn.ghost}>Close</button>
            <button type="button" onClick={doSave} disabled={busy || !locComplete || !d} className={btn.sky}>{busy ? "Saving…" : "Save & restart"}</button>
          </div>
        </div>

        {loading ? (
          <div className="px-5 py-16 text-center text-xs text-slate-500">Reading the contracts and the vendor sample…</div>
        ) : loadErr ? (
          <div className="m-5 rounded border border-rose-800/50 bg-rose-950/30 p-3 text-xs text-rose-300">Could not load the mapping contracts for {service}: {loadErr}<div className="mt-1 text-slate-500">Edit the schema JSON by hand instead.</div></div>
        ) : !mapSchema ? (
          <div className="m-5 rounded border border-slate-800 bg-slate-950 p-3 text-xs text-slate-400">This adapter does not publish a mapping grammar (/contract/schema), so there is nothing to map here.</div>
        ) : (
          <>
            <Hint focused={focused} focusedName={focused ? matchNameFor(focused) : null} focusedState={focused ? stateFor(focused) : null}
              unnamed={!!focused && isXrowId(focused) && !matchNameFor(focused)}
              binds={binds} leaves={leaves} sample={sample} onUse={(p) => tryBind(focused, p)} />
            <div className="grid flex-1 grid-cols-1 overflow-hidden md:grid-cols-[minmax(300px,1fr)_minmax(360px,1.2fr)]">
              {/* source pane */}
              <div className="overflow-y-auto border-b border-slate-800 bg-slate-950/60 px-4 py-3 md:border-b-0 md:border-r">
                <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">Vendor sample</p>
                <p className="mb-2 text-[11px] text-slate-600">device {sampleIdx + 1} of {(raw || []).length}, from the live discover</p>
                <div className="font-mono text-[11.5px] leading-relaxed">
                  <Tree obj={sample} focused={focused} focusedName={focused ? matchNameFor(focused) : null} focusedState={focused ? stateFor(focused) : null} binds={binds}
                    usedBy={(p) => [...mapKeys, ...diagKeys].find((k) => binds[k]?.mode === "path" && binds[k].path === p)
                      || xrows.find((r) => r.state?.mode === "path" && r.state.path === p)?.key} onLeaf={(p) => focused && tryBind(focused, p)} />
                </div>
              </div>
              {/* targets pane */}
              <div className="overflow-y-auto px-4 py-3">
                <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">Targets</p>
                <p className="mb-3 text-[11px] text-slate-600">Required location fields from the adapter contract; diagnostics from the gateway vocabulary.</p>

                <Group title="Location fix" done={locDone} total={requiredKeys.length}>
                  {mapKeys.map((k) => (
                    <Row key={k} tkey={k} required={requiredKeys.includes(k)} help={mapProps[k]?.description} state={binds[k]} sample={sample} binds={binds} leaves={leaves}
                      focused={focused === k} flash={flashKey === k} advOpen={advOpen.has(k)} pending={pending?.key === k ? pending : null}
                      onFocus={() => { setFocused(k); setPending(null); }} onSrc={() => { setFocused(k); if (binds[k]?.mode === "const") toggleAdv(k); }}
                      onGear={() => toggleAdv(k)} onClear={() => clearBind(k)} onChange={(s) => setBind(k, s)}
                      onConfirm={() => commit(pending.key, pending.path)} onCancelPending={() => setPending(null)} />
                  ))}
                </Group>

                <Group title="Diagnostics" done={diagDone} total={diagKeys.length} optional>
                  {diagKeys.map((k) => (
                    <Row key={k} tkey={k} tierLabel={core[k].tierDefault} help={core[k].description || core[k].help} state={binds[k]} sample={sample} binds={binds} leaves={leaves}
                      focused={focused === k} flash={flashKey === k} advOpen={advOpen.has(k)} pending={pending?.key === k ? pending : null}
                      onFocus={() => { setFocused(k); setPending(null); }} onSrc={() => { setFocused(k); if (binds[k]?.mode === "const") toggleAdv(k); }}
                      onGear={() => toggleAdv(k)} onClear={() => clearBind(k)} onChange={(s) => setBind(k, s)}
                      onConfirm={() => commit(pending.key, pending.path)} onCancelPending={() => setPending(null)} />
                  ))}
                </Group>

                <Group title={`${extBag} (extra vendor fields)`} done={xrows.filter((r) => isSet(r.state)).length} total={xrows.length}
                  action={<button type="button" onClick={addXrow} className="rounded border border-slate-700 px-1.5 py-0.5 text-[10.5px] text-slate-400 hover:border-sky-500/50 hover:text-sky-300">＋ add field</button>}>
                  {xrows.length === 0 && <p className="text-[10.5px] text-slate-600">Anything not in the core vocabulary above, carried as authored (e.g. a vendor's own usage counters).</p>}
                  {xrows.map((r, i) => {
                    const id = xrowId(i);
                    const issue = xrowIssue(i);
                    return (
                      <div key={i} className="flex flex-col gap-1.5">
                        <div className="flex items-center gap-2">
                          <input ref={(el) => (nameInputRefs.current[i] = el)}
                            className={`w-44 rounded border bg-slate-900 px-2 py-1 font-mono text-[11px] text-slate-200 focus:border-sky-500 focus:outline-none ${flashKey === id ? "border-sky-500 ring-2 ring-sky-500/50" : "border-slate-700"}`}
                            placeholder="name it, e.g. usageMoving" value={r.key}
                            onChange={(e) => setXrows((rs) => rs.map((x, j) => (j === i ? { ...x, key: e.target.value } : x)))} />
                          <select className="rounded border border-slate-700 bg-slate-900 px-1 py-1 text-[10px] text-slate-300" value={r.tier}
                            onChange={(e) => setXrows((rs) => rs.map((x, j) => (j === i ? { ...x, tier: e.target.value } : x)))}>
                            <option value="onDemand">onDemand</option>
                            <option value="stream">stream</option>
                          </select>
                          <button type="button" title="remove this field" className="ml-auto shrink-0 rounded border border-slate-700 px-1.5 text-[10px] text-slate-500 hover:border-rose-500 hover:text-rose-400"
                            onClick={() => { setXrows((rs) => rs.filter((_, j) => j !== i)); if (focused === id) setFocused(null); }}>✕</button>
                        </div>
                        <Row tkey={r.key || "(name it above)"} help="vendor-specific: not in the core vocabulary, carried as authored" state={r.state} sample={sample} binds={binds} leaves={leaves}
                          focused={focused === id} flash={flashKey === id} advOpen={advOpen.has(id)} pending={pending?.key === id ? pending : null}
                          onFocus={() => { setFocused(id); setPending(null); }} onSrc={() => { setFocused(id); if (r.state?.mode === "const") toggleAdv(id); }}
                          onGear={() => toggleAdv(id)} onClear={() => clearBind(id)} onChange={(s) => setStateFor(id, s)}
                          onConfirm={() => commit(pending.key, pending.path)} onCancelPending={() => setPending(null)} />
                        {issue && <p className="text-[10px] text-amber-300">{issue}</p>}
                      </div>
                    );
                  })}
                </Group>
              </div>
            </div>

            {/* footer: informational only; the gate is location completeness */}
            <div className="flex items-center gap-4 border-t border-slate-800 bg-slate-900/80 px-5 py-2.5 text-[11px]">
              <span className="text-slate-400">Location <b className={`font-mono ${locComplete ? "text-emerald-400" : "text-amber-400"}`}>{locDone}/{requiredKeys.length}</b></span>
              <span className="text-slate-400">Diagnostics <b className="font-mono text-slate-300">{diagDone}/{diagKeys.length}</b></span>
              <span className="text-slate-500">
                {!locComplete ? `Location incomplete. ${requiredKeys.filter((k) => !isSet(binds[k])).join(", ")} still needs a source.`
                  : diagGap.length ? <>Ready to save. <span className="text-amber-400">{diagGap.length} optional diagnostic{diagGap.length > 1 ? "s" : ""} unmapped</span> ({diagGap.join(", ")}). They won't be reported.</>
                  : "Saving writes the schema and rolls the adapter to pick it up."}
              </span>
            </div>
          </>
        )}
      </div>
    </div>

    {act && (
      <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm" onMouseDown={(e) => { if (e.target === e.currentTarget) setAct(null); }}>
        <div className={`w-full max-w-md rounded-xl border border-slate-700 bg-slate-900 p-5 shadow-2xl border-t-[3px] ${act.danger ? "border-t-amber-400" : "border-t-sky-500"}`}>
          <div className="mb-2 flex items-center gap-2.5">
            <span className={`grid h-6 w-6 shrink-0 place-items-center rounded-full text-sm font-bold ${act.danger ? "bg-amber-950/50 text-amber-300" : "bg-sky-950/50 text-sky-300"}`}>{act.danger ? "!" : "↑"}</span>
            <h4 className="text-sm font-semibold text-slate-100">{act.title}</h4>
          </div>
          <p className="mb-4 text-xs leading-relaxed text-slate-400">{act.body}</p>
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => setAct(null)} className={btn.ghost}>{act.danger ? "Keep editing" : "Not now"}</button>
            <button type="button" onClick={() => { const run = act.run; setAct(null); run?.(); }} className={act.danger ? "rounded bg-amber-500 px-3.5 py-1.5 text-xs font-semibold text-amber-950 hover:bg-amber-400" : btn.sky}>{act.ok}</button>
          </div>
        </div>
      </div>
    )}
    </>,
    document.body
  );
}

// ---- hint bar ----
function Hint({ focused, focusedName, focusedState, unnamed, binds, leaves, sample, onUse }) {
  if (!focused) return <div className="border-b border-slate-800 bg-slate-950/60 px-5 py-2 text-[11.5px] text-slate-400"><span className="mr-2 inline-block h-1.5 w-1.5 rounded-full bg-sky-400 align-middle" />Select a target on the right, then click a value on the left to assign it.</div>;
  // A just-added extra field: no name yet, so there is nothing to suggest or bind
  // against - naming it is the next step, not picking a value.
  if (unnamed) return <div className="border-b border-slate-800 bg-slate-950/60 px-5 py-2 text-[11.5px] text-slate-300"><span className="mr-2 inline-block h-1.5 w-1.5 rounded-full bg-sky-400 align-middle" />Click a value on the left - it names the field for you (you can rename it after).</div>;
  const key = focusedName; // display + match name; never the __x:<i> storage id
  const state = focusedState;
  const setNow = isSet(state);
  const sug = suggestionsFor(key, leaves, binds);
  const top = sug[0];
  const conf = confidence(state, key, binds, sample);
  const topIsCurrent = top && state?.mode === "path" && state.path === top.path;
  let body;
  if (setNow) {
    const where = state.mode === "const" ? "a constant" : <span className="font-mono text-sky-300">{state.path}</span>;
    const stronger = conf?.lvl === "warn" && top && !topIsCurrent && tier(top.path, top.value, key, binds).level >= 2 ? top : null;
    body = (<><span className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-400 align-middle" /> <span><b className="text-slate-200">{key}</b> is set to {where}.</span>
      {stronger
        ? <><span className="text-slate-500">stronger match</span> <span className="font-mono text-sky-300">{stronger.path}</span> <button type="button" onClick={() => onUse(stronger.path)} className="rounded border border-emerald-700/40 bg-emerald-950/40 px-2 py-0.5 text-[10.5px] font-semibold text-emerald-300 hover:bg-emerald-900/40">use it</button></>
        : <span className="text-slate-500">Click another value to replace, or ✕ on the field to clear.</span>}</>);
  } else if (top) {
    const others = sug.length - 1;
    body = (<><span className="inline-block h-1.5 w-1.5 rounded-full bg-sky-400 align-middle" /> <span>Suggested for <b className="text-slate-200">{key}</b>: <span className="font-mono text-sky-300">{top.path}</span></span>
      <button type="button" onClick={() => onUse(top.path)} className="rounded border border-emerald-700/40 bg-emerald-950/40 px-2 py-0.5 text-[10.5px] font-semibold text-emerald-300 hover:bg-emerald-900/40">use it</button>
      <span className="text-slate-500">{others > 0 ? `${others} other field${others === 1 ? "" : "s"} share the name (amber). This one sits with the position.` : "or click any value; a looser match asks first."}</span></>);
  } else {
    body = (<><span className="inline-block h-1.5 w-1.5 rounded-full bg-sky-400 align-middle" /> <span>Nothing in the vendor data matches <b className="text-slate-200">{key}</b>. Click a value to bind (you'll confirm), or set a constant with ⚙.</span></>);
  }
  return <div className="flex flex-wrap items-center gap-2 border-b border-slate-800 bg-slate-950/60 px-5 py-2 text-[11.5px] text-slate-300">{body}</div>;
}

// ---- source tree ----
function Tree({ obj, prefix = "", depth = 0, focused, focusedName, focusedState, binds, usedBy, onLeaf }) {
  return (
    <>
      {Object.entries(obj).map(([k, v]) => {
        const p = prefix ? `${prefix}.${k}` : k;
        const pad = { paddingLeft: 8 + depth * 15 };
        if (v && typeof v === "object" && !Array.isArray(v)) {
          return (
            <div key={p}>
              <div style={pad} className="py-[1px] text-slate-400">{k}</div>
              <Tree obj={v} prefix={p} depth={depth + 1} focused={focused} focusedName={focusedName} focusedState={focusedState} binds={binds} usedBy={usedBy} onLeaf={onLeaf} />
            </div>
          );
        }
        const u = usedBy(p);
        // The leaf the FOCUSED field is currently bound to: highlight it (sky) and never mute
        // it, even when it isn't a name suggestion, so you can see where the field points.
        // focusedState/focusedName are resolved by the parent (Row.state for a mapKey/core
        // key lives in `binds`, an extra field's lives on its own xrows entry, and its match
        // name is what the operator typed, not its `__x:<i>` storage id - see matchNameFor).
        const boundHere = !!focused && focusedState?.mode === "path" && focusedState.path === p;
        // No name yet (a just-added extra field): nothing to rank a leaf against, so skip the
        // match tiers entirely rather than showing every leaf as a false "no match" - that just
        // buries the `usedBy` tags that are the actual point right now (find an unused one).
        const hasName = focused && (!isXrowId(focused) || focusedName);
        const tt = hasName ? tier(p, v, focusedName, binds) : { level: 0 };
        const cls = !focused || boundHere || !hasName ? "" : tt.level >= 2 ? "bg-emerald-950/30 shadow-[inset_2px_0_0_#34d399]" : tt.level === 1 ? "bg-amber-950/20 shadow-[inset_2px_0_0_#fbbf24] opacity-90" : "opacity-50";
        const clickable = !!focused;
        return (
          <div key={p} style={boundHere ? { ...pad, boxShadow: "inset 2px 0 0 #38bdf8", backgroundColor: "rgba(56,189,248,0.12)" } : pad}
            className={`flex items-baseline gap-2 rounded-sm py-[1.5px] pr-1.5 ${cls} ${clickable ? "cursor-pointer hover:bg-sky-950/40 hover:opacity-100" : ""}`}
            onClick={() => clickable && onLeaf(p)}>
            <span className="text-slate-400">{k}</span>
            <span className={valColor(v)}>{fmt(v)}</span>
            {u ? <span className="ml-auto rounded border px-1.5 text-[9px]" style={boundHere ? { borderColor: "#38bdf8", color: "#38bdf8" } : { borderColor: "#334155", color: "#94a3b8" }}>{boundHere ? `${u} ←` : u}</span>
              : tt.level >= 2 ? <span className="ml-auto rounded bg-emerald-950/50 px-1.5 text-[9px] text-emerald-300">match</span>
              : tt.level === 1 ? <span className="ml-auto rounded bg-amber-950/40 px-1.5 text-[9px] text-amber-300">named</span> : null}
          </div>
        );
      })}
    </>
  );
}

// ---- target group ----
function Group({ title, done, total, optional, action, children }) {
  const ok = total > 0 && done === total;
  return (
    <div className="mb-4">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">{title}</span>
        <span className={`rounded px-1.5 py-0.5 font-mono text-[10px] ${ok ? "bg-emerald-950/40 text-emerald-300" : "bg-slate-800 text-slate-400"}`}>{done}/{total}</span>
        {optional && <span className="text-[10px] text-slate-600">optional telemetry</span>}
        {action && <span className="ml-auto">{action}</span>}
      </div>
      <div className="flex flex-col gap-1.5">{children}</div>
    </div>
  );
}

// ---- one target row ----
function Row({ tkey, required, tierLabel, help, state, sample, binds, leaves, focused, flash, advOpen, pending, onFocus, onSrc, onGear, onClear, onChange, onConfirm, onCancelPending }) {
  const set = isSet(state);
  const val = resolvedOf(state, sample);
  const conf = confidence(state, tkey, binds, sample);
  const border = required && !set ? "border-l-2 border-l-amber-400" : conf?.lvl === "warn" ? "border-l-2 border-l-amber-400" : "";
  return (
    <div className={`rounded-lg border border-slate-800 bg-slate-900/60 p-2.5 ${focused ? "ring-1 ring-sky-500/60" : ""} ${flash ? "ring-2 ring-emerald-500/70" : ""} ${border}`}>
      <div className="flex items-center gap-2">
        <span className="font-mono text-xs font-semibold text-slate-200" title={help || undefined}>{tkey}{required && <span className="text-rose-400"> *</span>}</span>
        {help && <span className="cursor-help text-[10px] text-slate-600" title={help}>ⓘ</span>}
        {tierLabel && <span className={`rounded border px-1.5 py-0.5 text-[9px] uppercase ${tierLabel === "stream" ? "border-violet-400/30 text-violet-300" : "border-slate-700 text-slate-400"}`}>{tierLabel}</span>}
        <button type="button" onClick={onGear} title="edit source (field / constant / convert)" className={`ml-auto rounded border px-1.5 py-0.5 text-[11px] ${advOpen ? "border-sky-500/50 bg-sky-950/40 text-sky-300" : "border-slate-700 text-slate-500 hover:text-slate-300"}`}>⚙</button>
      </div>

      {pending ? (
        <div className="mt-2 rounded border border-amber-500/40 bg-amber-950/30 p-2 text-[11px] text-slate-200">
          <div className="mb-1.5">Bind <b>{tkey}</b> ← <span className="font-mono text-sky-300">{pending.path}</span> <span className="text-slate-500">= {fmt(resolvePath(sample, pending.path))}</span><br /><span className="text-amber-300">{weakReason(pending.path, tkey, binds, sample)}</span></div>
          <div className="flex gap-2">
            <button type="button" onClick={onConfirm} className="rounded bg-amber-500 px-2.5 py-1 text-[11px] font-semibold text-amber-950">Bind anyway</button>
            <button type="button" onClick={onCancelPending} className="rounded border border-slate-700 bg-slate-800 px-2.5 py-1 text-[11px] font-semibold text-slate-300">Cancel</button>
          </div>
        </div>
      ) : (
        <div className="mt-2 flex items-center gap-2">
          <button type="button" onClick={onSrc} onFocus={onFocus}
            className={`flex-1 truncate rounded border px-2 py-1.5 text-left font-mono text-[11px] ${set ? (state.mode === "const" ? "border-slate-700 bg-slate-950 text-amber-300" : "border-slate-700 bg-slate-950 text-sky-300") : "border-dashed border-slate-600 bg-slate-950 text-slate-500"} hover:border-sky-500/50`}>
            {!set ? "click a field on the left" : state.mode === "const" ? `= ${state.value}` : state.path || "—"}
          </button>
          {set && (
            <>
              <span className="shrink-0 text-slate-500">→</span>
              <span className={`shrink-0 max-w-[34%] truncate font-mono text-[11px] ${val === undefined ? "text-amber-400" : "text-slate-200"}`}>{fmt(val)}</span>
              {state.format && <span className="shrink-0 text-[9px] text-slate-500">iso</span>}
              {state.transform && <span className="shrink-0 text-[9px] text-slate-500">{state.transform.type}</span>}
              {conf && <span title={conf.msg} className={`shrink-0 cursor-help rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase ${conf.lvl === "ok" ? "bg-emerald-950/40 text-emerald-300" : "bg-amber-950/40 text-amber-300"}`}>{conf.lvl === "ok" ? "match" : "check"}</span>}
              <button type="button" onClick={onClear} title="clear this field" className="shrink-0 rounded border border-slate-700 px-1.5 text-[10px] text-slate-500 hover:border-rose-500 hover:text-rose-400">✕</button>
            </>
          )}
          {!set && required && <><span className="shrink-0 text-slate-500">→</span><span className="shrink-0 font-mono text-[11px] text-amber-400">required</span></>}
        </div>
      )}

      {advOpen && <SourceEditor tkey={tkey} state={state} onChange={onChange} val={val} />}
    </div>
  );
}

// ---- source editor: kind (field | constant) first; conversions only under a path ----
function SourceEditor({ tkey, state, onChange, val }) {
  const s = state || specToState(null);
  const up = (patch) => onChange({ ...s, ...patch });
  const tx = s.transform;
  const isset = isSet(s);
  const inp = "rounded border border-slate-700 bg-slate-900 px-2 py-1 font-mono text-[11px] text-slate-200 focus:border-sky-500 focus:outline-none";
  return (
    <div className="mt-2 flex flex-col gap-2 rounded-lg border border-slate-800 bg-slate-950/70 p-2.5">
      <div className="flex items-center gap-2">
        <span className="w-12 shrink-0 text-[9.5px] font-semibold uppercase tracking-wide text-slate-500">Source</span>
        <div className="inline-flex overflow-hidden rounded border border-slate-700">
          <button type="button" onClick={() => up({ mode: "path" })} className={`px-2.5 py-1 text-[10.5px] ${s.mode === "path" ? "bg-slate-700 text-slate-100" : "bg-slate-900 text-slate-500"}`}>vendor field</button>
          <button type="button" onClick={() => up({ mode: "const" })} className={`px-2.5 py-1 text-[10.5px] ${s.mode === "const" ? "bg-slate-700 text-slate-100" : "bg-slate-900 text-slate-500"}`}>constant</button>
        </div>
      </div>
      {s.mode === "const" ? (
        <>
          <div className="flex items-center gap-2">
            <span className="w-12 shrink-0 text-[9.5px] font-semibold uppercase tracking-wide text-slate-500">Value</span>
            <input autoFocus className={`${inp} flex-1`} value={s.value} placeholder="e.g. wgs84, or 0" onChange={(e) => up({ value: e.target.value })} />
          </div>
          <p className="text-[10px] text-slate-500">A fixed value, written as-is. Use it for a field the vendor's data doesn't carry.</p>
        </>
      ) : (
        <>
          <div className="flex items-center gap-2">
            <span className="w-12 shrink-0 text-[9.5px] font-semibold uppercase tracking-wide text-slate-500">Field</span>
            <span className={`flex-1 truncate rounded border px-2 py-1 font-mono text-[11px] ${s.path ? "border-slate-700 bg-slate-900 text-sky-300" : "border-dashed border-slate-700 bg-slate-900 text-slate-500"}`}>{s.path || "Click a value on the left"}</span>
          </div>
          <div className="flex flex-col gap-2 rounded-lg border border-slate-800 bg-slate-900/60 p-2">
            <div className="text-[9.5px] font-semibold uppercase tracking-wide text-slate-400">Convert <span className="ml-1 font-normal normal-case tracking-normal text-slate-500">optional, applied to the vendor value</span></div>
            <div className="flex flex-wrap items-center gap-2 text-[10.5px] text-slate-300">
              <label className="inline-flex items-center gap-1.5">transform
                <select className="rounded border border-slate-700 bg-slate-900 px-1.5 py-1 text-[10.5px] text-slate-200" value={tx ? tx.type : ""}
                  onChange={(e) => { const t = e.target.value; up({ transform: t === "bool" ? { type: "bool", truthy: [] } : t === "linear" ? { type: "linear", scale: 1, offset: 0 } : null }); }}>
                  <option value="">none (pass through)</option>
                  <option value="bool">bool (from matches)</option>
                  <option value="linear">linear (scale + offset)</option>
                </select>
              </label>
              {tx?.type === "bool" && <label className="inline-flex items-center gap-1.5">true when in <input className={`${inp} w-32`} placeholder="MOVING" value={(tx.truthy || []).join(",")} onChange={(e) => up({ transform: { type: "bool", truthy: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) } })} /></label>}
              {tx?.type === "linear" && <><label className="inline-flex items-center gap-1">× <input className={`${inp} w-16`} value={tx.scale} onChange={(e) => up({ transform: { ...tx, scale: Number(e.target.value) || 0 } })} /></label><label className="inline-flex items-center gap-1">+ <input className={`${inp} w-16`} value={tx.offset ?? 0} onChange={(e) => up({ transform: { ...tx, offset: Number(e.target.value) || 0 } })} /></label></>}
            </div>
            <div className="flex flex-wrap items-center gap-3 text-[10.5px] text-slate-300">
              <label className="inline-flex items-center gap-1.5"><input type="checkbox" checked={s.format === "iso8601"} onChange={(e) => up({ format: e.target.checked ? "iso8601" : null })} /> source is an ISO-8601 time</label>
              <label className="inline-flex items-center gap-1.5">if missing, use <input className={`${inp} w-16`} placeholder="none" value={s.default ?? ""} onChange={(e) => up({ default: e.target.value })} /></label>
            </div>
          </div>
        </>
      )}
      <div className="flex items-center justify-between border-t border-slate-800 pt-2">
        <span className="text-[10px] text-slate-500">applies as you edit{isset ? <> · now <span className="font-mono text-sky-300">{fmt(val)}</span></> : null}</span>
      </div>
    </div>
  );
}


