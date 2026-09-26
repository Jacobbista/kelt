# 5G Network Functions - Architecture & Theory

> **Status**: DRAFT - To be completed with detailed NF analysis
>
> This document provides in-depth theoretical coverage of each Network Function deployed in Phase 5, including 3GPP specifications alignment, message flows, and implementation details.

## Document Purpose

This document serves to:

1. Validate theoretical alignment between Open5GS implementation and 3GPP specifications
2. Document each NF's responsibilities, interfaces, and protocols
3. Provide troubleshooting context for protocol-level issues
4. Serve as reference for future enhancements (network slicing, QoS policies, etc.)

## Network Functions Overview

### Control Plane Functions

#### 1. NRF (Network Repository Function) - 3GPP TS 23.501 §6.2.6

**Purpose**: Service discovery and registration for all NFs

**Interfaces**:

- SBI: HTTP/2 REST API on port 7777
- Metrics: Prometheus endpoint on port 9090

**Key Operations**:

- `NFRegister`: NF registration with profile (NFType, PLMN, services)
- `NFUpdate`: Profile updates (status changes, capacity)
- `NFDeregister`: Graceful NF removal
- `NFDiscover`: Query available NFs by type, PLMN, or capability

**Database**: MongoDB (stores NF profiles)

**Message Flow Example** (AMF registration):

```
AMF → NRF: POST /nnrf-nfm/v1/nf-instances
  {
    "nfInstanceId": "uuid",
    "nfType": "AMF",
    "plmnList": [{"mcc": "001", "mnc": "01"}],
    "amfInfo": { ... }
  }
NRF → AMF: 201 Created
  Location: /nnrf-nfm/v1/nf-instances/{nfInstanceId}
```

**Deployment Considerations**:

- Must start before other NFs (dependency)
- Single instance sufficient for lab/testbed
- Production: Deploy redundant instances with load balancing

**Open5GS Implementation Notes**:

- NRF uses MongoDB for persistent NF profile storage
- Heartbeat: NFUpdate every 60 seconds (default)
- Discovery: Priority-based selection (NF load, locality)

---

#### 2. AMF (Access and Mobility Management Function) - 3GPP TS 23.501 §6.2.2

**Purpose**: Mobility management, UE authentication, session establishment

**Interfaces**:

- **N1**: NAS signaling to/from UE (SCTP/NAS)
- **N2**: NGAP signaling to/from gNB (SCTP/NGAP)
- **SBI**: REST APIs to NRF, AUSF, UDM, SMF
- Metrics: Prometheus endpoint on port 9090

**Key Procedures**:

- **Registration**: UE attach, authentication (5G-AKA), security mode
- **Mobility**: Handover, tracking area updates
- **Session Management**: SM-PDU forwarding to SMF
- **Paging**: UE reachability in idle mode

**Network Attachments** (Multus):

- N1: `10.201.0.100/24` (static IP from NAD `n1-net`)
- N2: `10.202.0.100/24` (static IP from NAD `n2-net`)

**Message Flow Example** (UE Registration):

```
UE → gNB → AMF (N2): InitialUEMessage (RegistrationRequest)
AMF → AUSF (SBI): POST /nausf-auth/v1/ue-authentications
AMF → UDM (SBI): GET /nudm-uecm/v1/{supi}/registrations/amf-3gpp-access
AMF → gNB → UE (N2/N1): DownlinkNASTransport (RegistrationAccept)
```

**Deployment Considerations**:

- Requires secondary network interfaces (N1, N2) via Multus
- Privileged container for network operations
- Static IPs critical for gNB configuration

**Open5GS Implementation Notes**:

- Single AMF instance (no regions/sets in testbed)
- GUAMI: MCC=001, MNC=01, AMF Region/Set/Pointer = default
- N2 NGAP: InitialUEMessage, HandoverRequest/Required, Paging supported

---

#### 3. SMF (Session Management Function) - 3GPP TS 23.501 §6.2.3

**Purpose**: PDU session management, UPF selection, IP address allocation

