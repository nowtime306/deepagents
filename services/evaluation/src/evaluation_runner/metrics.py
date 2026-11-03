"""Locked DeepEval public metric integration, without a supplier fallback."""

from .contracts import Check, Result


def tool_correctness(spec: Check, evidence: dict) -> Result:
    if not evidence.get("tools_complete"):
        return Result(registered_id=spec.registered_id, required=spec.required, status="NOT_SCORED",
                      reason="Complete actual tool evidence is unavailable")
    # The CLI installs network/privacy controls before this first DeepEval import.
    from deepeval.metrics import ToolCorrectnessMetric
    from deepeval.models import DeepEvalBaseLLM
    from deepeval.test_case import LLMTestCase, ToolCall, ToolCallParams

    class ControlledJudge(DeepEvalBaseLLM):
        calls = 0

        def load_model(self):
            return self

        def get_model_name(self):
            return "controlled-no-provider"

        def generate(self, prompt, schema=None):
            self.calls += 1
            raise RuntimeError("Judge calls are disabled in deterministic tool evaluation")

        async def a_generate(self, prompt, schema=None):
            return self.generate(prompt, schema)

    parameters = spec.parameters
    if set(parameters) - {"expected_tools", "compare_parameters", "compare_output", "exact", "ordered", "threshold"}:
        raise ValueError("Unsupported tool metric options")
    judge = ControlledJudge()
    fields = []
    if parameters.get("compare_parameters", True):
        fields.append(ToolCallParams.INPUT_PARAMETERS)
    if parameters.get("compare_output", True):
        fields.append(ToolCallParams.OUTPUT)
    metric = ToolCorrectnessMetric(
        model=judge, eval_mode="llm", async_mode=False,
        evaluation_params=fields, should_exact_match=parameters.get("exact", True),
        should_consider_ordering=parameters.get("ordered", False), threshold=parameters.get("threshold", 1.0),
    )
    test_case = LLMTestCase(input=evidence.get("input", "Evaluation"), actual_output="Controlled tool evidence",
                            tools_called=[ToolCall(**tool) for tool in evidence.get("tools", [])],
                            expected_tools=[ToolCall(**tool) for tool in parameters.get("expected_tools", [])])
    score = float(metric.measure(test_case, _show_indicator=False))
    if judge.calls:
        raise RuntimeError("Unexpected judge invocation")
    return Result(registered_id=spec.registered_id, required=spec.required,
                  status="PASS" if metric.is_successful() else "FAIL", score=score,
                  reason="DeepEval 4.3.2 deterministic tool comparison; judge.generate=0 (not semantic quality)")


def score(spec: Check, evidence: dict) -> Result:
    if spec.registered_id == "tool_correctness":
        return tool_correctness(spec, evidence)
    return Result(registered_id=spec.registered_id, required=spec.required, status="NOT_SCORED",
                  reason="No supported complete trajectory/metric adapter")
