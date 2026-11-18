"""Regression test from docs/trace_case.md: seeded_regression C-002@11, first wrong step = event 3
`model_override {"delay_days": 5}` (FM-03, ticket trusted over DB delay_days=2)."""
import pytest

pytestmark = pytest.mark.student


def _ctx(trial, label):
    from pydantic_evals.evaluators import EvaluatorContext

    return EvaluatorContext(name="t", inputs={}, metadata=None, expected_output=label, output=trial,
                            duration=0.0, _span_tree=None, attributes={}, metrics={})


def _grade(shape: str, claim: str, seed: int):
    from lab9 import graders
    from lab9.trace_case import investigate

    case = investigate(shape, claim, seed)
    t, label = case["trial"], case["label"]
    return t, {
        "db_fact_kept": graders.DbFactKept().evaluate(_ctx(t, label)).value,
        "refund_amount_correct": graders.RefundAmountCorrect().evaluate(_ctx(t, label)).value,
        "approval_bypass": graders.ApprovalBypass().evaluate(_ctx(t, label)).value,
    }


def test_lab9_trace_case_c002_seeded_regression_is_caught():
    t, g = _grade("seeded_regression", "C-002", 11)
    kinds = [e["kind"] for e in t["events"]]
    assert kinds.index("model_override") == 2                 # event 3: the first wrong step
    assert t["events"][2]["payload"] == {"delay_days": 5}
    assert g["db_fact_kept"] is False                         # step-level grader sees the override
    assert g["refund_amount_correct"] is False                # outcome: 20.000đ written, label 0
    assert t["snapshot"]["refund_vnd"] == 20000


def test_lab9_trace_case_c002_fixed_is_clean():
    t, g = _grade("fixed", "C-002", 11)
    assert "model_override" not in [e["kind"] for e in t["events"]]
    assert g == {"db_fact_kept": True, "refund_amount_correct": True, "approval_bypass": False}
    assert t["snapshot"]["refund_vnd"] == 0
