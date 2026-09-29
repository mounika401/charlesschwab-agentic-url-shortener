"""Reliability metrics derived from the audit trail.

Metrics are computed *from* the audit log rather than tracked separately, so
they can always be recomputed and can never disagree with the record.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def compute(events: list[dict[str, Any]]) -> dict[str, Any]:
    by_event: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        by_event[e["event"]].append(e)

    started = by_event["run.started"]
    finished = by_event["run.finished"]
    t0 = started[0]["ts"] if started else (events[0]["ts"] if events else 0.0)
    t1 = finished[-1]["ts"] if finished else (events[-1]["ts"] if events else t0)

    attempts = len(by_event["attempt.started"])
    failed_attempts = len(by_event["attempt.failed"])
    nodes_ok = {e["node"] for e in by_event["node.succeeded"]}
    nodes_failed = {e["node"] for e in by_event["node.failed"]} - nodes_ok

    # MTTR: first failure of a node -> the node's next success.
    incidents: list[float] = []
    open_failure: dict[str, float] = {}
    for e in events:
        if e["event"] == "attempt.failed" and e["node"] not in open_failure:
            open_failure[e["node"]] = e["ts"]
        elif e["event"] == "node.succeeded" and e["node"] in open_failure:
            incidents.append(e["ts"] - open_failure.pop(e["node"]))

    stage_latency: dict[str, float] = defaultdict(float)
    for e in by_event["node.succeeded"] + by_event["node.failed"]:
        stage_latency[e["data"].get("stage", "?")] += e["data"].get("duration_s", 0.0)

    approval_waits = [e["data"].get("wait_s", 0.0) for e in by_event["approval.decided"]]
    total_nodes = len(nodes_ok | nodes_failed)

    return {
        "status": finished[-1]["data"].get("status") if finished else "incomplete",
        "end_to_end_latency_s": round(t1 - t0, 3),
        "nodes_total": total_nodes,
        "nodes_succeeded": len(nodes_ok),
        "nodes_failed": len(nodes_failed),
        "node_success_rate": round(len(nodes_ok) / total_nodes, 3) if total_nodes else None,
        "attempts_total": attempts,
        "attempt_success_rate": round((attempts - failed_attempts) / attempts, 3) if attempts else None,
        "retries": len(by_event["retry.scheduled"]),
        "retry_frequency": round(len(by_event["retry.scheduled"]) / attempts, 3) if attempts else 0.0,
        "rollbacks": len(by_event["rollback.performed"]),
        "fallbacks": len(by_event["fallback.used"]),
        "policy_violations": len(by_event["policy.violation"]),
        "replans": len(by_event["replan.triggered"]),
        "approvals": len(by_event["approval.decided"]),
        "approval_wait_s": round(sum(approval_waits), 3),
        "incidents_recovered": len(incidents),
        "incidents_unrecovered": len(open_failure),
        "mttr_s": round(sum(incidents) / len(incidents), 3) if incidents else None,
        "stage_latency_s": {k: round(v, 3) for k, v in sorted(stage_latency.items())},
    }


def append_history(path: Path, run_id: str, scenario: str, metrics: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps({"run_id": run_id, "scenario": scenario, **metrics}, sort_keys=True) + "\n")


def aggregate(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"runs": 0}
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    mttrs = [r["mttr_s"] for r in rows if r.get("mttr_s") is not None]
    attempts = sum(r["attempts_total"] for r in rows)
    return {
        "runs": len(rows),
        "run_success_rate": round(sum(r["status"] == "succeeded" for r in rows) / len(rows), 3),
        "retry_frequency": round(sum(r["retries"] for r in rows) / attempts, 3) if attempts else 0.0,
        "rollbacks_per_run": round(sum(r["rollbacks"] for r in rows) / len(rows), 3),
        "mean_mttr_s": round(sum(mttrs) / len(mttrs), 3) if mttrs else None,
        "mean_end_to_end_latency_s": round(sum(r["end_to_end_latency_s"] for r in rows) / len(rows), 3),
        "by_scenario": {
            s: sum(1 for r in rows if r["scenario"] == s) for s in sorted({r["scenario"] for r in rows})
        },
    }
