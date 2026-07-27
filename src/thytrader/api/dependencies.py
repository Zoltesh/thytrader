"""Typed FastAPI dependencies."""

# FastAPI resolves this dependency annotation at runtime.
from fastapi import Request  # noqa: TC002

from thytrader.runtime import RuntimeState


def get_runtime_state(request: Request) -> RuntimeState:
    """Return the validated ThyTrader runtime attached to the application."""
    runtime = getattr(request.app.state, "runtime", None)
    if not isinstance(runtime, RuntimeState):
        message = "ThyTrader runtime state is unavailable."
        raise TypeError(message)
    return runtime
