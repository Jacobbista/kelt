# No 3GPP Network-Based Positioning

The testbed does not compute device position from the radio network: Open5GS ships no LMF, and the RAN is a closed commercial femtocell (BTI nCell F2240) that exposes no LPP or NRPPa measurements, so the 3GPP positioning path (RAN measurements feeding a Location Management Function) cannot be assembled.

Position is instead sourced from non-3GPP adapters (UWB, Wi-Fi) fused by the positioning-engine and exposed through the CAMARA gateway as a private-network profile. The CAMARA Location data model carries no field naming the underlying technology, so the same API surface serves these fixes without modification. See [../architecture/positioning-adapters.md](../architecture/positioning-adapters.md) for the adapter architecture; the private-profile surface is owned upstream in `5g-northbound`.

This is a component boundary, not a platform one. KELT orchestrates the stack, so the network-based path becomes available by attaching an LMF-capable or open RAN, or a simulator that exposes positioning, together with an LMF (see the experimental NF direction in [../architecture/nf-platform-dev-plan.md](../architecture/nf-platform-dev-plan.md)). That direction is tracked in [../roadmap.md](../roadmap.md).
