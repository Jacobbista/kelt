"""The RAN page's chain: five links from the cable to the UEs, in order.

Pure: takes the facts the RAN service read and says, per link, whether it
holds, what to show, and what fixes it. After the first broken link the rest
cannot be read, so they are reported as blocked, not as failed.
"""
from typing import Any

LINKS = [
    ("cable", "Cable and link"),
    ("bridge", "RAN bridge"),
    ("core", "Core connection"),
    ("user_plane", "User plane"),
    ("ues", "UEs"),
]

NO_CARRIER_CHECKLIST = [
    "The cable from the host adapter to the switch is in",
    "The switch port and the gNB's port have their lights on",
    "The gNB is powered",
]


def _link(i: int, state: str, verdict: str, value: str = "",
          facts: list | None = None, fix: dict | None = None) -> dict[str, Any]:
    lid, name = LINKS[i]
    return {"id": lid, "name": name, "state": state, "verdict": verdict,
            "value": value, "facts": facts or [], "fix": fix}


def _cable(f: dict) -> dict:
    iface, state = f["iface"] or "(none)", f["nic_state"]
    if state == "up":
        return _link(0, "ok", "Link up, carrier on the wire", iface, [
            ["Host adapter", f"{f['host_nic']}, bridged into the worker" if f.get("host_nic") else "not recorded"],
            ["Worker interface", f"{iface} · UP, LOWER_UP"],
            ["Another adapter", "kelt ran <adapter>, then kelt provision on the host (restarts the worker)"],
        ])
    if state == "down":
        return _link(0, "bad", "The link is down", f"{iface} · down",
                     [["Worker interface", f"{iface} · DOWN"], ["Port of", "br-ran"]],
                     {"piece": "ran_link"})
    if state == "no_carrier":
        return _link(0, "bad", "No carrier on the wire", f"{iface} · no carrier",
                     [["Worker interface", f"{iface} · UP, NO-CARRIER"]],
                     {"checklist": NO_CARRIER_CHECKLIST})
    return _link(0, "bad", "The worker has no RAN interface", f"{iface} · not present",
                 [], {"cli": f"kelt ran {f.get('host_nic') or '<adapter>'}"})


def _bridge(f: dict) -> dict:
    ports = f.get("bridge_ports") or []
    if f["nic_on_bridge"]:
        n = len(ports)
        return _link(1, "ok", f"{f['iface']} is a port of br-ran", f"br-ran · {n} port{'s' if n != 1 else ''}",
                     [["Bridge", "br-ran (Open vSwitch, worker)"], ["Ports", ", ".join(ports)]])
    # No br-ran at all: the attach piece builds it with the adapter. A br-ran
    # without the adapter is not something a piece can repair (they find the
    # adapter through br-ran's own ports): restarting the worker runs the setup.
    if not f.get("bridge_exists", True):
        return _link(1, "bad", "br-ran is missing", "br-ran · absent",
                     [["Bridge", "br-ran (Open vSwitch, worker): not present"]], {"piece": "ran_attach"})
    return _link(1, "bad", f"{f['iface']} is not on br-ran", "br-ran",
                 [["Ports", ", ".join(ports) or "none"]], {"cli": f"kelt ran {f.get('host_nic') or '<adapter>'}"})


def _core(f: dict) -> dict:
    # The NGAP port comes from the running AMF; unknown while it is not running.
    listen = f"{f['amf_ip']}:{f['ngap_port']}" if f.get("ngap_port") else f["amf_ip"]
    if not f["amf_attached"]:
        return _link(2, "bad", "The core is not attached to the RAN", "no AMF on br-ran",
                     [["AMF address", f"{f['amf_ip']} (not on br-ran)"]], {"piece": "ran_attach"})
    if not f["gnb_connected"]:
        return _link(2, "bad", "No NG Setup from the gNB", listen,
                     [["AMF listens on", f"{listen} (SCTP)" if f.get("ngap_port") else f"{listen} (AMF not running)"]], {"see": "setup"})
    return _link(2, "ok", "NG Setup accepted", f"{f['amf_ip']} ↔ {f['gnb_ip']}", [
        ["AMF on br-ran", f"{f['amf_ip']} ({f.get('amf_iface', 'N2 RAN interface')})"],
        ["gNB", f"{f['gnb_ip']} · setup accepted"],
        ["Association", f"SCTP, port {f['ngap_port']}" if f.get("ngap_port") else "SCTP"],
    ])


def _user_plane(f: dict) -> dict:
    if f["upf_route"]:
        return _link(3, "ok", "The UPF has its route back to the RAN", f["subnet"],
                     [["UPF route", f"{f['subnet']} over N3"], ["N3 subnet", f["n3_subnet"]]])
    return _link(3, "bad", "The UPF has no route back to the RAN", f["subnet"],
                 [["UPF route", f"{f['subnet']}: missing"]], {"piece": "ran_attach"})


