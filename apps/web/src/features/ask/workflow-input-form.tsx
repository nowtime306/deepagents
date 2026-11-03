"use client";

import { useEffect, useState } from "react";

export type InputSchema = {
  type: string;
  title?: string;
  description?: string;
  properties?: Record<string, InputSchema>;
  required?: string[];
  items?: InputSchema;
  enum?: unknown[];
  minLength?: number;
  maxLength?: number;
  minimum?: number;
  maximum?: number;
  minItems?: number;
  maxItems?: number;
};

function initial(schema: InputSchema): unknown {
  if (schema.enum?.length) return schema.enum[0];
  if (schema.type === "object")
    return Object.fromEntries(
      (schema.required || []).map((key) => [
        key,
        initial(schema.properties![key]),
      ]),
    );
  if (schema.type === "array") return [];
  if (schema.type === "boolean") return false;
  if (schema.type === "null") return null;
  if (schema.type === "number" || schema.type === "integer")
    return schema.minimum ?? 0;
  return "";
}

function equal(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (!left || !right || typeof left !== "object" || typeof right !== "object")
    return false;
  if (Array.isArray(left) || Array.isArray(right))
    return (
      Array.isArray(left) &&
      Array.isArray(right) &&
      left.length === right.length &&
      left.every((item, index) => equal(item, right[index]))
    );
  const a = left as Record<string, unknown>,
    b = right as Record<string, unknown>;
  return (
    Object.keys(a).length === Object.keys(b).length &&
    Object.keys(a).every(
      (key) => Object.hasOwn(b, key) && equal(a[key], b[key]),
    )
  );
}

function invalid(schema: InputSchema, value: unknown, path = "输入"): string {
  if (schema.type === "object") {
    if (!value || typeof value !== "object" || Array.isArray(value))
      return `${path}需要对象`;
    const record = value as Record<string, unknown>;
    for (const key of schema.required || [])
      if (!(key in record)) return `${path}缺少必填字段：${key}`;
    for (const [key, item] of Object.entries(record)) {
      const child =
        schema.properties && Object.hasOwn(schema.properties, key)
          ? schema.properties[key]
          : undefined;
      if (!child) return `${path}包含未声明字段：${key}`;
      const error = invalid(child, item, `${path} / ${child.title || key}`);
      if (error) return error;
    }
  } else if (schema.type === "array") {
    if (!Array.isArray(value)) return `${path}需要数组`;
    if (value.length > 30) return `${path}超过30项，请切换JSON输入处理较大数组`;
    if (
      value.length < (schema.minItems ?? 0) ||
      value.length > (schema.maxItems ?? Infinity)
    )
      return `${path}的项数不符合要求`;
    for (const [index, item] of value.entries()) {
      const error = invalid(schema.items!, item, `${path} / 第${index + 1}项`);
      if (error) return error;
    }
  } else if (schema.type === "string") {
    if (typeof value !== "string") return `${path}需要文本`;
    if (
      [...value].length < (schema.minLength ?? 0) ||
      [...value].length > (schema.maxLength ?? Infinity)
    )
      return `${path}的文字长度不符合要求`;
  } else if (schema.type === "number" || schema.type === "integer") {
    if (
      typeof value !== "number" ||
      !Number.isFinite(value) ||
      (schema.type === "integer" && !Number.isInteger(value))
    )
      return `${path}需要${schema.type === "integer" ? "整数" : "有限数字"}`;
    if (
      value < (schema.minimum ?? -Infinity) ||
      value > (schema.maximum ?? Infinity)
    )
      return `${path}的数值超出范围`;
  } else if (schema.type === "boolean" && typeof value !== "boolean")
    return `${path}需要选择是或否`;
  else if (schema.type === "null" && value !== null) return `${path}只能为空值`;
  if (schema.enum && !schema.enum.some((item) => equal(item, value)))
    return `${path}不在可选值中`;
  return "";
}

