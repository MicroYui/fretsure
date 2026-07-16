"""Strict ASGI request-envelope parsing for the public HTTP adapter."""

from __future__ import annotations

from collections.abc import Collection
from urllib.parse import unquote_to_bytes

from starlette.requests import ClientDisconnect, Request

from fretsure.api.problems import APIProblem

MAX_QUERY_BYTES = 8 * 1024
MAX_HEADER_VALUE_BYTES = 1024


def _problem(status: int, code: str, title: str, detail: str) -> APIProblem:
    return APIProblem(status=status, code=code, title=title, detail=detail)


def _header_values(request: Request, name: str) -> tuple[bytes, ...]:
    raw_headers = request.scope.get("headers", ())
    if type(raw_headers) not in (list, tuple):
        raise _problem(400, "INVALID_HEADERS", "Invalid headers", "header framing is invalid")
    expected = name.encode("ascii")
    values: list[bytes] = []
    for item in raw_headers:
        if type(item) not in (list, tuple) or len(item) != 2:
            raise _problem(
                400,
                "INVALID_HEADERS",
                "Invalid headers",
                "header framing is invalid",
            )
        raw_name, raw_value = item
        if type(raw_name) is not bytes or type(raw_value) is not bytes:
            raise _problem(
                400,
                "INVALID_HEADERS",
                "Invalid headers",
                "header framing is invalid",
            )
        if raw_name.lower() == expected:
            if len(raw_value) > MAX_HEADER_VALUE_BYTES:
                raise _problem(
                    400,
                    "INVALID_HEADER",
                    "Invalid header",
                    "header value exceeds the public limit",
                )
            values.append(raw_value)
    return tuple(values)


def single_header(request: Request, name: str) -> str | None:
    """Return one strict ASCII header value, rejecting duplicate fields."""

    values = _header_values(request, name.lower())
    if len(values) > 1:
        raise _problem(
            400,
            "DUPLICATE_HEADER",
            "Duplicate header",
            f"{name.lower()} must occur at most once",
        )
    if not values:
        return None
    try:
        return values[0].decode("ascii")
    except UnicodeDecodeError:
        raise _problem(
            400,
            "INVALID_HEADER",
            "Invalid header",
            f"{name.lower()} must contain ASCII syntax",
        ) from None


def require_media_type(
    request: Request,
    allowed: Collection[str],
    *,
    allow_utf8_charset: bool,
) -> str:
    """Validate one Content-Type and return its normalized media type."""

    content_type = single_header(request, "content-type")
    if content_type is None:
        raise _problem(
            415,
            "CONTENT_TYPE_REQUIRED",
            "Content type required",
            "request must declare a supported content-type",
        )
    pieces = tuple(piece.strip() for piece in content_type.split(";"))
    media_type = pieces[0].lower()
    if not media_type or media_type not in allowed:
        raise _problem(
            415,
            "UNSUPPORTED_MEDIA_TYPE",
            "Unsupported media type",
            "content-type is not supported for this endpoint and filename",
        )
    parameters = tuple(piece.lower() for piece in pieces[1:])
    allowed_parameters = {"charset=utf-8", 'charset="utf-8"'}
    if any(not piece for piece in pieces[1:]) or (
        parameters
        and (
            not allow_utf8_charset
            or len(parameters) != 1
            or parameters[0] not in allowed_parameters
        )
    ):
        raise _problem(
            415,
            "UNSUPPORTED_MEDIA_TYPE_PARAMETER",
            "Unsupported media type parameter",
            "content-type parameters are not supported",
        )
    encoding = single_header(request, "content-encoding")
    if encoding is not None and encoding.strip().lower() != "identity":
        raise _problem(
            415,
            "CONTENT_ENCODING_UNSUPPORTED",
            "Unsupported content encoding",
            "compressed request bodies are not accepted",
        )
    return media_type


def _declared_length(request: Request, limit: int) -> int | None:
    content_length = single_header(request, "content-length")
    transfer_encoding = single_header(request, "transfer-encoding")
    if content_length is not None and transfer_encoding is not None:
        raise _problem(
            400,
            "AMBIGUOUS_BODY_FRAMING",
            "Ambiguous body framing",
            "content-length and transfer-encoding cannot be combined",
        )
    if transfer_encoding is not None and transfer_encoding.strip().lower() != "chunked":
        raise _problem(
            400,
            "INVALID_TRANSFER_ENCODING",
            "Invalid transfer encoding",
            "transfer-encoding must be exactly chunked",
        )
    if content_length is None:
        return None
    if not content_length or len(content_length) > 64 or not content_length.isascii():
        raise _problem(
            400,
            "INVALID_CONTENT_LENGTH",
            "Invalid content length",
            "content-length must be a non-negative decimal integer",
        )
    declared = 0
    for character in content_length:
        if not "0" <= character <= "9":
            raise _problem(
                400,
                "INVALID_CONTENT_LENGTH",
                "Invalid content length",
                "content-length must be a non-negative decimal integer",
            )
        declared = declared * 10 + ord(character) - ord("0")
        if declared > limit:
            raise _problem(
                413,
                "BODY_TOO_LARGE",
                "Request body too large",
                f"request body exceeds the {limit}-byte endpoint limit",
            )
    return declared


