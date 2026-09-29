# Threat model: brownfield

STRIDE-lite pass over the normalised spec. 'mitigated-in-code' means an existing control was found in the codebase; 'residual-risk' items are not addressed and are carried into release readiness.

| ID | Category | Threat | Severity | Mitigation | Status |
|---|---|---|---|---|---|
| T2 | Information disclosure | Sequential codes let attackers enumerate every link | medium | Codes drawn from a CSPRNG over a 62^7 space | mitigated-in-code |
| T3 | Information disclosure | Analytics could persist raw IPs / user agents (personal data) | high | Visitor identity stored only as salted truncated hash; referrer reduced to host | addressed-in-spec |
| T5 | Denial of service | Clients hammer redirects to inflate analytics or load | medium | Per-client redirect rate limit | residual-risk |
| T8 | Repudiation | No way to correlate a client report with server logs | low | X-Request-ID propagated on every response | mitigated-in-code |
