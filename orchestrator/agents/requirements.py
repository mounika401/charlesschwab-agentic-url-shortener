"""Requirements agent: interpret intent, surface ambiguity, normalise into a spec."""

from __future__ import annotations

import re

from ..approvals import ANSWER, ApprovalRequest
from ..models import AgentResult, Decision
from .base import AgentContext, AgentError

# Words that signal an unmeasurable requirement unless a number or explicit
# criterion accompanies them.
VAGUE_TERMS = [
    "safe", "safer", "secure", "fast", "faster", "scalable", "reliable", "robust", "better",
    "improve", "user-friendly", "simple", "efficient", "public use", "production-ready", "some",
]


def detect_vague_terms(text: str) -> list[str]:
    found = []
    lowered = text.lower()
    for term in VAGUE_TERMS:
        for match in re.finditer(rf"\b{re.escape(term)}\b", lowered):
            window = lowered[max(0, match.start() - 60): match.end() + 60]
            if not re.search(r"\d", window):  # quantified nearby -> not vague
                found.append(term)
                break
    return sorted(set(found))


class RequirementsAgent:
    name = "requirements"

    def run(self, ctx: AgentContext) -> AgentResult:
        requirement = ctx.inputs.get("requirement", "")
        if not requirement.strip():
            raise AgentError("empty requirement")
        amendments = ctx.inputs.get("amendments", [])
        prior_answers = ctx.inputs.get("clarification_answers", {})

        raw = ctx.provider.generate("requirements", {"requirement": requirement, "amendments": amendments})
        decisions: list[Decision] = []
        vague = detect_vague_terms(requirement)
        clarifications = list(raw.get("clarifications", []))
        covered = {term for c in clarifications for term in c.get("covers", [])}
        for term in vague:
            if term not in covered:
                clarifications.append({
                    "id": f"define-{term.replace(' ', '-')}",
                    "question": f"The requirement says '{term}' without a measurable criterion. What does it mean here?",
                    "default": "use the interpretation in the normalised spec",
                    "covers": [term],
                })

        answers = dict(prior_answers)
        pending = [c for c in clarifications if c["id"] not in answers]
        if pending:
            decision = ctx.ask_human(ApprovalRequest(
                checkpoint="clarification",
                node=ctx.node.id,
                title=f"{len(pending)} ambiguities need a decision before design can start",
                reasons=[f"vague terms without measurable criteria: {vague}"] if vague else
                        ["provider flagged open questions"],
                summary="\n".join(f"- {c['question']} (default: {c.get('default')})" for c in pending),
                risk="medium",
                questions=pending,
            ))
            if decision.decision != ANSWER:
                raise AgentError(f"clarification not answered: {decision.comment}")
            for c in pending:
                value = decision.answers.get(c["id"], c.get("default"))
                answers[c["id"]] = value
                accepted_default = c["id"] not in decision.answers
                decisions.append(Decision(
                    id=f"clarify:{c['id']}",
                    node=ctx.node.id,
                    summary=f"{c['question']} -> {value}",
                    rationale="default accepted by approver" if accepted_default else "answered by approver",
                    alternatives=[str(o) for o in c.get("options", [])],
                    based_on=["requirement"],
                    decided_by=f"human:{decision.approver}",
                ))

        flags = set(raw.get("flags", []))
        functional = list(raw.get("functional", []))
        acceptance = list(raw.get("acceptance_criteria", []))
        non_functional = list(raw.get("non_functional", []))
        assumptions = list(raw.get("assumptions", []))

        for c in clarifications:
            value = answers.get(c["id"])
            effects = c.get("effects", {}).get(str(value), {})
            flags.update(effects.get("flags", []))
            functional += effects.get("functional", [])
            non_functional += effects.get("non_functional", [])
            acceptance += effects.get("acceptance_criteria", [])
            assumptions.append(f"{c['question']} => {value}")

        for i, amendment in enumerate(amendments, 1):
            flags.update(amendment.get("flags", []))
            if amendment.get("requirement"):
                functional.append(f"[amendment {i}] {amendment['requirement']}")
            acceptance += amendment.get("acceptance_criteria", [])
            decisions.append(Decision(
                id=f"amendment:{i}",
                node=ctx.node.id,
                summary=amendment.get("requirement", "amendment"),
                rationale=amendment.get("reason", "requested at an approval checkpoint"),
                based_on=["requirement"],
                decided_by=f"human:{amendment.get('approver', 'unknown')}",
            ))

        spec = {
            "goal": raw.get("goal", ""),
            "type": ctx.scenario.get("type"),
            "functional": functional,
            "non_functional": non_functional,
            "acceptance_criteria": acceptance,
            "assumptions": assumptions,
            "out_of_scope": raw.get("out_of_scope", []),
            "keywords": raw.get("keywords", []),
            "touches": raw.get("touches", []),
            "flags": sorted(flags),
            "ambiguity": {"vague_terms": vague, "questions": len(clarifications)},
            "open_questions": [],
        }
        decisions.append(Decision(
            id="spec:normalised",
            node=ctx.node.id,
            summary=f"normalised requirement into {len(functional)} functional reqs, "
                    f"{len(acceptance)} acceptance criteria, flags={sorted(flags)}",
            rationale=raw.get("rationale", "provider interpretation validated by requirements agent"),
            based_on=["requirement"] + [d.id for d in decisions],
        ))
        return AgentResult(
            outputs={"spec": spec, "clarification_answers": answers},
            decisions=decisions,
            summary=f"spec with {len(acceptance)} acceptance criteria; {len(clarifications)} clarifications",
        )