async def read_bounded_body(request: Request, *, limit: int) -> bytes:
    """Read one raw body, enforcing declared and observed framing limits."""

    if type(limit) is not int or limit < 0:
        raise RuntimeError("body limit configuration is invalid")
    declared = _declared_length(request, limit)
    output = bytearray()
    observed = 0
    try:
        async for chunk in request.stream():
            if type(chunk) is not bytes:
                raise _problem(
                    400,
                    "INVALID_BODY_FRAMING",
                    "Invalid body framing",
                    "request stream yielded an invalid body chunk",
                )
            if not chunk:
                continue
            observed += len(chunk)
            if declared is not None and observed > declared:
                raise _problem(
                    400,
                    "CONTENT_LENGTH_MISMATCH",
                    "Content length mismatch",
                    "observed body length does not match content-length",
                )
            if observed > limit:
                raise _problem(
                    413,
                    "BODY_TOO_LARGE",
                    "Request body too large",
                    f"request body exceeds the {limit}-byte endpoint limit",
                )
            output.extend(chunk)
    except ClientDisconnect:
        raise _problem(
            400,
            "CLIENT_DISCONNECTED",
            "Client disconnected",
            "request body ended before framing completed",
        ) from None
    if declared is not None and observed != declared:
        raise _problem(
            400,
            "CONTENT_LENGTH_MISMATCH",
            "Content length mismatch",
            "observed body length does not match content-length",
        )
    return bytes(output)


def _decode_query_component(raw: bytes) -> str:
    index = 0
    while index < len(raw):
        if raw[index] == ord("%"):
            if index + 2 >= len(raw) or any(
                character not in b"0123456789abcdefABCDEF"
                for character in raw[index + 1 : index + 3]
            ):
                raise _problem(
                    400,
                    "MALFORMED_QUERY",
                    "Malformed query",
                    "query contains an invalid percent escape",
                )
            index += 3
        else:
            index += 1
    try:
        return unquote_to_bytes(raw.replace(b"+", b" ")).decode("utf-8")
    except UnicodeDecodeError:
        raise _problem(
            400,
            "MALFORMED_QUERY",
            "Malformed query",
            "query is not valid UTF-8",
        ) from None


def parse_query(
    request: Request,
    *,
    allowed: Collection[str],
    required: Collection[str] = (),
) -> dict[str, str]:
    """Parse an exact, duplicate-free UTF-8 query field mapping."""

    raw = request.scope.get("query_string", b"")
    if type(raw) is not bytes:
        raise _problem(400, "MALFORMED_QUERY", "Malformed query", "query framing is invalid")
    if len(raw) > MAX_QUERY_BYTES:
        raise _problem(
            400,
            "QUERY_TOO_LARGE",
            "Query too large",
            f"query exceeds the {MAX_QUERY_BYTES}-byte limit",
        )
    result: dict[str, str] = {}
    if raw:
        for field in raw.split(b"&"):
            if not field or field.count(b"=") != 1:
                raise _problem(
                    400,
                    "MALFORMED_QUERY",
                    "Malformed query",
                    "each query field must contain exactly one equals sign",
                )
            raw_name, raw_value = field.split(b"=", 1)
            name = _decode_query_component(raw_name)
            value = _decode_query_component(raw_value)
            if not name:
                raise _problem(
                    400,
                    "MALFORMED_QUERY",
                    "Malformed query",
                    "query field names must not be empty",
                )
            if name not in allowed:
                raise _problem(
                    400,
                    "UNKNOWN_QUERY_FIELD",
                    "Unknown query field",
                    "query contains a field not supported by this endpoint",
                )
            if name in result:
                raise _problem(
                    400,
                    "DUPLICATE_QUERY_FIELD",
                    "Duplicate query field",
                    "query fields must occur exactly once",
                )
            result[name] = value
    for name in required:
        if name not in result:
            raise _problem(
                400,
                "MISSING_QUERY_FIELD",
                "Missing query field",
                f"required query field {name} is missing",
            )
    return result


__all__ = [
    "MAX_HEADER_VALUE_BYTES",
    "MAX_QUERY_BYTES",
    "parse_query",
    "read_bounded_body",
    "require_media_type",
    "single_header",
]