**Interfaces**:

- **N4**: PFCP to UPF(s) for session rules (UDP/PFCP)
- **SBI**: REST APIs to NRF, AMF, PCF, UDM
- Metrics: Prometheus endpoint on port 9090

**Key Procedures**:

- **PDU Session Establishment**: QoS flow setup, UPF selection
- **PDU Session Modification**: QoS changes, UPF reselection
- **PDU Session Release**: Cleanup session state
- **Charging Triggers**: Coordinates with CHF (not deployed in this testbed)

**Network Attachments** (Multus):

- N4: `10.204.0.100/24` (static IP for PFCP)

**PFCP Session Flow** (SMF → UPF):

```
SMF → UPF (N4 PFCP): Session Establishment Request
  {
    CREATE PDR: match UE IP, tunnel ID
    CREATE FAR: forward to N6 interface
    CREATE QER: enforce QoS (GBR/MBR)
  }
UPF → SMF (N4 PFCP): Session Establishment Response
```

**Deployment Considerations**:

- Privileged container (PFCP socket operations)
- N4 interface on overlay network (VXLAN tunnel to UPFs)
- Configures multiple UPFs (cloud + edge) with DNN mapping

**Open5GS Implementation Notes**:

- DNN selection: `internet` (UPF-Cloud), `mec` (UPF-Edge)
- UPF selection: Static config in `smf.yaml` (no dynamic load balancing)
- PFCP: Session Establishment/Modification/Deletion fully supported

---

#### 4. UDM (Unified Data Management) - 3GPP TS 23.501 §6.2.7

**Purpose**: Subscriber data management, authentication credential processing

**Interfaces**:

- **SBI**: REST APIs to NRF, AMF, AUSF, SMF

**Key Operations**:

- **Authentication**: Retrieves authentication vectors from UDR for AUSF
- **Registration**: Stores UE registration state (AMF ID, access type)
- **Subscription Data**: Provides subscriber profiles to AMF/SMF
- **Event Exposure**: Notifies consumers of subscriber events

**Data Stored** (in MongoDB via UDR):

- Subscriber profile (QoS, DNN list, slice subscriptions)
- Authentication credentials (K, OPc keys)
- Registration context

**Deployment Considerations**:

- Worker node (centralized data access)
- HTTP/2 SBI only (no secondary network interfaces)
- Reads/writes to MongoDB via UDR APIs

---

#### 5. UDR (Unified Data Repository) - 3GPP TS 23.501 §6.2.8

**Purpose**: Unified storage backend for subscriber data and policy data

**Interfaces**:

- **SBI**: REST APIs to UDM, PCF, NEF
- **Database**: MongoDB connection for persistent storage

**Key Operations**:

- `QueryData`: Retrieve subscriber/policy data
- `UpdateData`: Store UE registration, session state
- `SubscribeData`: Notify consumers of data changes

**Database Schema** (MongoDB):

- `subscribers`: IMSI, K, OPc, QoS profiles
- `policy_data`: PCF policy rules
- `registration_data`: AMF ID, registration status

**Deployment Considerations**:

- Worker node (co-located with MongoDB)
- Backend for UDM/PCF (not directly accessed by UE/RAN)

---

#### 6. PCF (Policy Control Function) - 3GPP TS 23.501 §6.2.9

**Purpose**: Policy control and charging rules for PDU sessions

**Interfaces**:

- **SBI**: REST APIs to NRF, SMF, UDR

**Key Procedures**:

- **SM Policy Control**: QoS flow rules for PDU sessions
- **AM Policy Control**: Mobility restrictions, access rules
- **Policy Authorization**: Service data flow (SDF) templates

**Policy Types**:

- **QoS**: 5QI, GBR/MBR, packet filter rules
- **Charging**: Trigger conditions, rating groups
- **Traffic Steering**: Route selection, N6 breakout rules

**Message Flow** (SM Policy):