function InputField({
  schema,
  value,
  change,
  name,
}: {
  schema: InputSchema;
  value: unknown;
  change: (value: unknown) => void;
  name: string;
}) {
  if (schema.enum)
    return (
      <label>
        {name}
        <select
          aria-label={name}
          value={schema.enum.findIndex((item) => equal(item, value))}
          onChange={(event) => change(schema.enum![Number(event.target.value)])}
        >
          <option
            value={-1}
            disabled
          >
            请选择
          </option>
          {schema.enum.map((item, index) => (
            <option
              key={index}
              value={index}
            >
              {typeof item === "string" ? item : JSON.stringify(item)}
            </option>
          ))}
        </select>
      </label>
    );
  if (schema.type === "object") {
    const record =
      value && typeof value === "object" && !Array.isArray(value)
        ? (value as Record<string, unknown>)
        : {};
    return (
      <fieldset className="workflow-input-object">
        <legend>{name}</legend>
        {Object.entries(schema.properties || {}).map(([key, child]) => {
          const required = schema.required?.includes(key);
          const label = `${name} / ${child.title || key}`;
          return (
            <div key={key}>
              {!required && (
                <label className="workflow-optional">
                  <input
                    type="checkbox"
                    checked={key in record}
                    onChange={(event) => {
                      const next = { ...record };
                      if (event.target.checked) next[key] = initial(child);
                      else delete next[key];
                      change(next);
                    }}
                  />
                  启用{child.title || key}
                </label>
              )}
              {(required || key in record) && (
                <>
                  <InputField
                    schema={child}
                    value={record[key]}
                    name={label}
                    change={(item) => change({ ...record, [key]: item })}
                  />
                  {required && <small>必填</small>}
                  {child.description && <small>{child.description}</small>}
                </>
              )}
            </div>
          );
        })}
      </fieldset>
    );
  }
  if (schema.type === "array") {
    const values = Array.isArray(value) ? value : [];
    return (
      <fieldset className="workflow-input-array">
        <legend>{name}</legend>
        <small>
          至少{schema.minItems ?? 0}项
          {schema.maxItems !== undefined ? `，最多${schema.maxItems}项` : ""}
          ；表单最多显示30项，较大数组可切换JSON输入。
        </small>
        {values.slice(0, 30).map((item, index) => (
          <div key={index}>
            <InputField
              schema={schema.items!}
              value={item}
              name={`${name} / 第${index + 1}项`}
              change={(next) =>
                change(
                  values.map((old, position) =>
                    position === index ? next : old,
                  ),
                )
              }
            />
            <button
              type="button"
              aria-label={`删除${name}第${index + 1}项`}
              onClick={() =>
                change(values.filter((_, position) => position !== index))
              }
            >
              删除此项
            </button>
          </div>
        ))}
        <button
          type="button"
          disabled={values.length >= Math.min(30, schema.maxItems ?? 30)}
          onClick={() => change([...values, initial(schema.items!)])}
        >
          添加{name}项
        </button>
      </fieldset>
    );
  }
  if (schema.type === "boolean")
    return (
      <label>
        {name}
        <select
          aria-label={name}
          value={typeof value === "boolean" ? String(value) : ""}
          onChange={(event) => change(event.target.value === "true")}
        >
          <option
            value=""
            disabled
          >
            请选择
          </option>
          <option value="true">是</option>
          <option value="false">否</option>
        </select>
      </label>
    );
  if (schema.type === "null") return <p>{name}：空值</p>;
  if (schema.type === "number" || schema.type === "integer")
    return (
      <label>
        {name}
        <input
          aria-label={name}
          type="number"
          min={schema.minimum}
          max={schema.maximum}
          step={schema.type === "integer" ? 1 : "any"}
          value={typeof value === "number" ? value : ""}
          onChange={(event) =>
            change(
              event.target.value === "" ? null : Number(event.target.value),
            )
          }
        />
        <small>
          {schema.type === "integer" ? "整数" : "数字"}
          {schema.minimum !== undefined ? `，最小${schema.minimum}` : ""}
          {schema.maximum !== undefined ? `，最大${schema.maximum}` : ""}
        </small>
      </label>
    );
  return (
    <label>
      {name}
      <input
        aria-label={name}
        value={typeof value === "string" ? value : ""}
        maxLength={16000}
        onChange={(event) => change(event.target.value)}
      />
      <small>
        {schema.minLength !== undefined ? `至少${schema.minLength}字` : ""}
        {schema.maxLength !== undefined ? `，最多${schema.maxLength}字` : ""}
      </small>
    </label>
  );
}

export function WorkflowInputForm({
  schema,
  text,
  change,
  onInvalid,
  disabled,
}: {
  schema: InputSchema;
  text: string;
  change: (text: string) => void;
  onInvalid: (invalid: boolean) => void;
  disabled: boolean;
}) {
  const structured = schema.type !== "string" || !!schema.enum;
  const [form, setForm] = useState(structured);
  let value: unknown;
  let error: string;
  let parsed = true;
  try {
    value = schema.type === "string" ? text : JSON.parse(text);
    error = invalid(schema, value);
  } catch {
    parsed = false;
    error = "现有内容不是有效JSON。可切换JSON输入修正，或明确按结构新建。";
  }
  if (text.length > 16000) error = "测试输入最多16000字符，请缩短内容";
  const blocked = structured && form && !!error;
  useEffect(() => {
    onInvalid(blocked);
  }, [blocked, onInvalid]);
  function update(value: unknown) {
    change(
      schema.type === "string" ? String(value) : JSON.stringify(value, null, 2),
    );
  }
  return (
    <fieldset
      className="workflow-input-form"
      disabled={disabled}
    >
      {structured && (
        <div className="workflow-input-modes">
          <button
            type="button"
            aria-pressed={form}
            onClick={() => setForm(true)}
          >
            表单输入
          </button>
          <button
            type="button"
            aria-pressed={!form}
            onClick={() => setForm(false)}
          >
            JSON输入
          </button>
          <button
            type="button"
            onClick={() => {
              if (
                !text.trim() ||
                window.confirm("按当前结构新建输入？现有测试输入将被替换。")
              )
                update(initial(schema));
            }}
          >
            按结构新建输入
          </button>
        </div>
      )}
      {structured && form ? (
        <>
          {parsed && (
            <InputField
              schema={schema}
              value={value}
              name={schema.title || "输入"}
              change={update}
            />
          )}
          {error && <p role="alert">{error}</p>}
        </>
      ) : (
        <label>
          测试输入
          <textarea
            aria-label="测试输入"
            required
            maxLength={16000}
            value={text}
            onChange={(event) => change(event.target.value)}
          />
        </label>
      )}
    </fieldset>
  );
}
