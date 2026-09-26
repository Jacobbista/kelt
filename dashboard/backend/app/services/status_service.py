"""One answer to "does anything need attention?", for the header's status pill.

Reads what other pages already compute: node readiness, the 5G pods, the last
network check run (never starts one: the checks exec into pods) and the AMF CNI
alert. A source that cannot be read is a warning, never a failure of the answer.
"""
from typing import Any, Callable

RANK = {"ok": 0, "warn": 1, "error": 2}
# Container waiting reasons that do not clear on their own. A crashlooping
# container keeps the pod phase Running, so the phase alone would miss them.
STUCK = {"CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "InvalidImageName",
         "CreateContainerConfigError", "CreateContainerError", "RunContainerError"}


class StatusService:
    def __init__(self, nodes: Callable, pods: Callable, checks: Callable, amf_alert: Callable) -> None:
        self._sources = [("Nodes", nodes, self._nodes), ("5G core", pods, self._pods),
                         ("Network", checks, self._checks), ("5G core", amf_alert, self._alert)]

    @staticmethod
    def _nodes(nodes) -> list[tuple[str, str]]:
        return [(f"{n.name} is not Ready", "error") for n in nodes if n.status != "Ready"]

    @staticmethod
    def _pods(pods) -> list[tuple[str, str]]:
        out = []
        for p in pods:
            if p.waiting_reason in STUCK:
                out.append((f"{p.name} is {p.waiting_reason}", "error"))
            elif p.phase == "Failed":
                out.append((f"{p.name} is Failed", "error"))
            elif p.phase == "Pending":
                out.append((f"{p.name} is Pending", "warn"))
            elif p.phase == "Running" and not p.ready:
                out.append((f"{p.name} is not ready", "warn"))
        return out

    @staticmethod
    def _checks(checks) -> list[tuple[str, str]]:
        out = []
        for c in checks:
            detail = c.get("detail") or ""
            # The result is the last run Health made: say how old it is.
            when = f" {round(c['age_s'] / 60)} min ago" if c.get("age_s") is not None else ""
            if c.get("status") in ("fail", "error"):
                out.append((f"{c['interface']} check failed{when}" + (f": {detail}" if detail else ""), "error"))
            elif c.get("status") == "warn":
                out.append((f"{c['interface']} check{when}: " + (detail or "warning"), "warn"))
        return out

    @staticmethod
    def _alert(alert) -> list[tuple[str, str]]:
        # A stuck AMF pod is already reported by the pods source.
        reasons = [r for r in alert.get("reasons") or [] if r != "stuck_amf_pods"]
        if not alert.get("active") or not reasons:
            return []
        return [("AMF networking alert: " + ", ".join(reasons), "warn")]

    def summary(self) -> dict[str, Any]:
        problems = []
        for area, read, judge in self._sources:
            try:
                found = judge(read())
            except Exception as exc:
                found = [(f"Cannot read: {exc}", "warn")]
            problems += [{"area": area, "text": text, "severity": sev} for text, sev in found]
        state = max((p["severity"] for p in problems), key=RANK.get, default="ok")
        return {"state": state, "problems": problems}
