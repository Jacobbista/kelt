# CAMARA private-network profile — design brief for `5g-northbound`

Working notes for the northbound refactor. Frames the positioning/CAMARA model
around the position paper *"Private Networks, Public APIs: Exposing Hybrid
Positioning through CAMARA in Industrial 6G"* (RISE, 6GHYPE4Ind). The paper is
the conceptual authority; this brief states the decisions taken for the KELT
testbed deployment. Not a testbed canonical doc (cross-repo), so it lives at root.

## Frame: assets, not subscribers

The tracked entities are **assets** (UWB tags, WiFi-located items), not cellular
subscribers. They have no MSISDN/IMSI/NAI and never will. Consequences:

- Open5GS is the **private-network setting + connectivity substrate**, NOT the
  position source. UEs/subscribers are connected devices, not positioning targets.
- Positioning is **non-3GPP and independent of the core**. The CAMARA gateway is
  fed by the fusion engine, NOT by NEF/LMF (those are pedagogical analogies only).
- The asset registry is **deliberately decoupled** from the subscriber DB.
  Coupling them would re-import the public-network assumption the paper rejects.
  This separation is the design, not an inconsistency to fix.

## The three decisions (mapping the paper's three gaps)

### A. Device identity (gap 1) — asset identifier is first-class

Do NOT borrow the MSISDN slot (a UWB tag is not a phone number). Expose the asset
through an **explicit asset identifier**, first-class in the CAMARA `device`
object. Two implementation paths for the other agent to pick:

1. New `device` member (e.g. `assetId`) — cleanest semantics; diverges from the
   stock CAMARA schema, so document it as a private-profile extension.
2. Carry it in `networkAccessIdentifier` with an asset scheme
   (e.g. `pkg-4471@fiskarheden.assets`) — stays a standard CAMARA field,
   repurposed; less schema churn, slightly less explicit.

Either way the gateway resolves the asset identifier through the **Asset Identity
Map** to a positioning device id + adapter. No subscriber lookup.

### B. Authorisation (gap 3) — 2-legged, collapsed roles

No three-legged consent: in a factory the operator owns network + assets + apps,
so the consent arbiter role collapses. Use **enterprise-issued tokens**:

- one Keycloak confidential client **per consumer** (identity, audit via `azp`,
  per-consumer rotate/revoke) — replaces the single shared `camara-gateway` secret;
- an **`org` scope/claim** that the gateway joins against the asset's `org` so a
  consumer only sees its tenant's assets;
- no new realm role (reuse `camara-location-read`); `org` is an orthogonal tenant
  dimension. Optional mTLS for internal engine↔gateway trust.

### C. Supporting extensions (gaps 1/2) — keep in the model, secondary

- `source` / `kind` metadata (a UWB fix is trusted differently from a WiFi fix at
  the same radius);
- optional `z`/altitude + vertical-accuracy (multi-floor, stacked storage);
- streaming channel alongside pull retrieval (moving forklifts/tools).

## Entity model (Asset Identity Map — first-class in northbound)

```yaml
entity:
  asset_id: pkg-4471                 # first-class asset identifier (NOT MSISDN/IMSI)
  positioning_id: D00124B00249ECBB2  # internal id → adapter routing
  kind: uwb-tag                      # uwb-tag | forklift | tool | pallet ...
  source: wittra                     # adapter / modality (source metadata)
  org: fiskarheden                   # tenant → authorisation scope
  metadata: { floor: 0, label: "Timber bundle 01" }
```

No cellular fields. The entity is an asset, not a subscriber.

## Ownership split

- **northbound (this repo):** the Asset Identity Map (schema + resolution),
  asset-id as a first-class CAMARA `device` identifier, `kind`/`source` metadata,
  the gateway authorisation join (`org` scope, enterprise token), profile
  extensions (z, streaming).
- **KELT testbed:** host/manage the asset registry data (a dashboard editor,
  separate plane from subscribers); wire per-consumer Keycloak clients + `org`
  claim once the model is fixed; orchestrate adapters; provide the private-network
  setting (Open5GS core, MEC).

## Asks for the other agent

1. Pick the asset-id carrier (new `device` member vs NAI scheme) and make the
   gateway resolve it via the Asset Identity Map (no subscriber path).
2. Promote the registry to the first-class entity model above.
3. Add the gateway authorisation join: token `org` claim vs entity `org`, on top
   of the existing role check; document the 2-legged enterprise-token profile.
