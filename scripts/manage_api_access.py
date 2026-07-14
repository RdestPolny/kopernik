#!/usr/bin/env python3
"""Manage Kopernik API organizations and hash-only API keys in Firestore."""

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
from datetime import datetime, timezone

import requests


def _gcloud(*args: str) -> str:
    result = subprocess.run(
        ["gcloud", *args], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _project(explicit: str) -> str:
    project = explicit or os.getenv("FIRESTORE_PROJECT", "")
    if not project:
        project = _gcloud("config", "get-value", "project")
    if not project or project == "(unset)":
        sys.exit("Set --project, FIRESTORE_PROJECT or the active gcloud project.")
    return project


def _token() -> str:
    try:
        return _gcloud("auth", "print-access-token")
    except subprocess.CalledProcessError as exc:
        sys.exit(f"Google Cloud authentication failed. Run `gcloud auth login`.\n{exc.stderr}")


def _field(value):
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, list):
        return {"arrayValue": {"values": [_field(item) for item in value]}}
    return {"stringValue": str(value)}


def _document_url(project: str, collection: str, document_id: str = "") -> str:
    base = f"https://firestore.googleapis.com/v1/projects/{project}/databases/(default)/documents/{collection}"
    return f"{base}/{document_id}" if document_id else base


def _write_document(project: str, collection: str, document_id: str, fields: dict) -> None:
    response = requests.patch(
        _document_url(project, collection, document_id),
        headers={"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"},
        json={"fields": {key: _field(value) for key, value in fields.items()}},
        timeout=30,
    )
    if response.status_code != 200:
        sys.exit(f"Firestore write failed ({response.status_code}): {response.text[:500]}")


def _document_exists(project: str, collection: str, document_id: str) -> bool:
    response = requests.get(
        _document_url(project, collection, document_id),
        headers={"Authorization": f"Bearer {_token()}"},
        timeout=30,
    )
    if response.status_code == 404:
        return False
    if response.status_code != 200:
        sys.exit(f"Firestore read failed ({response.status_code}): {response.text[:500]}")
    return True


def create_organization(args) -> None:
    now = datetime.now(timezone.utc).isoformat()
    _write_document(
        _project(args.project),
        "organizations",
        args.organization_id,
        {"name": args.name, "active": True, "created_at": now},
    )
    print(f"Organization created: {args.organization_id}")


def create_key(args) -> None:
    project = _project(args.project)
    if not _document_exists(project, "organizations", args.organization_id):
        sys.exit(f"Organization does not exist: {args.organization_id}")
    key_id = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    api_key = f"kop_{args.environment}_{key_id}_{secret}"
    scopes = [scope.strip() for scope in args.scopes.split(",") if scope.strip()]
    if not scopes:
        sys.exit("At least one scope is required.")
    fields = {
        "organization_id": args.organization_id,
        "name": args.name,
        "environment": args.environment,
        "key_hash": hashlib.sha256(api_key.encode("utf-8")).hexdigest(),
        "scopes": scopes,
        "revoked": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if args.expires_at:
        fields["expires_at"] = args.expires_at
    _write_document(project, "api_keys", key_id, fields)
    print("API key created. Copy it now; it cannot be recovered later:")
    print(api_key)


def revoke_key(args) -> None:
    project = _project(args.project)
    url = _document_url(project, "api_keys", args.key_id)
    response = requests.patch(
        url,
        params={"updateMask.fieldPaths": ["revoked", "revoked_at"]},
        headers={"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"},
        json={
            "fields": {
                "revoked": _field(True),
                "revoked_at": _field(datetime.now(timezone.utc).isoformat()),
            }
        },
        timeout=30,
    )
    if response.status_code != 200:
        sys.exit(f"Firestore update failed ({response.status_code}): {response.text[:500]}")
    print(f"API key revoked: {args.key_id}")


def list_keys(args) -> None:
    project = _project(args.project)
    response = requests.get(
        _document_url(project, "api_keys"),
        headers={"Authorization": f"Bearer {_token()}"},
        params={"pageSize": 300},
        timeout=30,
    )
    if response.status_code != 200:
        sys.exit(f"Firestore read failed ({response.status_code}): {response.text[:500]}")
    rows = []
    for document in response.json().get("documents", []):
        fields = document.get("fields", {})
        rows.append({
            "key_id": document.get("name", "").rsplit("/", 1)[-1],
            "organization_id": fields.get("organization_id", {}).get("stringValue"),
            "name": fields.get("name", {}).get("stringValue"),
            "environment": fields.get("environment", {}).get("stringValue"),
            "revoked": fields.get("revoked", {}).get("booleanValue", False),
        })
    print(json.dumps(rows, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="", help="Google Cloud / Firestore project")
    commands = parser.add_subparsers(dest="command", required=True)

    org = commands.add_parser("create-organization")
    org.add_argument("--id", dest="organization_id", required=True)
    org.add_argument("--name", required=True)
    org.set_defaults(handler=create_organization)

    create = commands.add_parser("create-key")
    create.add_argument("--organization", dest="organization_id", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--environment", choices=("live", "test"), default="live")
    create.add_argument("--scopes", default="audits:create,audits:read,usage:read")
    create.add_argument("--expires-at", default="", help="ISO-8601 timestamp")
    create.set_defaults(handler=create_key)

    revoke = commands.add_parser("revoke-key")
    revoke.add_argument("--id", dest="key_id", required=True)
    revoke.set_defaults(handler=revoke_key)

    listing = commands.add_parser("list-keys")
    listing.set_defaults(handler=list_keys)

    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
