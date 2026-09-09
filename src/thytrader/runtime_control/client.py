"""HTTP helpers for confirmation-gated paper and live runtime control."""

from __future__ import annotations

from thytrader.agent_http import request_json

_DEPLOYMENTS_PREFIX = "/api/v1/deployments"


class RuntimeControlError(RuntimeError):
    """Report a redacted runtime-control failure."""


def list_deployments(base_url: str) -> object:
    """Return the newest-first deployment list from the API."""
    return request_json(method="GET", url=f"{base_url}{_DEPLOYMENTS_PREFIX}")


def show_deployment(base_url: str, deployment_id: str) -> object:
    """Return one deployment snapshot including orders and fills."""
    return request_json(method="GET", url=f"{base_url}{_DEPLOYMENTS_PREFIX}/{deployment_id}")


def start_deployment(
    base_url: str,
    *,
    strategy_fingerprint: str,
    mode: str,
    paper_starting_cash: str | None,
) -> object:
    """Create one paper or live deployment through the existing HTTP contract."""
    payload: dict[str, str] = {
        "strategy_fingerprint": strategy_fingerprint,
        "mode": mode,
    }
    if paper_starting_cash is not None:
        payload["paper_starting_cash"] = paper_starting_cash
    return request_json(method="POST", url=f"{base_url}{_DEPLOYMENTS_PREFIX}", payload=payload)


def set_deployment_status(base_url: str, deployment_id: str, action: str) -> object:
    """Pause, resume, or stop one deployment."""
    if action not in {"pause", "resume", "stop"}:
        raise RuntimeControlError(f"unsupported runtime action: {action}")
    return request_json(
        method="POST",
        url=f"{base_url}{_DEPLOYMENTS_PREFIX}/{deployment_id}/{action}",
    )