def _ues(f: dict) -> dict:
    if f["ues"] > 0:
        return _link(4, "ok", f"{f['ues']} registered, {f['pdu']} PDU session{'s' if f['pdu'] != 1 else ''}")
    return _link(4, "idle", "No UE registered yet")


def _detached(facts: dict[str, Any]) -> dict[str, Any]:
    """Detached on purpose (PHYSICAL_RAN_ENABLED=false): nothing past the cable
    is expected to work; the one action is Attach."""
    cable = _cable(facts)
    # The intent says detached, but what runs may not yet (a detach running or
    # one that stopped half way): say so and offer Detach again, not Attach.
    left = [text for present, text in (
        (facts.get("bridge_exists", False), "br-ran is still there"),
        (facts.get("amf_attached", False), "the AMF is still on br-ran"),
    ) if present]
    if left:
        links = [cable, _link(1, "bad", "Detach not finished: " + "; ".join(left), "br-ran",
                              [["Set by", "PHYSICAL_RAN_ENABLED=false (Detach)"]], {"piece": "ran_detach"})]
        links += [_link(i, "blocked", "Not checked until ran bridge works") for i in range(2, len(LINKS))]
        return {"state": "broken", "first_broken": "bridge", "links": links}
    links = [cable, _link(1, "idle", "Detached: the core is not connected to the RAN", "br-ran · removed",
                          [["Set by", "PHYSICAL_RAN_ENABLED=false (Detach)"]], {"piece": "ran_attach"})]
    links += [_link(i, "idle", "Attach to check") for i in range(2, len(LINKS))]
    if cable["state"] == "bad":
        links[1:] = [_link(i, "blocked", "Not checked until cable and link works") for i in range(1, len(LINKS))]
        return {"state": "broken", "first_broken": "cable", "links": links}
    return {"state": "detached", "first_broken": None, "links": links}


def build_chain(facts: dict[str, Any]) -> dict[str, Any]:
    if not facts.get("intent_attached", True):
        return _detached(facts)
    links = [_cable(facts), _bridge(facts), _core(facts), _user_plane(facts), _ues(facts)]
    first = next((i for i, l in enumerate(links) if l["state"] == "bad"), None)
    if first is not None:
        after = LINKS[first][1].lower()
        for i in range(first + 1, len(links)):
            links[i] = _link(i, "blocked", f"Not checked until {after} works")
    return {
        "state": "serving" if first is None else "broken",
        "first_broken": None if first is None else LINKS[first][0],
        "links": links,
    }


def _amf_settled(pods: list[dict[str, Any]]) -> str | None:
    """Why the AMF cannot be judged yet (None when exactly one pod runs ready
    and none is going away): a read-back must see the new pod, not the old."""
    if any(p["deleting"] for p in pods) or len(pods) != 1:
        return "the AMF is still being replaced"
    if not pods[0]["ready"]:
        return "the AMF is not ready yet"
    return None


# A verdict is (True, why), (False, why), or (None, why) while the AMF is still
# being replaced: not a result yet; the read-back asks again (get_operation).

def attach_verdict(f: dict[str, Any]) -> tuple[bool | None, str]:
    """Read-back of ran_attach, from the running pods and the worker."""
    missing = [text for ok, text in (
        (f["br_exists"], "br-ran is missing"),
        (f["nic_on_bridge"], "the RAN adapter is not on br-ran"),
        (f["nad_exists"], "the RAN network attachment is missing"),
    ) if not ok]
    if missing:
        return False, "; ".join(missing)
    unsettled = _amf_settled(f["amf_pods"])
    if unsettled:
        return None, unsettled
    missing = [text for ok, text in (
        (f["amf_veth"], "the AMF is not on br-ran"),
        (f["amf_pods"][0]["phy"], "the AMF has no RAN interface"),
    ) if not ok]
    if missing:
        return False, "; ".join(missing)
    return True, "br-ran carries the RAN adapter and the AMF; the AMF has its RAN interface"


def detach_verdict(f: dict[str, Any]) -> tuple[bool | None, str]:
    """Read-back of ran_detach, from the running pods and the worker."""
    left = [text for present, text in (
        (f["br_exists"], "br-ran is still there"),
        (f["nad_exists"], "the RAN network attachment is still there"),
    ) if present]
    if left:
        return False, "; ".join(left)
    unsettled = _amf_settled(f["amf_pods"])
    if unsettled:
        return None, unsettled
    if f["amf_pods"][0]["phy"]:
        return False, "the AMF still has its RAN interface"
    return True, "br-ran is gone and the AMF runs without its RAN interface"
