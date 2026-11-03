"use client";

import { useEffect, useState } from "react";
import type { WorkflowDefinition } from "./workflow-editor";

export function WorkflowSchemaEditor({
  definition,
  onChange,
  onInvalid,
}: {
  definition: WorkflowDefinition;
  onChange: (value: WorkflowDefinition) => void;
  onInvalid: (value: boolean) => void;
}) {
  const [input, setInput] = useState(() =>
    JSON.stringify(definition.input_schema || { type: "string" }, null, 2),
  );
  const [output, setOutput] = useState(() =>
    JSON.stringify(definition.output_schema || { type: "string" }, null, 2),
  );
  const [previous, setPrevious] = useState({
    input: definition.input_schema,
    output: definition.output_schema,
  });
  const [error, setError] = useState("");
  if (
    previous.input !== definition.input_schema ||
    previous.output !== definition.output_schema
  ) {
    setPrevious({
      input: definition.input_schema,
      output: definition.output_schema,
    });
    setInput(
      JSON.stringify(definition.input_schema || { type: "string" }, null, 2),
    );
    setOutput(
      JSON.stringify(definition.output_schema || { type: "string" }, null, 2),
    );
    setError("");
  }
  useEffect(() => {
    onInvalid(Boolean(error));
  }, [error, onInvalid]);
  function update(nextInput: string, nextOutput: string) {
    setInput(nextInput);
    setOutput(nextOutput);
    try {
      const incoming = JSON.parse(nextInput),
        outgoing = JSON.parse(nextOutput);
      if (
        !incoming ||
        !outgoing ||
        typeof incoming.type !== "string" ||
        typeof outgoing.type !== "string"
      )
        throw new Error("输入与输出必须是包含 type 的 JSON Schema 对象");
      setError("");
      setPrevious({ input: incoming, output: outgoing });
      onChange({
        ...definition,
        input_schema: incoming,
        output_schema: outgoing,
      });
    } catch {
      setError("请填写有效的 JSON Schema；完整类型和范围由服务端校验。");
    }
  }
  return (
    <details>
      <summary>输入输出结构与共享预算</summary>
      <p>
        文本类型直接输入文字；对象、数组等类型在测试输入中填写
        JSON。对象只接受声明的字段，最多六层。不支持远程引用或表达式。
      </p>
      <label>
        输入 Schema
        <textarea
          aria-label="输入 Schema"
          maxLength={12000}
          value={input}
          onChange={(e) => update(e.target.value, output)}
        />
      </label>
      <label>
        输出 Schema
        <textarea
          aria-label="输出 Schema"
          maxLength={12000}
          value={output}
          onChange={(e) => update(input, e.target.value)}
        />
      </label>
      {error && <p role="alert">{error}</p>}
      <label>
        共享模型调用上限
        <input
          type="number"
          aria-label="共享模型调用上限"
          min={1}
          max={100}
          value={definition.model_calls || 20}
          onChange={(e) =>
            onChange({ ...definition, model_calls: Number(e.target.value) })
          }
        />
      </label>
      <label>
        共享 Token 上限
        <input
          type="number"
          aria-label="共享 Token 上限"
          min={1000}
          max={1000000}
          value={definition.token_limit || 128000}
          onChange={(e) =>
            onChange({ ...definition, token_limit: Number(e.target.value) })
          }
        />
      </label>
      <small>
        子流程与并行分支共享根流程的预算；输入估算和输出预留都会先占用预算。
      </small>
    </details>
  );
}
