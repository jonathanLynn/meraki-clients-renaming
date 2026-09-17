#!/usr/bin/env python3
"""Find Meraki clients by MAC and set their display description to a hostname."""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
from urllib.parse import quote


# Edit this list. MAC addresses may use colons, hyphens, dots, or no separators.
DEVICES = [
    {"mac": "00:11:22:33:44:55", "hostname": "LAPTOP-001"},
    {"mac": "AA-BB-CC-DD-EE-FF", "hostname": "LAPTOP-002"},
]

API_BASE = "https://api.meraki.com/api/v1"
MAC_PATTERN = re.compile(r"^[0-9a-f]{12}$")


def normalize_mac(value: str) -> str:
    compact = re.sub(r"[^0-9A-Fa-f]", "", value).lower()
    if not MAC_PATTERN.fullmatch(compact):
        raise ValueError(f"Invalid MAC address: {value!r}")
    return compact


def display_mac(compact: str) -> str:
    return ":".join(compact[index : index + 2] for index in range(0, 12, 2))


def validate_devices(devices: list[dict[str, str]]) -> list[dict[str, str]]:
    validated: list[dict[str, str]] = []
    seen: dict[str, str] = {}
    for index, device in enumerate(devices, start=1):
        hostname = str(device.get("hostname", "")).strip()
        if not hostname:
            raise ValueError(f"DEVICES entry {index} has no hostname")
        if len(hostname.encode("utf-8")) > 255:
            raise ValueError(f"Hostname {hostname!r} exceeds Meraki's 255-byte limit")
        mac = normalize_mac(str(device.get("mac", "")))
        if mac in seen and seen[mac] != hostname:
            raise ValueError(f"MAC {display_mac(mac)} is assigned to both {seen[mac]!r} and {hostname!r}")
        if mac not in seen:
            seen[mac] = hostname
            validated.append({"mac": mac, "hostname": hostname})
    return validated


class MerakiAPI:
    def __init__(self, api_key: str, base_url: str = API_BASE, verify_ssl: bool = True) -> None:
        self.base_url = base_url.rstrip("/")
        self.ssl_context = None if verify_ssl else ssl._create_unverified_context()
        self.headers = {
            "X-Cisco-Meraki-API-Key": api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "mac-hostname-meraki-renamer/1.0",
        }

    def request(
        self,
        method: str,
        path_or_url: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        url = path_or_url if path_or_url.startswith("http") else self.base_url + path_or_url
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        for attempt in range(6):
            request = urllib.request.Request(url, data=data, headers=self.headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=30, context=self.ssl_context) as response:
                    body = response.read()
                    return json.loads(body) if body else None
            except urllib.error.HTTPError as error:
                body = error.read().decode("utf-8", errors="replace")
                if error.code == 429 and attempt < 5:
                    time.sleep(float(error.headers.get("Retry-After", "1")))
                    continue
                if 500 <= error.code < 600 and attempt < 5:
                    time.sleep(min(2**attempt, 16))
                    continue
                raise RuntimeError(
                    f"Meraki API {method} {url} returned HTTP {error.code}: {body[:1000]}"
                ) from error
            except urllib.error.URLError as error:
                if attempt < 5:
                    time.sleep(min(2**attempt, 16))
                    continue
                raise RuntimeError(f"Meraki API request failed: {error.reason}") from error
        raise RuntimeError("Meraki API retry limit reached")

    def search_client(self, organization_id: str, mac: str) -> dict[str, Any] | None:
        response = self.request(
            "GET",
            f"/organizations/{quote(organization_id, safe='')}/clients/search",
            # Organization client search currently accepts only 3-5 items per page.
            # The MAC filter is exact, so five results is sufficient for this lookup.
            params={"mac": display_mac(mac), "perPage": 5},
        )
        body = response
        if not body:
            return None
        if isinstance(body, list):
            exact = [item for item in body if normalize_mac(str(item.get("mac", ""))) == mac]
            return exact[0] if exact else None
        return body if normalize_mac(str(body.get("mac", ""))) == mac else None

    def get_policy(self, network_id: str, client_id: str) -> dict[str, Any]:
        return self.request(
            "GET",
            f"/networks/{quote(network_id, safe='')}/clients/{quote(client_id, safe='')}/policy",
        )

    def rename_client(
        self, network_id: str, mac: str, hostname: str, policy: dict[str, Any]
    ) -> None:
        payload = policy_for_provision(policy)
        payload["clients"] = [{"mac": display_mac(mac), "name": hostname}]
        self.request(
            "POST",
            f"/networks/{quote(network_id, safe='')}/clients/provision",
            payload=payload,
        )


def policy_for_provision(policy: dict[str, Any]) -> dict[str, Any]:
    current = str(policy.get("devicePolicy") or "Normal")
    device_policy = {
        "Whitelisted": "Allowed",
        "Different policies by SSID": "Per connection",
    }.get(current, current)
    allowed = {"Allowed", "Blocked", "Group policy", "Normal", "Per connection"}
    if device_policy not in allowed:
        raise ValueError(f"Unsupported existing Meraki policy: {current!r}")

    payload: dict[str, Any] = {"devicePolicy": device_policy}
    if device_policy == "Group policy" and policy.get("groupPolicyId"):
        payload["groupPolicyId"] = policy["groupPolicyId"]
    if device_policy == "Per connection":
        by_ssid: dict[str, dict[str, Any]] = {}
        for item in policy.get("policiesBySsid") or []:
            if "ssidNumber" not in item or "devicePolicy" not in item:
                continue
            entry = {"devicePolicy": item["devicePolicy"]}
            if item.get("groupPolicyId"):
                entry["groupPolicyId"] = item["groupPolicyId"]
            by_ssid[str(item["ssidNumber"])] = entry
        payload["policiesBySsid"] = by_ssid
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--organization-id",
        default=os.getenv("MERAKI_ORGANIZATION_ID"),
        help="Meraki organization ID; defaults to MERAKI_ORGANIZATION_ID",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform changes. Without this option the script is a dry run.",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS certificate verification (unsafe; use only on trusted networks)",
    )
    parser.add_argument(
        "--base-url", default=os.getenv("MERAKI_API_BASE_URL", API_BASE), help=argparse.SUPPRESS
    )
    return parser.parse_args()


