"""Evidence retrieval evaluation; never infer answer quality from retrieval scores."""

import statistics
import time


async def evaluate(search, questions: list[dict], document_ids: dict[str, str]) -> dict:
    details, elapsed = [], []
    for question in questions:
        started = time.perf_counter()
        result = await search(question["question"])
        duration = (time.perf_counter() - started) * 1000
        elapsed.append(duration)
        expected = {document_ids[key] for key in question["expected_documents"]}
        retrieved = {str(item["document_id"]) for item in result["items"]}
        recall = len(expected & retrieved) / len(expected) if expected else None
        relevant = sum(str(item["document_id"]) in expected for item in result["items"])
        details.append(
            {
                "id": question["id"],
                "category": question["category"],
                "recall_at_10": recall,
                "full_evidence": expected <= retrieved if expected else None,
                "evidence_precision": relevant / len(result["items"]) if result["items"] and expected else None,
                "no_answer_correct": not result["items"] if not expected else None,
                "retrieved": len(result["items"]),
                "duration_ms": round(duration, 2),
            }
        )
    answerable = [row for row in details if row["recall_at_10"] is not None]
    negatives = [row for row in details if row["no_answer_correct"] is not None]
    return {
        "dataset_type": "synthetic_regression",
        "mode": "keyword_without_external_models",
        "questions": len(questions),
        "recall_at_10": statistics.mean(row["recall_at_10"] for row in answerable),
        "full_evidence_coverage": statistics.mean(row["full_evidence"] for row in answerable),
        "no_answer_retrieval_accuracy": statistics.mean(row["no_answer_correct"] for row in negatives),
        "citation_correctness": None,
        "answer_refusal_accuracy": None,
        "limitations": "未调用真实向量、重排或生成模型；检索召回不能代替生成答案的引用正确率与拒答验收。",
        "latency_p50_ms": round(statistics.median(elapsed), 2),
        "latency_p95_ms": round(sorted(elapsed)[int(len(elapsed) * 0.95) - 1], 2),
        "details": details,
    }
