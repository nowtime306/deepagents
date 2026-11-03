"""Unknown denominators remain unknown; document/chunk revisions determine evidence matches."""

from app.evaluation_metrics import question_result, summary


def test_revision_and_chunk_annotations_determine_retrieval_metrics():
    question = {
        "id": "q1",
        "question": "资料",
        "category": "scanned",
        "unanswerable": False,
        "expected": [{"document_id": "d", "revision": 2, "chunk_ids": ["c2"]}],
    }
    wrong = question_result(
        question, {"mode": "keyword", "items": [{"document_id": "d", "document_revision": 1, "chunk_id": "c1"}]}, 5
    )
    assert wrong["recall_at_10"] == 0 and wrong["evidence_precision"] == 0
    correct = question_result(
        question, {"mode": "hybrid", "items": [{"document_id": "d", "document_revision": 2, "chunk_id": "c2"}]}, 10
    )
    assert correct["recall_at_10"] == 1 and correct["evidence_precision"] == 1
    metrics = summary([correct], 2)
    assert not metrics["complete"] and metrics["categories"]["scanned"]["recall_at_10"] == 1
    assert metrics["citation_correctness"] is None and metrics["answer_refusal_accuracy"] is None
    assert metrics["categories"]["no_answer"]["recall_at_10"] is None


def test_empty_and_negative_only_datasets_do_not_invent_quality_metrics():
    metrics = summary([], 2)
    assert metrics["recall_at_10"] is None and metrics["duration_ms"] is None
    negative = question_result(
        {"id": "n", "question": "未知", "category": "no_answer", "unanswerable": True, "expected": []},
        {"mode": "keyword", "items": []},
        2,
    )
    metrics = summary([negative], 1)
    assert metrics["recall_at_10"] is None and metrics["no_answer_correct"] == 1 and metrics["complete"]
