#!/usr/bin/env python
"""Run a real HTTP smoke test against the local Flask backend.

The script intentionally does not embed API keys. Pass one with --api-key, set
OUTLOOK_EXTERNAL_API_KEY / EXTERNAL_API_KEY, or put EXTERNAL_API_KEY in .env.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "http://127.0.0.1:5000"


class SmokeTestError(RuntimeError):
    pass


def parse_env_line(line: str) -> Optional[tuple[str, str]]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    value = value.strip().strip('"').strip("'")
    if key.startswith("export "):
        key = key[len("export ") :].strip()
    if not key:
        return None
    return key, value


def read_env_api_key(env_file: Path) -> str:
    if not env_file.exists():
        return ""
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    values: Dict[str, str] = {}
    for line in lines:
        parsed = parse_env_line(line)
        if parsed:
            key, value = parsed
            if key in {"OUTLOOK_EXTERNAL_API_KEY", "EXTERNAL_API_KEY"}:
                values[key] = value
    return values.get("OUTLOOK_EXTERNAL_API_KEY") or values.get("EXTERNAL_API_KEY") or ""


def resolve_api_key(cli_api_key: str, env_file: Path = ROOT_DIR / ".env") -> str:
    return (
        cli_api_key
        or os.getenv("OUTLOOK_EXTERNAL_API_KEY", "")
        or os.getenv("EXTERNAL_API_KEY", "")
        or read_env_api_key(env_file)
    )


def parse_group_ids(value: str) -> List[int]:
    ids: List[int] = []
    for part in str(value or "").replace(";", ",").split(","):
        item = part.strip()
        if not item:
            continue
        try:
            group_id = int(item)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"invalid group id: {item}") from exc
        if group_id <= 0:
            raise argparse.ArgumentTypeError(f"group id must be positive: {item}")
        ids.append(group_id)
    return ids


def mask_email(email: Optional[str]) -> str:
    if not email:
        return ""
    if "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    prefix = local[:2] if len(local) > 2 else local
    return f"{prefix}***@{domain}"


def request_json(
    method: str,
    base_url: str,
    path: str,
    api_key: str,
    payload: Optional[Dict[str, Any]] = None,
    timeout: float = 15.0,
) -> Dict[str, Any]:
    url = base_url.rstrip("/") + path
    body = None
    headers = {"X-API-Key": api_key}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=headers, method=method.upper())
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw or "{}")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise SmokeTestError(f"{method} {path} returned HTTP {exc.code}: {raw}") from exc
    except URLError as exc:
        raise SmokeTestError(f"{method} {path} failed: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise SmokeTestError(f"{method} {path} returned non-JSON response") from exc


def server_responds(base_url: str, timeout: float = 2.0) -> bool:
    try:
        with urlopen(base_url.rstrip("/") + "/", timeout=timeout):
            return True
    except HTTPError:
        return True
    except URLError:
        return False


def wait_for_server(base_url: str, timeout: float) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if server_responds(base_url):
            return
        time.sleep(0.5)
    raise SmokeTestError(f"server did not respond at {base_url} within {timeout:.0f}s")


def start_backend_if_needed(base_url: str, startup_timeout: float) -> Optional[subprocess.Popen]:
    if server_responds(base_url):
        print(f"Using existing backend at {base_url}")
        return None

    tmp_dir = ROOT_DIR / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    log_path = tmp_dir / "e2e-external-api-smoke.log"
    log_file = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "web_outlook_app.py"],
        cwd=str(ROOT_DIR),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )
    print(f"Started backend PID={process.pid}, log={log_path}")
    try:
        wait_for_server(base_url, startup_timeout)
    except Exception:
        process.terminate()
        raise
    return process


def summarize_accounts(data: Dict[str, Any]) -> str:
    accounts = list(data.get("accounts") or [])
    masked = ", ".join(mask_email(item.get("email")) for item in accounts[:5])
    if len(accounts) > 5:
        masked += f", ... +{len(accounts) - 5}"
    return masked


def run_smoke(args: argparse.Namespace) -> None:
    api_key = resolve_api_key(args.api_key)
    if not api_key:
        raise SmokeTestError("missing API key; pass --api-key, set OUTLOOK_EXTERNAL_API_KEY, or set EXTERNAL_API_KEY in .env")

    backend = start_backend_if_needed(args.base_url, args.startup_timeout)
    claimed_mailbox: Optional[Dict[str, Any]] = None
    try:
        group_summaries: Dict[int, Dict[str, Any]] = {}
        for group_id in args.group_ids:
            query = urlencode(
                {
                    "group_id": group_id,
                    "limit": args.limit,
                    "sort_by": "created_at",
                    "sort_order": "asc",
                }
            )
            data = request_json("GET", args.base_url, f"/api/external/accounts?{query}", api_key)
            if not data.get("success"):
                raise SmokeTestError(f"accounts query failed for group {group_id}: {data}")
            group_summaries[group_id] = data
            print(
                f"accounts group_id={group_id}: "
                f"total={data.get('total')} returned={len(data.get('accounts') or [])} "
                f"emails=[{summarize_accounts(data)}]"
            )

        claim_group_id = args.claim_group_id
        if claim_group_id is None:
            for group_id, data in group_summaries.items():
                if int(data.get("total") or 0) > 0:
                    claim_group_id = group_id
                    break
        if claim_group_id is None:
            raise SmokeTestError("no claim group available; pass --claim-group-id for a non-empty group")

        claim_payload = {
            "source_group_id": claim_group_id,
            "caller_id": args.caller_id,
            "task_id": args.task_id,
            "lease_seconds": args.lease_seconds,
        }
        claim = request_json("POST", args.base_url, "/api/external/mailboxes/claim", api_key, claim_payload)
        if not claim.get("success"):
            raise SmokeTestError(f"claim failed: {claim}")
        claimed_mailbox = claim.get("mailbox")
        if not claimed_mailbox:
            raise SmokeTestError(f"claim group {claim_group_id} returned mailbox=null")

        print(
            "claim: "
            f"group_id={claim_group_id} "
            f"{claimed_mailbox.get('resource_type')}#{claimed_mailbox.get('resource_id')} "
            f"{mask_email(claimed_mailbox.get('email'))}"
        )

        release_payload = {
            "resource_type": claimed_mailbox.get("resource_type"),
            "resource_id": claimed_mailbox.get("resource_id"),
            "claim_token": claimed_mailbox.get("claim_token"),
            "caller_id": args.caller_id,
            "task_id": args.task_id,
            "detail": "e2e external api smoke release",
        }
        release = request_json("POST", args.base_url, "/api/external/mailboxes/release", api_key, release_payload)
        if not release.get("success"):
            raise SmokeTestError(f"release failed: {release}")
        claimed_mailbox = None
        print("release: success")
    finally:
        if claimed_mailbox:
            try:
                request_json(
                    "POST",
                    args.base_url,
                    "/api/external/mailboxes/release",
                    api_key,
                    {
                        "resource_type": claimed_mailbox.get("resource_type"),
                        "resource_id": claimed_mailbox.get("resource_id"),
                        "claim_token": claimed_mailbox.get("claim_token"),
                        "caller_id": args.caller_id,
                        "task_id": args.task_id,
                        "detail": "e2e cleanup after failure",
                    },
                )
                print("cleanup release: success")
            except Exception as exc:
                print(f"cleanup release failed: {exc}", file=sys.stderr)
        if backend is not None:
            backend.terminate()
            try:
                backend.wait(timeout=10)
            except subprocess.TimeoutExpired:
                backend.kill()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run real HTTP smoke tests for the external mailbox API.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--group-ids", type=parse_group_ids, default=parse_group_ids("1,2,49,50"))
    parser.add_argument("--claim-group-id", type=int)
    parser.add_argument("--caller-id", default="codex-e2e-smoke")
    parser.add_argument("--task-id", default="external-api-smoke")
    parser.add_argument("--lease-seconds", type=int, default=120)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--startup-timeout", type=float, default=30.0)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        run_smoke(args)
    except SmokeTestError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    print("PASSED: external API e2e smoke")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
