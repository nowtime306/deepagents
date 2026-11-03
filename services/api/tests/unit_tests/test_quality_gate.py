"""Real-data gates need complete human labels; retrieval scores alone cannot pass."""

from app.model_compatibility import capability, matrix
from app.quality_gate import DEFAULTS, verdict


def evidence(kind="user"):
    questions = [{"id": str(n), "unanswerable": n == 49} for n in range(50)]
    results = [{"id": str(n), "recall_at_10": None if n == 49 else 1,
                "no_answer_correct": True if n == 49 else None} for n in range(50)]
    labels = [{"question_id": str(n), "citation_correct": None if n == 49 else True,
               "refusal_correct": True if n == 49 else None} for n in range(50)]
    return {"status": "SUCCEEDED", "dataset_snapshot": {"dataset_type": kind, "questions": questions},
            "results": results}, labels


def test_complete_real_labels_pass_and_synthetic_never_does():
    row, labels = evidence()
    result = verdict(DEFAULTS, row, labels, True)
    assert result["passed"] and result["citation_denominator"] == 49 and result["no_answer_denominator"] == 1
    row["dataset_snapshot"]["dataset_type"] = "synthetic"
    assert not verdict(DEFAULTS, row, labels, True)["passed"]


def test_unknown_partial_and_source_drift_do_not_become_passes():
    row, labels = evidence()
    assert not verdict(DEFAULTS, row, [], True)["passed"]
    labels[0]["citation_correct"] = None
    assert verdict(DEFAULTS, row, labels, True)["metrics"]["citation_correctness"] is None
    _, labels = evidence()
    assert not verdict(DEFAULTS, row, labels, False)["passed"]
    row["results"][-1]["no_answer_correct"] = False
    result = verdict(DEFAULTS, row, labels, True)
    assert not result["passed"] and result["metrics"]["no_answer_retrieval"] == 0


def test_empty_results_or_duplicate_question_ids_cannot_game_gate():
    row, labels = evidence()
    row["results"] = []
    assert not verdict(DEFAULTS, row, labels, True)["passed"]
    row, labels = evidence()
    row["results"][0]["id"] = "1"
    assert "实验结果题号不匹配" in verdict(DEFAULTS, row, labels, True)["reasons"]


def test_protocol_matrix_is_finite_and_not_live_provider_status():
    assert 0 < len(matrix()) < 1000
    assert capability("chat", "anthropic", "anthropic")["state"] == "executable"
    assert capability("embedding", "custom", "custom")["state"] == "configuration_only"
    assert capability("video", "openai", "openai")["state"] == "unsupported"
    assert capability("video", "openai", "dashscope")["state"] == "business_sample"
    assert not capability("text_to_speech", "openai", "openai")["probe_supported"]
    assert "已隐藏" in capability("audio", "openai", "openai")["entry"]
