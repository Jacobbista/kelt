import time

from fastapi import APIRouter, Depends, Query

from app.services.prometheus_service import PrometheusService, get_prometheus_service

router = APIRouter(prefix="/api/v1/metrics", tags=["metrics"])


@router.get("/nodes")
async def node_resources(
    prom: PrometheusService = Depends(get_prometheus_service),
) -> dict:
    cpu = await prom.node_cpu()
    mem = await prom.node_memory()
    disk = await prom.node_disk()
    return {"cpu": cpu, "memory": mem, "disk": disk}


@router.get("/nf")
async def nf_resources(
    prom: PrometheusService = Depends(get_prometheus_service),
) -> dict:
    cpu = await prom.nf_cpu()
    mem = await prom.nf_memory()
    restarts = await prom.nf_restarts()
    return {"cpu": cpu, "memory": mem, "restarts": restarts}


@router.get("/overview")
async def metrics_overview(
    prom: PrometheusService = Depends(get_prometheus_service),
) -> dict:
    cpu = await prom.node_cpu()
    mem = await prom.node_memory()
    avg_cpu = round(sum(c["value"] for c in cpu) / max(len(cpu), 1), 1)
    avg_mem = round(sum(m["value"] for m in mem) / max(len(mem), 1), 1)
    return {"avg_cpu_pct": avg_cpu, "avg_mem_pct": avg_mem, "node_count": len(cpu)}


@router.get("/range/nodes")
async def node_range(
    minutes: int = Query(default=30, ge=5, le=1440),
    step: str = Query(default="60s"),
    prom: PrometheusService = Depends(get_prometheus_service),
) -> dict:
    now = int(time.time())
    start = str(now - minutes * 60)
    end = str(now)
    cpu = await prom.cpu_range(start, end, step)
    mem = await prom.memory_range(start, end, step)
    return {"cpu": cpu, "memory": mem}


@router.get("/range/nf")
async def nf_range(
    minutes: int = Query(default=30, ge=5, le=1440),
    step: str = Query(default="60s"),
    prom: PrometheusService = Depends(get_prometheus_service),
) -> dict:
    now = int(time.time())
    start = str(now - minutes * 60)
    end = str(now)
    cpu = await prom.nf_cpu_range(start, end, step)
    return {"cpu": cpu}


@router.get("/query")
async def raw_query(
    q: str = Query(..., alias="query"),
    prom: PrometheusService = Depends(get_prometheus_service),
) -> dict:
    data = await prom.instant_query(q)
    return {"data": data}
