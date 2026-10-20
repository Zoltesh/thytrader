"""Parse one model answer into a :class:`DeskDecision` (ADR 0147).

The model is asked for exactly one JSON object. A fenced ``json`` block or a bare object is
accepted; anything else, or an object that fails validation, is an invalid wake with the reason.
Nothing is guessed or repaired.
"""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from thytrader.agent_desk.models import DeskDecision

_FENCED = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class DecisionParseError(ValueError):
    """The answer held no single valid decision object."""


def parse_decision(text: str) -> DeskDecision:
    """Return the decision in ``text`` or raise :class:`DecisionParseError`."""
    candidate = _json_text(text)
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as error:
        raise DecisionParseError(f"The answer's JSON does not parse: {error.msg}.") from None
    if not isinstance(payload, dict):
        raise DecisionParseError("The answer must be one JSON object.")
    try:
        return DeskDecision.model_validate(payload)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'decision'}: {item['msg']}"
            for item in error.errors()[:5]
        )
        raise DecisionParseError(f"The decision is invalid: {problems}.") from None


def _json_text(text: str) -> str:
    """The fenced JSON block, else the outermost braces of the answer."""
    fenced = _FENCED.findall(text)
    if len(fenced) == 1:
        return fenced[0]
    if len(fenced) > 1:
        raise DecisionParseError("The answer holds more than one JSON block; send exactly one.")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise DecisionParseError("The answer holds no JSON object.")
    return text[start : end + 1]