```
SMF → PCF: POST /npcf-smpolicycontrol/v1/sm-policies
  {
    "supi": "imsi-001010000000001",
    "dnn": "internet",
    "pduSessionId": 1
  }
PCF → SMF: 201 Created
  {
    "policyDecision": {
      "qosDecs": {...},
      "chargingDecs": {...}
    }
  }
```

**Deployment Considerations**:

- Worker node (control-plane only)
- Can be scaled independently for high policy workload

---

#### 7. BSF (Binding Support Function) - 3GPP TS 23.501 §6.2.11

**Purpose**: Maintains PCF binding information for PDU sessions

**Interfaces**:

- **SBI**: REST APIs to NRF, PCF, NEF

**Key Operations**:

- **PCF Binding**: Stores SUPI → PCF mapping
- **Session Binding**: Tracks which PCF instance handles which PDU session
- **Discovery Assistance**: Helps NFs find the correct PCF for a UE

**Use Case**:
When a UE has multiple PDU sessions, BSF ensures all sessions use the same PCF instance for policy consistency.

**Deployment Considerations**:

- Worker node (control-plane)
- Lightweight (stores bindings only, not policy data)
- Optional: can be omitted in simple deployments (direct NRF discovery)

---

#### 8. NSSF (Network Slice Selection Function) - 3GPP TS 23.501 §6.2.10

**Purpose**: Network slice selection and allocation

**Interfaces**:

- **SBI**: REST APIs to NRF, AMF

**Key Operations**:

- **Slice Selection**: Selects S-NSSAI (Slice/Service Type + Slice Differentiator)
- **AMF Selection**: Chooses appropriate AMF based on slice requirements
- **Allowed NSSAI**: Returns list of allowed slices for UE

**Slice Types** (3GPP TS 23.501 §5.15.2):

- **SST 1**: eMBB (Enhanced Mobile Broadband)
- **SST 2**: URLLC (Ultra-Reliable Low-Latency Communications)
- **SST 3**: mMTC (Massive Machine-Type Communications)

**Message Flow**:

```
AMF → NSSF: GET /nnssf-nsselection/v1/network-slice-information
  Query: nst=1, tai={mcc:001, mnc:01, tac:1}
NSSF → AMF: 200 OK
  {
    "allowedNssaiList": [{"sst": 1, "sd": "000001"}]
  }
```

**Deployment Considerations**:

- Worker node (control-plane)
- **Current testbed**: Single slice (SST=1, SD=000001)
- Production: multiple slices with different QoS/isolation

---

#### 9. AUSF (Authentication Server Function) - 3GPP TS 23.501 §6.2.12

**Purpose**: UE authentication (5G-AKA primary method)

**Interfaces**:

- **SBI**: REST APIs to NRF, AMF, UDM

**Key Procedures**:

- **5G-AKA**: Authentication and Key Agreement using SUPI, K, OPc
- **EAP-AKA'**: Extensible Authentication Protocol (for non-3GPP access)

**Authentication Flow** (5G-AKA):

```
AMF → AUSF: POST /nausf-auth/v1/ue-authentications
  {"servingNetworkName": "5G:001:01"}
AUSF → UDM: GET /nudm-ueau/v1/{supi}/security-information/generate-auth-data
UDM → AUSF: 200 OK {authenticationVector: {rand, autn, xres*, kseaf}}
AUSF → AMF: 201 Created {authenticationVector: {rand, autn, hxres*}}
AMF → UE (N1): AuthenticationRequest(RAND, AUTN)
UE → AMF (N1): AuthenticationResponse(RES*)
AMF → AUSF: PUT /nausf-auth/v1/ue-authentications/{id}/5g-aka-confirmation
  {res*}
AUSF → AMF: 200 OK {result: "AUTHENTICATION_SUCCESS"}
```

**Security Keys**:

- **RAND**: Random challenge (128 bits)
- **AUTN**: Authentication token (128 bits)
- **XRES*/RES***: Expected/received response
- **KSEAF**: Security anchor function key (256 bits)

**Deployment Considerations**:

