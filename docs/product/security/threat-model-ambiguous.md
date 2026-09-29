# Threat model: ambiguous

STRIDE-lite pass over the normalised spec. 'mitigated-in-code' means an existing control was found in the codebase; 'residual-risk' items are not addressed and are carried into release readiness.

| ID | Category | Threat | Severity | Mitigation | Status |
|---|---|---|---|---|---|
| T1 | Tampering | Attacker submits javascript:/data:/file: URLs to run script behind our domain | high | Only absolute http(s) URLs accepted (validate_url) | mitigated-in-code |
| T4 | Denial of service | Unauthenticated clients flood link creation | high | Per-client token-bucket rate limit on creation | addressed-in-spec |
| T5 | Denial of service | Clients hammer redirects to inflate analytics or load | medium | Per-client redirect rate limit | addressed-in-spec |
| T6 | Elevation of privilege | Short links pointing at internal hosts (169.254.169.254, localhost) aid phishing/SSRF chains | high | Reject private/loopback/link-local IPs and internal hostnames | addressed-in-spec |
| T7 | Spoofing | https://trusted.com@evil.com style URLs deceive users | medium | Reject URLs with embedded credentials | addressed-in-spec |