def run() -> int:
    args = parse_args()
    api_key = os.getenv("MERAKI_DASHBOARD_API_KEY")
    if not api_key:
        raise ValueError("Set MERAKI_DASHBOARD_API_KEY in the environment")
    if not args.organization_id:
        raise ValueError("Set MERAKI_ORGANIZATION_ID or provide --organization-id")

    devices = validate_devices(DEVICES)
    if args.insecure:
        print("WARNING: TLS certificate verification is disabled.", file=sys.stderr)
    api = MerakiAPI(api_key, args.base_url, verify_ssl=not args.insecure)
    results: list[dict[str, Any]] = []

    for device in devices:
        mac = device["mac"]
        hostname = device["hostname"]
        try:
            client = api.search_client(args.organization_id, mac)
            if not client:
                results.append({"mac": display_mac(mac), "hostname": hostname, "status": "not_found"})
                continue

            client_id = str(client.get("clientId") or client.get("id") or display_mac(mac))
            records = client.get("records") or []
            if not records and client.get("network"):
                records = [client]
            if not records:
                results.append(
                    {
                        "mac": display_mac(mac),
                        "hostname": hostname,
                        "status": "not_found",
                        "reason": "Client search returned no network records",
                    }
                )
                continue

            for record in records:
                network = record.get("network") or {}
                network_id = str(network.get("id") or record.get("networkId") or "")
                old_name = str(record.get("description") or client.get("name") or "")
                result = {
                    "mac": display_mac(mac),
                    "hostname": hostname,
                    "oldName": old_name,
                    "networkId": network_id,
                    "networkName": network.get("name", ""),
                }
                if not network_id:
                    result.update(status="failed", reason="Search record has no network ID")
                elif old_name == hostname:
                    result["status"] = "unchanged"
                elif not args.apply:
                    result["status"] = "would_update"
                else:
                    policy = api.get_policy(network_id, client_id)
                    api.rename_client(network_id, mac, hostname, policy)
                    result["status"] = "updated"
                results.append(result)
        except Exception as error:  # continue processing the remaining mappings
            results.append(
                {"mac": display_mac(mac), "hostname": hostname, "status": "failed", "reason": str(error)}
            )

    counts: dict[str, int] = {}
    for result in results:
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    print(json.dumps({"mode": "apply" if args.apply else "dry-run", "counts": counts, "results": results}, indent=2))
    return 1 if counts.get("failed") else 0


def main() -> int:
    try:
        return run()
    except (ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