- Worker node (control-plane)
- Stateless (authentication vectors from UDM)
- Critical for UE attach (must be available)

---

### User Plane Function

#### 10. UPF (User Plane Function) - 3GPP TS 23.501 §6.2.3

**Purpose**: Packet forwarding, QoS enforcement, traffic routing

**Interfaces**:

- **N3**: GTP-U tunnel to/from gNB (UDP/GTP-U)
- **N4**: PFCP from SMF for session rules (UDP/PFCP)
- **N6**: Data Network connectivity (IP routing to Internet/MEC)
- **N9**: Inter-UPF tunnel (for mobility, not used in static setup)
- Metrics: Prometheus endpoint on port 9090

**Two Instances**:

1. **UPF-Cloud** (worker node): fixed N3 and N4 addresses on `n3-static` and
   `n4-static`, N6 on `n6c-net` (internet, NATed) and a fixed N6m address on
   `5g/n6m-static` (MEC data network, routed; both DNNs reach it, see
   docs/architecture/5g-interfaces.md#data-networks).

2. **UPF-Edge** (edge node, disabled, see docs/known-issues/upf-edge-cni-route-conflict.md):
   fixed N3 and N4 addresses, N6 on `n6e-net` (DNN `mec`).

The addresses are in the 5G network plan in `ansible/group_vars/all.yml`,
explained in docs/architecture/5g-interfaces.md.

**Packet Processing Pipeline**:

```
1. Uplink (UE → DN):
   gNB → [N3 GTP-U] → UPF → [N6 IP] → Data Network

2. Downlink (DN → UE):
   Data Network → [N6 IP] → UPF → [N3 GTP-U] → gNB → UE
```

**PFCP Rules** (configured by SMF via N4):

- **PDR** (Packet Detection Rule): Match criteria (UE IP, tunnel ID, QFI)
- **FAR** (Forwarding Action Rule): Forward, drop, buffer, notify
- **QER** (QoS Enforcement Rule): Rate limiting, marking (DSCP)
- **URR** (Usage Reporting Rule): Charging triggers (not used in testbed)

**Deployment Considerations**:

- Privileged container (TUN/TAP interfaces for `ogstun`)
- Secondary interfaces (N3, N4, N6) via Multus
- Edge placement for MEC low-latency traffic

**Open5GS Implementation Notes**:

- GTP-U: Uses `ogstun` TUN interface for N6 (IP routing)
- TEID management: Allocated by SMF during session establishment
- Buffering: Downlink packets buffered during UE idle mode (DDN to AMF)
- Linux routing: iptables SNAT for N6, routes via `ip route` for PDU sessions

---

## Protocol Details

### SBI (Service-Based Interface) - 3GPP TS 29.500

All control-plane NFs communicate via HTTP/2 REST APIs:

**Common Headers**:

```http
POST /namf-comm/v1/ue-contexts/{ueContextId}/n1-n2-messages HTTP/2
Content-Type: application/json
3gpp-Sbi-Target-apiRoot: http://smf:7777
```

**Service Operations** (examples):

- AMF: `Namf_Communication`, `Namf_EventExposure`
- SMF: `Nsmf_PDUSession`
- NRF: `Nnrf_NFManagement`, `Nnrf_NFDiscovery`

**SBI Operation Matrix** (partial):
| NF | Services Provided | Services Consumed |
| ---- | ------------------------------------------ | ---------------------------------- |
| NRF | Nnrf_NFManagement, Nnrf_NFDiscovery | - |
| AMF | Namf_Communication, Namf_EventExposure | NRF, AUSF, UDM, SMF, PCF |
| SMF | Nsmf_PDUSession, Nsmf_EventExposure | NRF, UDM, PCF, UPF (PFCP not SBI) |
| UDM | Nudm_UECM, Nudm_UEAU, Nudm_SDM | NRF, UDR |
| PCF | Npcf_SMPolicyControl, Npcf_AMPolicyControl | NRF, UDR, BSF |

---

### N2 (NGAP) - 3GPP TS 38.413

**Transport**: SCTP (port 38412)

**Key Procedures**:

- `NGSetup`: gNB → AMF initial registration
- `InitialUEMessage`: First UE NAS message (Registration Request)
- `HandoverRequired / HandoverRequest`: Mobility procedures
- `Paging`: AMF → gNB UE wake-up

**NGAP Message Structure** (ASN.1):

- Encoded using APER (Aligned Packed Encoding Rules)
- Key IEs: UE-NGAP-IDs, RAN-UE-NGAP-ID, AMF-UE-NGAP-ID, NAS-PDU
- Open5GS uses libngap for encoding/decoding

---

### N3 (GTP-U) - 3GPP TS 29.281

**Transport**: UDP (port 2152)

**Header Format**:

```
0                   1                   2                   3
0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
| Ver |P|R|E|S|PN| Message Type|          Length               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                Tunnel Endpoint Identifier (TEID)              |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                    Sequence Number (optional)                 |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

**TEID Allocation**:

- Uplink TEID: Allocated by UPF, sent to SMF in PFCP Session Establishment Response
- Downlink TEID: Allocated by gNB, sent to SMF in N2 PDUSessionResourceSetupRequest
- TEID space: 32-bit identifier, unique per tunnel endpoint
- Open5GS: Sequential allocation starting from 1

---

### N4 (PFCP) - 3GPP TS 29.244

**Transport**: UDP (port 8805)

**Key Messages**:

- `PFCP Association Setup`: SMF ↔ UPF initial handshake
- `PFCP Session Establishment`: Create PDR/FAR/QER rules
- `PFCP Session Modification`: Update rules (QoS change, handover)
- `PFCP Session Deletion`: Cleanup

**PFCP IEs** (key ones):

- **PDR**: Packet Detection Rule (IE Type 1)
  - PDI (Packet Detection Information): Source Interface, UE IP, F-TEID
  - FAR ID, URR ID, QER ID (references)
- **FAR**: Forwarding Action Rule (IE Type 3)
  - Apply Action: FORW/DROP/BUFF/NOCP/DUPL
  - Forwarding Parameters: Destination Interface, Outer Header Creation
- **QER**: QoS Enforcement Rule (IE Type 7)
  - Gate Status, MBR, GBR, Packet Rate
- Open5GS: libpfcp for encoding/decoding

---

## Implementation Gaps & Future Work

### Current Limitations

1. **No Network Slicing**: Single slice (SST=1, SD=000001) configured
2. **No Charging**: CHF (Charging Function) not deployed
3. **No QoS Differentiation**: All flows use default QoS (QCI 9)
4. **No Roaming**: SEPP (Security Edge Protection Proxy) not present
5. **No Voice**: IMS/VoNR not configured

### Potential Enhancements

- [ ] Deploy multiple UPF instances (N9 inter-UPF)
- [ ] Configure network slicing (eMBB, URLLC, mMTC)
- [ ] Implement QoS flows with 5QI/GBR
- [ ] Add CHF for charging data records (CDR)
- [ ] Integrate with external DNS (N6 Internet breakout)

---

## References

### 3GPP Specifications

- **TS 23.501**: System architecture for the 5G System (5GS)
- **TS 23.502**: Procedures for the 5G System (5GS)
- **TS 29.500**: Technical Realization of Service Based Architecture (SBA)
- **TS 29.518**: Access and Mobility Management Services
- **TS 29.502**: Session Management Services
- **TS 29.510**: Network Repository Function Services
- **TS 29.244**: PFCP (Packet Forwarding Control Protocol)
- **TS 29.281**: GTP-U (GPRS Tunneling Protocol User Plane)
- **TS 38.413**: NG-RAN; NG Application Protocol (NGAP)

### Open5GS Documentation

- Official Docs: https://open5gs.org/open5gs/docs/
- GitHub: https://github.com/open5gs/open5gs

---

**Last Updated**: 2025-10-19  
**Status**: COMPLETE - All NF sections documented  
**Maintainer**: Phase 5 refactoring team
