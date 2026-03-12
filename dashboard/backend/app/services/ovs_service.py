import json
import subprocess

from fastapi import HTTPException, status

from app.config import settings


class OvsService:
    def __init__(self) -> None:
        self.allowed_prefixes = [
            ["sudo", "ovs-vsctl", "list-br"],
            ["sudo", "ovs-vsctl", "list-ports"],
            ["sudo", "ovs-ofctl", "dump-flows"],
        ]

    def list_bridges(self) -> list[str]:
        out = self._run_remote(["sudo", "ovs-vsctl", "list-br"])
        return [line.strip() for line in out.splitlines() if line.strip()]

    def list_bridge_ports(self, bridge: str) -> list[str]:
        out = self._run_remote(["sudo", "ovs-vsctl", "list-ports", bridge])
        return [line.strip() for line in out.splitlines() if line.strip()]

    def dump_flows(self, bridge: str) -> str:
        return self._run_remote(["sudo", "ovs-ofctl", "dump-flows", bridge])

    def _run_remote(self, command: list[str]) -> str:
        if not self._is_allowed(command):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Command is not allowed by policy",
            )

        wrapped = [
            "ssh",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "LogLevel=ERROR",
            "-o", "BatchMode=yes",
            settings.worker_ssh_host,
            " ".join(command),
        ]
        try:
            proc = subprocess.run(
                wrapped,
                capture_output=True,
                text=True,
                timeout=settings.shell_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(status_code=504, detail="Command timeout") from exc

        if proc.returncode != 0:
            err = ((proc.stdout or "") + (proc.stderr or "")).strip()
            if len(err) > settings.shell_max_output_bytes:
                err = err[: settings.shell_max_output_bytes]
            raise HTTPException(
                status_code=500,
                detail=f"OVS command failed ({proc.returncode}): {err}",
            )

        return proc.stdout or ""

    def _is_allowed(self, command: list[str]) -> bool:
        for prefix in self.allowed_prefixes:
            if command[: len(prefix)] == prefix:
                return True
        return False
