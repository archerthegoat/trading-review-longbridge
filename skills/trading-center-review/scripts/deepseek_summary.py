#!/usr/bin/env python3
"""Summarize an allowlisted trading-center fact packet with DeepSeek.

The local deterministic scripts remain the source of truth for broker and
market data.  This adapter accepts only a small, structured, sanitized JSON
packet and never reads broker credentials or raw broker responses itself.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_BASE_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash"
SCHEMA_VERSION = "trading-center-summary.v1"

ALLOWED_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "as_of",
        "timezone",
        "data_status",
        "authorization_status",
        "positions",
        "executions",
        "events",
        "plan_items",
    }
)
ALLOWED_RECORD_KEYS = frozenset(
    {
        "symbol",
        "name",
        "market",
        "side",
        "quantity",
        "available_quantity",
        "price",
        "date",
        "time",
        "time_asia_shanghai",
        "time_new_york",
        "event",
        "status",
        "source",
        "data_status",
        "actual",
        "forecast",
        "previous",
        "impact_object",
        "role",
        "target_range",
        "trigger",
        "invalidation",
        "plan_status",
    }
)

# These fields are not needed for a useful summary and must not cross the
# external-provider boundary.  Matching is case-insensitive and treats a
# field such as orderId as equivalent to order_id.
FORBIDDEN_KEY_TERMS = (
    "api_key",
    "apikey",
    "access_token",
    "accesstoken",
    "refresh_token",
    "refreshtoken",
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "password",
    "secret",
    "private_key",
    "privatekey",
    "account_id",
    "accountid",
    "broker_account",
    "brokeraccount",
    "order_id",
    "orderid",
    "execution_id",
    "executionid",
    "transaction_id",
    "transactionid",
    "raw_response",
    "rawresponse",
    "raw_payload",
    "rawpayload",
    "session_id",
    "sessionid",
    "commission",
    "statement",
)
SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~-]+"),
    re.compile(r"(?i)\b(?:api[_ -]?key|access[_ -]?token|password|secret)\s*[:=]"),
    re.compile(r"(?i)\b(?:order|execution|account|transaction)[_ -]?(?:id|number)?\s*[:=]"),
    re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b"),
    re.compile(r"(?:订单|成交|账户|交易)[_ -]?(?:id|编号|标识|号)\s*[:=]", re.IGNORECASE),
)


class DeepSeekSummaryError(RuntimeError):
    """A fail-closed, user-actionable DeepSeek integration error."""


def _normalized_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(key).strip().lower()).strip("_")


def _find_forbidden_keys(value: object, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = _normalized_key(key)
            child_path = f"{path}.{key}"
            is_top_level_authorization_status = path == "$" and normalized == "authorization_status"
            if not is_top_level_authorization_status and any(term in normalized for term in FORBIDDEN_KEY_TERMS):
                found.append(child_path)
            found.extend(_find_forbidden_keys(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_find_forbidden_keys(child, f"{path}[{index}]"))
    return found


def _check_sensitive_string(value: str, path: str) -> None:
    for pattern in SENSITIVE_VALUE_PATTERNS:
        if pattern.search(value):
            raise DeepSeekSummaryError(f"sensitive value is not allowed at {path}")


def _copy_scalar(value: object, path: str) -> object:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        _check_sensitive_string(value, path)
        return value
    raise DeepSeekSummaryError(f"unsupported value type at {path}")


def _copy_record(value: object, path: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise DeepSeekSummaryError(f"record must be an object at {path}")
    result: dict[str, object] = {}
    for key, child in value.items():
        if key not in ALLOWED_RECORD_KEYS:
            continue
        result[key] = _copy_scalar(child, f"{path}.{key}")
    return result


def sanitize_payload(payload: object) -> dict[str, object]:
    """Validate and project a fact packet to the external-provider allowlist."""
    if not isinstance(payload, dict):
        raise DeepSeekSummaryError("input packet must be a JSON object")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise DeepSeekSummaryError(f"schema_version must be {SCHEMA_VERSION}")

    unknown_top_level = sorted(set(payload) - ALLOWED_TOP_LEVEL_KEYS)
    if unknown_top_level:
        raise DeepSeekSummaryError("unsupported top-level field: " + ", ".join(unknown_top_level[:3]))

    forbidden = _find_forbidden_keys(payload)
    if forbidden:
        raise DeepSeekSummaryError("forbidden field in input packet: " + ", ".join(forbidden[:3]))

    result: dict[str, object] = {"schema_version": SCHEMA_VERSION}
    for key in ("as_of", "timezone", "data_status", "authorization_status"):
        if key in payload:
            result[key] = _copy_scalar(payload[key], f"$.{key}")

    for collection in ("positions", "executions", "events", "plan_items"):
        value = payload.get(collection, [])
        if value is None:
            result[collection] = []
        elif isinstance(value, list):
            result[collection] = [
                _copy_record(item, f"$.{collection}[{index}]")
                for index, item in enumerate(value)
            ]
        else:
            raise DeepSeekSummaryError(f"{collection} must be a JSON array")

    return result


def build_request(payload: dict[str, object], model: str) -> dict[str, object]:
    """Build a deterministic, summary-only DeepSeek request."""
    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是交易中心的数据摘要助手。只使用用户输入的结构化字段，不补充、猜测或改写事实。"
                    "先区分数据状态、公告事实、计划内容和研究判断；缺失或冲突处放入 unresolved。"
                    "只输出 JSON 对象，且严格使用这个字段类型："
                    '{"summary":"string","facts":["string"],"position_impact":["string"],'
                    '"event_impact":["string"],"unresolved":["string"]}。'
                    "summary 必须是字符串；其余四个字段必须是只含字符串的 JSON 数组，"
                    "没有内容时返回空数组，禁止返回对象、嵌套数组或其他字段。"
                    "不生成买入、卖出、加仓、减仓或其他交易指令。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            },
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
        "max_tokens": 2000,
        "stream": False,
        "thinking": {"type": "disabled"},
    }


def _parse_summary_response(response: object) -> dict[str, object]:
    if not isinstance(response, dict):
        raise DeepSeekSummaryError("DeepSeek returned a non-object response")
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise DeepSeekSummaryError("DeepSeek response has no usable choice")
    message = choices[0].get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise DeepSeekSummaryError("DeepSeek response has no JSON content")
    try:
        content = json.loads(message["content"])
    except json.JSONDecodeError as error:
        raise DeepSeekSummaryError("DeepSeek returned non-JSON summary content") from error
    if not isinstance(content, dict):
        raise DeepSeekSummaryError("DeepSeek summary content must be a JSON object")
    required = ("summary", "facts", "position_impact", "event_impact", "unresolved")
    missing = [key for key in required if key not in content]
    if missing:
        raise DeepSeekSummaryError("DeepSeek summary is missing: " + ", ".join(missing))
    unexpected = sorted(set(content) - set(required))
    if unexpected:
        raise DeepSeekSummaryError("DeepSeek summary has unsupported fields: " + ", ".join(unexpected))
    if not isinstance(content["summary"], str):
        raise DeepSeekSummaryError("DeepSeek summary.summary must be a string")
    _check_sensitive_string(content["summary"], "$.summary.summary")
    for key in ("facts", "position_impact", "event_impact", "unresolved"):
        if not isinstance(content[key], list):
            raise DeepSeekSummaryError(f"DeepSeek summary.{key} must be an array")
        if not all(isinstance(item, str) for item in content[key]):
            raise DeepSeekSummaryError(f"DeepSeek summary.{key} must contain strings")
        for index, item in enumerate(content[key]):
            _check_sensitive_string(item, f"$.summary.{key}[{index}]")
    return content


def _call_api(request_body: dict[str, object], api_key: str, base_url: str) -> dict[str, object]:
    request = urllib.request.Request(
        base_url,
        data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        raise DeepSeekSummaryError(f"DeepSeek HTTP error {error.code}") from error
    except urllib.error.URLError as error:
        raise DeepSeekSummaryError(f"DeepSeek network error: {error.reason}") from error
    except TimeoutError as error:
        raise DeepSeekSummaryError("DeepSeek request timed out") from error

    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DeepSeekSummaryError("DeepSeek returned invalid JSON") from error
    if isinstance(envelope, dict) and envelope.get("error"):
        raise DeepSeekSummaryError("DeepSeek returned an API error")
    return _parse_summary_response(envelope)


def is_in_git_worktree(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    return any((parent / ".git").exists() for parent in (resolved.parent, *resolved.parents))


def write_result(path: Path, result: dict[str, object]) -> None:
    path = path.expanduser().resolve()
    if is_in_git_worktree(path):
        raise DeepSeekSummaryError("output must be outside the Git worktree")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = temporary.name
            json.dump(result, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
    finally:
        if temporary_path:
            temporary = Path(temporary_path)
            if temporary.exists():
                temporary.unlink()


def summarize(
    payload: object,
    *,
    api_key: str,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
) -> dict[str, object]:
    if not api_key.strip():
        raise DeepSeekSummaryError("DEEPSEEK_API_KEY is not configured")
    sanitized = sanitize_payload(payload)
    request_body = build_request(sanitized, model)
    summary = _call_api(request_body, api_key, base_url)
    return {
        "status": "completed",
        "provider": "deepseek",
        "model": model,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_schema_version": SCHEMA_VERSION,
        "summary": summary,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Sanitized JSON fact packet")
    parser.add_argument("--output", required=True, type=Path, help="Private JSON result outside Git")
    parser.add_argument("--model", default=os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL))
    parser.add_argument("--base-url", default=os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        input_path = args.input.expanduser().resolve()
        if is_in_git_worktree(input_path):
            raise DeepSeekSummaryError("input must be outside the Git worktree")
        with input_path.open("r", encoding="utf-8") as source:
            payload = json.load(source)
        result = summarize(
            payload,
            api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
            model=args.model,
            base_url=args.base_url,
        )
        write_result(args.output, result)
    except (OSError, json.JSONDecodeError) as error:
        print(f"DEEPSEEK_SUMMARY_BLOCKED: input/output error ({type(error).__name__})", file=sys.stderr)
        return 2
    except DeepSeekSummaryError as error:
        print(f"DEEPSEEK_SUMMARY_BLOCKED: {error}", file=sys.stderr)
        return 2
    print(f"DEEPSEEK_SUMMARY_COMPLETED: model={args.model} output={args.output.expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
