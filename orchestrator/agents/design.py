"""Design agent and threat-model agent (run in parallel after requirements)."""

from __future__ import annotations

from ..models import AgentResult, Decision
from .base import AgentContext, AgentError


class DesignAgent:
    name = "design"

    def run(self, ctx: AgentContext) -> AgentResult:
        spec = ctx.inputs["spec"]
        request = {"spec": spec, "codebase": ctx.inputs.get("codebase"), "flags": spec.get("flags", []),
                   "feedback": ctx.feedback}
        design = ctx.provider.generate("design", request)
        if not design.get("document"):
            raise AgentError("provider returned an empty design document")
        slug = ctx.scenario["name"]

        lines = [design["document"].rstrip(), "", "## API contract", "",
                 "| Method | Path | Success | Notes |", "|---|---|---|---|"]
        lines += [f"| {e['method']} | `{e['path']}` | {e.get('status', '')} | {e.get('notes', '')} |"
                  for e in design["endpoints"]]
        if design.get("data_model"):
            lines += ["", "## Data model changes", ""]
            lines += [f"- **{d['table']}**: {d.get('change', '')}" for d in design["data_model"]]
        lines += ["", "## Decisions", ""]
        lines += [f"- [{a['id']}](../adr/{a['id']}.md) {a['title']}" for a in design.get("adrs", [])]
        doc_path = ctx.write(f"docs/design/{slug}.md", "\n".join(lines) + "\n")

        decisions = []
        for adr in design.get("adrs", []):
            body = [
                f"# {adr['id']}: {adr['title']}", "", f"Status: accepted ({slug} scenario)", "",
                "## Decision", "", adr["decision"], "", "## Rationale", "", adr.get("rationale", ""), "",
                "## Alternatives considered", "",
            ] + [f"- {alt}" for alt in adr.get("alternatives", [])]
            if adr.get("consequences"):
                body += ["", "## Consequences", "", adr["consequences"]]
            ctx.write(f"docs/adr/{adr['id']}.md", "\n".join(body) + "\n")
            decisions.append(Decision(
                id=adr["id"], node=ctx.node.id, summary=f"{adr['title']}: {adr['decision']}",
                rationale=adr.get("rationale", ""), alternatives=adr.get("alternatives", []),
                based_on=["spec"] + (["codebase"] if ctx.inputs.get("codebase") else []),
            ))

        return AgentResult(
            outputs={"design": {
                "doc": doc_path,
                "endpoints": design["endpoints"],
                "data_model": design.get("data_model", []),
                "adrs": [{k: a.get(k) for k in ("id", "title", "decision", "rationale", "alternatives")}
                         for a in design.get("adrs", [])],
                "provider": design.get("_provider"),
            }},
            decisions=decisions,
            artifacts=[doc_path],
            summary=f"{len(design['endpoints'])} endpoints, {len(decisions)} ADRs",
        )


# (category, trigger keywords in spec/design, threat, mitigation keywords, mitigation text, severity)
THREATS = [
    ("Tampering", ["url"], "Attacker submits javascript:/data:/file: URLs to run script behind our domain",
     ["http", "scheme"], "Only absolute http(s) URLs accepted (validate_url)", "high"),
    ("Information disclosure", ["code"], "Sequential codes let attackers enumerate every link",
     ["random", "csprng", "base62"], "Codes drawn from a CSPRNG over a 62^7 space", "medium"),
    ("Information disclosure", ["analytics", "visitor", "referrer"],
     "Analytics could persist raw IPs / user agents (personal data)",
     ["hash", "salt"], "Visitor identity stored only as salted truncated hash; referrer reduced to host", "high"),
    ("Denial of service", ["create", "public"], "Unauthenticated clients flood link creation",
     ["rate limit", "rate-limit", "token bucket"], "Per-client token-bucket rate limit on creation", "high"),
    ("Denial of service", ["redirect"], "Clients hammer redirects to inflate analytics or load",
     ["redirect rate", "rate limit on redirects", "rate-limit redirects", "redirect limit"],
     "Per-client redirect rate limit", "medium"),
    ("Elevation of privilege", ["url", "destination"],
     "Short links pointing at internal hosts (169.254.169.254, localhost) aid phishing/SSRF chains",
     ["private", "internal", "loopback"], "Reject private/loopback/link-local IPs and internal hostnames", "high"),
    ("Spoofing", ["url"], "https://trusted.com@evil.com style URLs deceive users",
     ["credential", "userinfo"], "Reject URLs with embedded credentials", "medium"),
    ("Repudiation", ["api"], "No way to correlate a client report with server logs",
     ["request id", "request-id", "x-request-id"], "X-Request-ID propagated on every response", "low"),
]


class ThreatModelAgent:
    name = "threat_model"

    def run(self, ctx: AgentContext) -> AgentResult:
        spec = ctx.inputs["spec"]
        corpus = " ".join(
            [spec.get("goal", "")] + spec.get("functional", []) + spec.get("non_functional", [])
            + spec.get("acceptance_criteria", []) + spec.get("assumptions", [])
        ).lower()
        # Evidence of controls already present in the codebase (brownfield).
        code = " ".join(p.read_text().lower() for p in sorted((ctx.workspace.root / "shortener").glob("*.py"))) \
            if (ctx.workspace.root / "shortener").exists() else ""
        threats = []
        for idx, (cat, triggers, desc, mit_kw, mitigation, sev) in enumerate(THREATS, 1):
            if not any(t in corpus for t in triggers):
                continue
            if any(k in corpus for k in mit_kw):
                status = "addressed-in-spec"
            elif any(k in code for k in mit_kw):
                status = "mitigated-in-code"
            else:
                status = "residual-risk"
            threats.append({"id": f"T{idx}", "category": cat, "threat": desc, "severity": sev,
                            "mitigation": mitigation, "status": status})

        slug = ctx.scenario["name"]
        body = [f"# Threat model: {slug}", "", "STRIDE-lite pass over the normalised spec. "
                "'mitigated-in-code' means an existing control was found in the codebase; "
                "'residual-risk' items are not addressed and are carried into release readiness.",
                "", "| ID | Category | Threat | Severity | Mitigation | Status |", "|---|---|---|---|---|---|"]
        body += [f"| {t['id']} | {t['category']} | {t['threat']} | {t['severity']} | {t['mitigation']} | {t['status']} |"
                 for t in threats]
        path = ctx.write(f"docs/security/threat-model-{slug}.md", "\n".join(body) + "\n")
        residual = [t for t in threats if t["status"] == "residual-risk"]
        return AgentResult(
            outputs={"threats": {"items": threats, "residual": [t["id"] for t in residual], "doc": path}},
            decisions=[Decision(
                id="threats:assessed", node=ctx.node.id,
                summary=f"{len(threats)} threats, {len(residual)} residual",
                rationale="rule-based STRIDE checklist evaluated against the spec", based_on=["spec"])],
            artifacts=[path],
            summary=f"{len(threats)} threats identified, {len(residual)} residual",
        )
