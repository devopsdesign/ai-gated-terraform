#!/usr/bin/env python3
"""Review a Terraform plan before a human approves it.

Two layers:
1. Deterministic rules decide the risk level (low / medium / high). They never
   depend on the language model, so the gate behaves the same with or without it.
2. An optional language model writes a short plain-English summary for the
   reviewer. It only sees resource addresses and actions, never attribute values,
   so secrets in the plan are not sent anywhere.

The script never approves anything. It writes a Markdown report for the pull
request and exposes the risk level so the workflow can require extra reviewers.

Usage:
    python scripts/ai_review.py plan.json --out review.md
Environment (all optional):
    LLM_API_URL   OpenAI-compatible chat completions endpoint
    LLM_API_KEY   API key for that endpoint
    LLM_MODEL     model name
    GITHUB_OUTPUT set by GitHub Actions; the risk level is written there
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from dataclasses import dataclass, field

# Resource types where any change deserves a second look.
SENSITIVE_TYPES = (
    "aws_iam_", "google_project_iam", "azurerm_role_assignment",
    "aws_security_group", "aws_kms_", "aws_s3_bucket_policy",
    "aws_db_instance", "aws_rds_cluster",
)
MANY_CHANGES = 20


@dataclass
class Change:
    address: str
    rtype: str
    actions: list[str]

    @property
    def kind(self) -> str:
        a = self.actions
        if a == ["no-op"] or a == ["read"]:
            return "no-op"
        if "delete" in a and "create" in a:
            return "replace"
        if a == ["delete"]:
            return "delete"
        if a == ["create"]:
            return "create"
        return "update"


@dataclass
class Review:
    changes: list[Change]
    risk: str = "low"
    reasons: list[str] = field(default_factory=list)
    summary: str | None = None


def load_changes(plan: dict) -> list[Change]:
    out = []
    for rc in plan.get("resource_changes", []):
        ch = Change(rc["address"], rc["type"], rc["change"]["actions"])
        if ch.kind != "no-op":
            out.append(ch)
    return out


def assess(changes: list[Change]) -> Review:
    r = Review(changes)
    level = 0

    def bump(n: int, why: str) -> None:
        nonlocal level
        level = max(level, n)
        r.reasons.append(why)

    for c in changes:
        if c.kind in ("delete", "replace"):
            bump(2, f"`{c.address}` will be {'destroyed' if c.kind == 'delete' else 'replaced'}.")
        if c.rtype.startswith(SENSITIVE_TYPES):
            bump(2, f"`{c.address}` is an access or data resource ({c.rtype}).")
    if len(changes) > MANY_CHANGES:
        bump(1, f"{len(changes)} resources change in one plan; consider splitting it.")
    if changes and level == 0:
        bump(0, "Only creates and in-place updates on non-sensitive resources.")
    r.risk = ["low", "medium", "high"][level]
    return r


def llm_summary(changes: list[Change]) -> str | None:
    url, key, model = (os.environ.get(k) for k in ("LLM_API_URL", "LLM_API_KEY", "LLM_MODEL"))
    if not (url and key and model) or not changes:
        return None
    listing = "\n".join(f"- {c.kind}: {c.address}" for c in changes)
    body = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": (
                "You review Terraform plans for an on-call SRE. In at most 5 short "
                "bullet points, say what this change does and what could go wrong. "
                "Do not approve or reject; a human decides.")},
            {"role": "user", "content": listing},
        ],
    }
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.load(resp)
        return data["choices"][0]["message"]["content"].strip()
    except Exception as exc:  # the gate must not fail because the model is down
        return f"_Summary unavailable ({type(exc).__name__}); rule-based review still applies._"


def render(r: Review) -> str:
    icon = {"low": "🟢", "medium": "🟡", "high": "🔴"}[r.risk]
    counts: dict[str, int] = {}
    for c in r.changes:
        counts[c.kind] = counts.get(c.kind, 0) + 1
    lines = [f"## Plan review: {icon} {r.risk.upper()} risk", ""]
    if not r.changes:
        lines.append("No changes. Nothing to approve.")
        return "\n".join(lines) + "\n"
    lines.append("**Changes:** " + ", ".join(f"{n} {k}" for k, n in sorted(counts.items())))
    lines += ["", "**Why this risk level** (rule-based):"] + [f"- {x}" for x in r.reasons]
    if r.summary:
        lines += ["", "**Summary for the reviewer** (generated, verify against the plan):", r.summary]
    lines += ["", "| Action | Resource |", "|---|---|"]
    lines += [f"| {c.kind} | `{c.address}` |" for c in r.changes]
    lines += ["", "_Apply runs only after a person approves the `production` environment._"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("plan_json")
    ap.add_argument("--out", default="review.md")
    ap.add_argument("--no-llm", action="store_true")
    a = ap.parse_args(argv)

    with open(a.plan_json) as f:
        review = assess(load_changes(json.load(f)))
    if not a.no_llm:
        review.summary = llm_summary(review.changes)

    text = render(review)
    with open(a.out, "w") as f:
        f.write(text)
    if gh := os.environ.get("GITHUB_OUTPUT"):
        with open(gh, "a") as f:
            f.write(f"risk={review.risk}\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
