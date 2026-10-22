"""Contract schemas, validation with fixed-phrase details, and language codes.

Validation details never contain request values: each one is a JSON path made
of schema property names followed by a fixed phrase chosen by the failed
JSON Schema keyword.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

SchemaName = Literal["request", "response", "result"]

# Copied from faster-whisper 1.2.1 (faster_whisper.tokenizer._LANGUAGE_CODES);
# importing that module would load torch. A contract test keeps the two equal.
LANGUAGE_CODES: frozenset[str] = frozenset(
    "af am ar as az ba be bg bn bo br bs ca cs cy da de el en es et eu fa fi fo fr gl gu "
    "ha haw he hi hr ht hu hy id is it ja jw ka kk km kn ko la lb ln lo lt lv mg mi mk ml "
    "mn mr ms mt my ne nl nn no oc pa pl ps pt ro ru sa sd si sk sl sn so sq sr su sv sw "
    "ta te tg th tk tl tr tt uk ur uz vi yi yo yue zh".split()
)

_ROOT_LABEL = "request"
_GENERIC_PHRASE = "value violates the schema"
_PHRASES: dict[str, str] = {
    "required": "field is required",
    "pattern": "value has an invalid format",
    "uniqueItems": "items must be unique",
    "enum": "value is not allowed",
    "const": "unsupported value",
    "additionalProperties": "unknown field is not allowed",
    "minItems": "too few items",
    "maxItems": "too many items",
    "minLength": "value is too short",
    "maxLength": "value is too long",
    "type": "value has the wrong type",
    "oneOf": "must match exactly one alternative",
}
_REQUEST_BRANCHES = {"transcribe": "#/$defs/transcribe", "delete": "#/$defs/delete"}


def language_supported(code: str) -> bool:
    """Returns whether the transcription engine knows the Whisper language code."""
    return code in LANGUAGE_CODES


def load_schema(name: SchemaName) -> dict[str, Any]:
    """Loads one packaged contract schema.

    Args:
        name: ``request``, ``response`` or ``result``.

    Returns:
        The parsed JSON Schema document.
    """
    resource = resources.files("stt.server") / "schemas" / f"{name}.schema.json"
    return json.loads(resource.read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def _validator(name: SchemaName, branch_ref: str | None) -> Draft202012Validator:
    schema = load_schema(name)
    if branch_ref is not None:
        schema = {"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": branch_ref}
    return Draft202012Validator(schema)


def _describe(error: ValidationError) -> str:
    path = [part for part in error.absolute_path if isinstance(part, str)]
    if error.validator == "required":
        missing = [key for key in error.validator_value if key not in error.instance]
        path.append(missing[0])
    label = ".".join(path) if path else _ROOT_LABEL
    return f"{label}: {_PHRASES.get(str(error.validator), _GENERIC_PHRASE)}"


def _first_error(validator: Draft202012Validator, doc: object) -> str | None:
    error = next(iter(validator.iter_errors(doc)), None)
    return None if error is None else _describe(error)


def validate_request(doc: object) -> str | None:
    """Validates a request document against the branch its ``kind`` selects.

    Args:
        doc: The parsed request JSON.

    Returns:
        None when valid, otherwise ``<JSON path>: <fixed phrase>`` for the first
        violation. A missing or unknown ``kind`` is reported at ``kind``.
    """
    if not isinstance(doc, dict):
        return f"{_ROOT_LABEL}: {_PHRASES['type']}"
    kind = doc.get("kind")
    if "kind" not in doc:
        return f"kind: {_PHRASES['required']}"
    if not isinstance(kind, str) or kind not in _REQUEST_BRANCHES:
        return f"kind: {_PHRASES['enum']}"
    return _first_error(_validator("request", _REQUEST_BRANCHES[kind]), doc)


def validate_response(doc: object) -> str | None:
    """Validates a response document; returns None or the first violation detail."""
    return _first_error(_validator("response", None), doc)


def validate_result(doc: object) -> str | None:
    """Validates a result document; returns None or the first violation detail."""
    return _first_error(_validator("result", None), doc)
