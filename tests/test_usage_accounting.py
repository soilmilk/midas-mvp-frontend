#!/usr/bin/env python3
"""Focused, network-free tests for provider usage extraction and aggregation."""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from midas.agents import LLMResult, _call
from midas.loop import _account_llm_call, _usage_event, _usage_totals
from midas.models import ProofRunState


def check(label, condition):
    print(f"{label:<58}{'PASS' if condition else 'FAIL'}")
    return bool(condition)


usage = SimpleNamespace(
    prompt_tokens=120,
    completion_tokens=35,
    total_tokens=155,
    completion_tokens_details=SimpleNamespace(reasoning_tokens=11),
    prompt_tokens_details=SimpleNamespace(cached_tokens=40),
    cost=0.0125,
)
response = SimpleNamespace(
    usage=usage,
    model="resolved/reasoner",
    choices=[SimpleNamespace(message=SimpleNamespace(content=" answer "))],
)
submitted = {}


def create_completion(**kwargs):
    submitted.update(kwargs)
    return response


client = SimpleNamespace(
    chat=SimpleNamespace(
        completions=SimpleNamespace(create=create_completion)
    )
)
with patch("midas.agents._client", return_value=client):
    extracted = _call("requested/reasoner", "prompt", 100,
                      reasoning_effort="low")

state = ProofRunState(
    problem_id="usage",
    informal_problem_path="informal.md",
    context_path="context.lean",
    initial_body_path="body.lean",
)
_account_llm_call(state, "reasoner", extracted)
translated = LLMResult(
    prompt="translate",
    text="lean",
    model="resolved/translator",
    prompt_tokens=200,
    completion_tokens=50,
    total_tokens=250,
    cached_tokens=25,
    cost_credits=0.02,
)
_account_llm_call(state, "translator", translated)
_account_llm_call(state, "translator")  # failed call: count it, estimate nothing
reviewed = LLMResult(
    prompt="review", text="ALIGNED", model="resolved/reviewer",
    prompt_tokens=80, completion_tokens=20, total_tokens=100,
    cost_credits=0.01,
)
_account_llm_call(state, "reviewer", reviewed)

total = state.stats.llm_usage.total
reasoner = state.stats.llm_usage.reasoner
translator = state.stats.llm_usage.translator
reviewer = state.stats.llm_usage.reviewer
legacy = ProofRunState.model_validate({
    "problem_id": "legacy",
    "informal_problem_path": "informal.md",
    "context_path": "context.lean",
    "initial_body_path": "body.lean",
    "stats": {"total_llm_calls": 4},
})

checks = [
    check("reasoning effort uses OpenRouter unified request body", (
        submitted.get("extra_body") == {"reasoning": {"effort": "low"}}
    )),
    check("OpenRouter usage fields extracted", (
        extracted.text == "answer" and extracted.model == "resolved/reasoner" and
        extracted.prompt_tokens == 120 and extracted.completion_tokens == 35 and
        extracted.total_tokens == 155 and extracted.reasoning_tokens == 11 and
        extracted.cached_tokens == 40 and extracted.cost_credits == 0.0125
    )),
    check("overall tokens and cost aggregate exactly", (
        state.stats.total_llm_calls == 4 and total.calls == 4 and
        total.calls_with_token_usage == 3 and total.calls_with_cost == 3 and
        total.prompt_tokens == 400 and total.completion_tokens == 105 and
        total.total_tokens == 505 and total.reasoning_tokens == 11 and
        total.cached_tokens == 65 and abs(total.cost_credits - 0.0425) < 1e-12
    )),
    check("reasoner and translator subtotals stay separate", (
        reasoner.calls == 1 and reasoner.total_tokens == 155 and
        translator.calls == 2 and translator.calls_with_cost == 1 and
        translator.total_tokens == 250 and reviewer.calls == 1 and
        reviewer.total_tokens == 100
    )),
    check("per-call log rendering includes provider accounting", (
        "model=resolved/reasoner" in _usage_event("reasoner", extracted) and
        "reasoning_tokens=11" in _usage_event("reasoner", extracted) and
        "cost_credits=0.0125" in _usage_event("reasoner", extracted)
    )),
    check("totals rendering exposes accounting coverage", (
        "accounted_token_calls=3/4" in _usage_totals(total) and
        "accounted_cost_calls=3/4" in _usage_totals(total) and
        "cost_credits=0.0425" in _usage_totals(total)
    )),
    check("legacy state defaults new usage structure", (
        legacy.stats.total_llm_calls == 4 and
        legacy.stats.llm_usage.total.calls == 4 and
        legacy.stats.llm_usage.total.calls_with_cost == 0 and
        legacy.stats.llm_usage.total.cost_credits == 0
    )),
]

print(f"\nUSAGE ACCOUNTING: {'PASS' if all(checks) else 'FAIL'}")
sys.exit(0 if all(checks) else 1)
