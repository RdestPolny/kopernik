# Kopernik API v1

API v1 provides a stable boundary around the existing audit engine. It supports
starting an audit, checking its status and retrieving summary, finding and page
sections separately.

The API is currently a contract-first preview. Authentication, tenant isolation,
durable jobs and usage limits are planned for the following implementation phases.
It must not be offered as an unrestricted commercial API until those controls are
in place.

## Base URL

Production deployment:

```text
https://strategiczni.ai/llms-audit/v1
```

Interactive OpenAPI documentation:

```text
https://strategiczni.ai/llms-audit/docs
```

## Start an audit

```http
POST /v1/audits
Content-Type: application/json

{
  "domain": "example.com"
}
```

The endpoint returns `202 Accepted`. When a predefined report exists, its status
may already be `completed`.

```json
{
  "audit_id": "03365d5bd9334b7b88f55f0bdd7740f7",
  "status": "running",
  "created_at": "2026-07-12T20:00:00Z",
  "url": "https://example.com",
  "schema_version": "1.0"
}
```

An optional `picks` array can select up to five URLs explicitly:

```json
{
  "domain": "example.com",
  "picks": [
    {"url": "https://example.com", "page_type": "homepage"},
    {"url": "https://example.com/services", "page_type": "service"}
  ]
}
```

## Check status

```http
GET /v1/audits/{audit_id}
```

Public statuses are `running`, `completed` and `failed`. The response also carries
the schema, scoring and knowledge-base versions used to interpret the result.

## Retrieve result sections

```http
GET /v1/audits/{audit_id}/summary
GET /v1/audits/{audit_id}/findings?page=1&page_size=50
GET /v1/audits/{audit_id}/pages?page=1&page_size=20
```

Findings and pages are paginated. `page_size` is limited to 100.

## Capabilities

```http
GET /v1/capabilities
```

This endpoint reports current contract, scoring and knowledge-base versions and
the maximum number of audited pages.

## Errors

All v1 errors use one envelope:

```json
{
  "error": {
    "code": "audit_not_found",
    "message": "Audit not found",
    "request_id": "20ac3f49f94c42f7a4de318aee038e12",
    "details": null
  }
}
```

Legacy endpoints retain their previous error format for compatibility.
