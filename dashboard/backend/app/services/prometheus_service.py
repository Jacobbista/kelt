import logging
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger(__name__)

QUERY_TIMEOUT = 8.0


class PrometheusService:
    def __init__(self) -> None:
        self.base = settings.prometheus_url.rstrip("/")

    async def instant_query(self, query: str) -> Any:
        async with httpx.AsyncClient(timeout=QUERY_TIMEOUT) as c:
            resp = await c.get(f"{self.base}/api/v1/query", params={"query": query})
            resp.raise_for_status()
            return resp.json().get("data", {})

    async def range_query(self, query: str, start: str, end: str, step: str = "60s") -> Any:
        async with httpx.AsyncClient(timeout=QUERY_TIMEOUT) as c:
            resp = await c.get(
                f"{self.base}/api/v1/query_range",
                params={"query": query, "start": start, "end": end, "step": step},
            )
            resp.raise_for_status()
            return resp.json().get("data", {})

    async def node_cpu(self) -> list[dict]:
        data = await self.instant_query(
            '100 - (avg by(instance) (irate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)'
        )
        return self._extract_vector(data)

    async def node_memory(self) -> list[dict]:
        data = await self.instant_query(
            "(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100"
        )
        return self._extract_vector(data)

    async def node_disk(self) -> list[dict]:
        data = await self.instant_query(
            '(1 - node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}) * 100'
        )
        return self._extract_vector(data)

    async def nf_cpu(self) -> list[dict]:
        data = await self.instant_query(
            'sum by(pod) (rate(container_cpu_usage_seconds_total{namespace="5g", container!=""}[5m])) * 1000'
        )
        return self._extract_vector(data)

    async def nf_memory(self) -> list[dict]:
        data = await self.instant_query(
            'sum by(pod) (container_memory_usage_bytes{namespace="5g", container!=""}) / 1024 / 1024'
        )
        return self._extract_vector(data)

    async def nf_restarts(self) -> list[dict]:
        data = await self.instant_query(
            'sum by(pod) (kube_pod_container_status_restarts_total{namespace="5g"})'
        )
        return self._extract_vector(data)

    async def cpu_range(self, start: str, end: str, step: str = "60s") -> Any:
        return await self.range_query(
            '100 - (avg by(instance) (irate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)',
            start, end, step,
        )

    async def memory_range(self, start: str, end: str, step: str = "60s") -> Any:
        return await self.range_query(
            "(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100",
            start, end, step,
        )

    async def nf_cpu_range(self, start: str, end: str, step: str = "60s") -> Any:
        return await self.range_query(
            'sum by(pod) (rate(container_cpu_usage_seconds_total{namespace="5g", container!=""}[5m])) * 1000',
            start, end, step,
        )

    @staticmethod
    def _extract_vector(data: dict) -> list[dict]:
        results: list[dict] = []
        for item in data.get("result", []):
            metric = item.get("metric", {})
            value = item.get("value", [None, None])
            label = metric.get("pod") or metric.get("instance") or str(metric)
            try:
                num = float(value[1])
            except (TypeError, ValueError, IndexError):
                num = 0.0
            results.append({"label": label, "value": round(num, 2), "metric": metric})
        return results


def get_prometheus_service() -> PrometheusService:
    return PrometheusService()
