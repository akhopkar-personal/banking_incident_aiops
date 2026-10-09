"""get_service_dependencies (Architecture Spec Section 7.3; Req. Section 9.3):
topology and blast radius from data/service_dependencies.json."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from src import config
from src.errors import InvalidToolArguments
from src.schemas.evidence import DependencyInfo


def load_dependency_map() -> dict[str, dict]:
    path = config.get_settings().reference_file("service_dependencies.json")
    return _load(str(path), path.stat().st_mtime)


@lru_cache(maxsize=4)
def _load(path: str, _mtime: float) -> dict[str, dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _closure(start: str, edges: dict[str, list[str]]) -> list[str]:
    seen, stack = [], list(edges.get(start, []))
    while stack:
        node = stack.pop(0)
        if node not in seen and node != start:
            seen.append(node)
            stack.extend(edges.get(node, []))
    return seen


def upstream_of(service: str, deps: dict[str, dict] | None = None) -> list[str]:
    """Services `service` depends on, directly or transitively."""
    deps = deps or load_dependency_map()
    return _closure(service, {s: info["depends_on"] for s, info in deps.items()})


def downstream_of(service: str, deps: dict[str, dict] | None = None) -> list[str]:
    """Services that depend on `service`, directly or transitively."""
    deps = deps or load_dependency_map()
    reverse: dict[str, list[str]] = {s: [] for s in deps}
    for svc, info in deps.items():
        for target in info["depends_on"]:
            reverse.setdefault(target, []).append(svc)
    return _closure(service, reverse)


def get_service_dependencies(service: str, direction: Literal["upstream", "downstream", "both"] = "both"
                             ) -> DependencyInfo:
    deps = load_dependency_map()
    if service not in deps:
        raise InvalidToolArguments(f"unknown service {service!r}")
    up = upstream_of(service, deps) if direction in ("upstream", "both") else []
    down = downstream_of(service, deps)
    blast = [s for s in [service, *down] if deps.get(s, {}).get("customer_facing")]
    return DependencyInfo(service=service, direction=direction, owner_team=deps[service]["owner_team"],
                          customer_facing=deps[service]["customer_facing"], upstream=up,
                          downstream=down if direction in ("downstream", "both") else [],
                          blast_radius_customer_facing=blast)
