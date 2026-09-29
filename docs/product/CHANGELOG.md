# Changelog

## 1.2.0 - ambiguous

Harden the public-facing shortener against malicious destinations and automated abuse without changing behaviour for legitimate clients

- Abuse-control settings and error types
- Token-bucket rate limiter
- Destination safety policy
- Enforce destination safety in the service layer
- Wire creation rate limit and safety into the API (v1.2.0)
- Per-client redirect rate limit

## 1.1.0 - brownfield

Per-link click analytics without storing personal data, and clicks counted only on successful redirects

- Schema v2 migration (click_events)
- Regression test for BUG-101 (test-first)
- Analytics module
- Fix BUG-101 and record clicks on successful redirects
- Stats endpoint and version 1.1.0

## 1.0.0 - greenfield

HTTP service that creates, resolves, inspects and deletes short links, with optional vanity codes and expiry

- Domain model, errors and configuration
- Declare runtime and test dependencies
- Code generation and URL validation
- Persistence layer and schema v1
- Link service (business rules)
- HTTP API
