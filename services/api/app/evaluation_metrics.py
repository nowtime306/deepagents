"""Retrieval evidence metrics do not claim generated citation or refusal accuracy."""

import statistics


def question_result(question, result, duration):
    expected = question["expected"]
    matched = {
        item["document_id"]
        for item in expected
        if any(
            row["document_id"] == item["document_id"]
            and row.get("document_revision") == item["revision"]
            and (not item["chunk_ids"] or row["chunk_id"] in item["chunk_ids"])
            for row in result["items"]
        )
    }
    relevant = sum(
        any(
            row["document_id"] == item["document_id"]
            and row.get("document_revision") == item["revision"]
            and (not item["chunk_ids"] or row["chunk_id"] in item["chunk_ids"])
            for item in expected
        )
        for row in result["items"]
    )
    return {
        "id": question["id"],
        "question": question["question"],
        "category": question["category"],
        "recall_at_10": len(matched) / len(expected) if expected else None,
        "evidence_precision": relevant / len(result["items"]) if expected and result["items"] else None,
        "no_answer_correct": not result["items"] if question["unanswerable"] else None,
        "duration_ms": round(duration, 2),
        "mode": result["mode"],
        "warning": result.get("warning"),
        "items": result["items"],
    }


def summary(results, total):
    def average(rows, key):
        values = [row[key] for row in rows if row[key] is not None]
        return statistics.mean(values) if values else None

    def group(rows):
        return {
            "questions": len(rows),
            **{
                key: average(rows, key)
                for key in ("recall_at_10", "evidence_precision", "no_answer_correct", "duration_ms")
            },
        }

    return {
        **group(results),
        "total_questions": total,
        "complete": len(results) == total,
        "citation_correctness": None,
        "answer_refusal_accuracy": None,
        "categories": {
            category: group([row for row in results if row["category"] == category])
            for category in ("fact", "no_answer", "outdated", "conflict", "scanned", "table")
        },
        "limitations": "这是检索实验。生成答案的引用正确率、拒答与真实业务效果须另行人工验收；空指标表示未测。",
    }
