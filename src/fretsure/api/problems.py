"""Stable RFC 9457-style problem responses for the HTTP adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from fastapi.responses import JSONResponse

from fretsure.application import (
    ApplicationCode,
    ApplicationError,
    application_error_to_wire,
)

API_VERSION = "fretsure-api@0.2.0"
PROBLEM_MEDIA_TYPE = "application/problem+json"


class APIProblem(Exception):
    """One safe transport failure with no embedded raw exception text."""

    def __init__(
        self,
        *,
        status: int,
        code: str,
        title: str,
        detail: str,
        diagnostics: tuple[Mapping[str, str], ...] = (),
    ) -> None:
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.diagnostics = diagnostics
        super().__init__(code)


def problem_response(problem: APIProblem) -> JSONResponse:
    body: dict[str, object] = {
        "type": "about:blank",
        "api_version": API_VERSION,
        "status": problem.status,
        "code": problem.code,
        "title": problem.title,
        "detail": problem.detail,
    }
    if problem.diagnostics:
        body["diagnostics"] = [dict(diagnostic) for diagnostic in problem.diagnostics]
    return JSONResponse(body, status_code=problem.status, media_type=PROBLEM_MEDIA_TYPE)


_SEMANTIC_CODES = frozenset(
    {
        ApplicationCode.INVALID_ARGUMENT.value,
        ApplicationCode.INVALID_OPTIONS.value,
        ApplicationCode.UNKNOWN_PROFILE.value,
        ApplicationCode.IMPORT_REJECTED.value,
        ApplicationCode.TAB_INPUT_REJECTED.value,
        ApplicationCode.TARGET_INPUT_REJECTED.value,
        ApplicationCode.SOLVER_INPUT_REJECTED.value,
        ApplicationCode.UNSUPPORTED_RENDER_FORMAT.value,
    }
)


def application_problem(error: ApplicationError) -> APIProblem:
    """Map an application failure without copying exception rendering."""

    safe = application_error_to_wire(error)
    code = cast(str, safe["code"])
    raw_diagnostics = cast(list[dict[str, str]], safe["diagnostics"])
    diagnostics = tuple(raw_diagnostics)
    if code == ApplicationCode.DEPENDENCY_UNAVAILABLE.value:
        status = 503
        title = "Runtime dependency unavailable"
        detail = "a required optional runtime dependency is unavailable"
    elif code in _SEMANTIC_CODES:
        status = 422
        title = "Request semantics rejected"
        detail = cast(str, safe["detail"])
    elif code == ApplicationCode.LLM_CONFIGURATION_REJECTED.value:
        status = 503
        title = "Arrangement engine unavailable"
        detail = "the selected arrangement engine is unavailable"
    elif code == ApplicationCode.ARRANGEMENT_FAILED.value:
        status = 502
        title = "Arrangement failed"
        detail = "the arrangement engine could not complete the request"
    else:
        status = 500
        title = "Operation failed"
        detail = "the requested operation could not be completed"
    return APIProblem(
        status=status,
        code=code,
        title=title,
        detail=detail,
        diagnostics=diagnostics,
    )


def not_found_problem() -> APIProblem:
    return APIProblem(
        status=404,
        code="NOT_FOUND",
        title="Not found",
        detail="the requested resource does not exist",
    )


__all__ = [
    "API_VERSION",
    "PROBLEM_MEDIA_TYPE",
    "APIProblem",
    "application_problem",
    "not_found_problem",
    "problem_response",
]
