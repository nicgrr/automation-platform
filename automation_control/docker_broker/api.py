import os

from fastapi import FastAPI, HTTPException, Query

from .gateway import DockerCliGateway, DockerGateway

app = FastAPI(title="Read-only Docker Broker", version="0.1.0")
gateway: DockerGateway = DockerCliGateway()


def allowed_names() -> frozenset[str]:
    return frozenset(name.strip() for name in os.getenv("ALLOWED_CONTAINERS", "").split(",") if name.strip())


def require_allowed(name: str) -> None:
    if name not in allowed_names():
        raise HTTPException(status_code=404, detail="container not allowed")


@app.get("/containers")
def containers() -> dict:
    return {"containers": sorted(allowed_names())}


@app.get("/containers/{name}/status")
def status(name: str) -> dict:
    require_allowed(name)
    state = gateway.status(name)
    return {"name": name, "status": state.get("Status"), "running": state.get("Running", False)}


@app.get("/containers/{name}/health")
def health(name: str) -> dict:
    require_allowed(name)
    state = gateway.status(name)
    health_state = state.get("Health", {}).get("Status", "none")
    return {"name": name, "health": health_state}


@app.get("/containers/{name}/logs")
def logs(name: str, tail: int = Query(100, ge=1, le=500)) -> dict:
    require_allowed(name)
    return {"name": name, "tail": tail, "logs": gateway.logs(name, tail)}

