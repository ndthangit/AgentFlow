import { useMemo } from "react";

import type { SchemaType } from "./types";

const emptyObjectSchema = {
  type: "object",
  properties: {},
  additionalProperties: false,
};

const schemaTypes: { value: SchemaType; label: string }[] = [
  { value: "string", label: "Văn bản (string)" },
  { value: "number", label: "Số (number)" },
  { value: "integer", label: "Số nguyên (integer)" },
  { value: "boolean", label: "Đúng / sai (boolean)" },
  { value: "object", label: "Đối tượng (object)" },
  { value: "array", label: "Danh sách (array)" },
];

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function schemaForType(type: SchemaType): Record<string, unknown> {
  if (type === "object") return { type, properties: {}, additionalProperties: false };
  if (type === "array") return { type, items: { type: "string" } };
  return { type };
}

type SchemaBuilderProps = {
  label: string;
  value: string;
  onChange: (value: string) => void;
};

export function SchemaBuilder({ label, value, onChange }: SchemaBuilderProps) {
  const parsed = useMemo<{ schema: Record<string, unknown>; error?: string }>(() => {
    try {
      const schema: unknown = JSON.parse(value);
      return isRecord(schema)
        ? { schema }
        : { schema: emptyObjectSchema, error: "Schema phải là một JSON object." };
    } catch {
      return { schema: emptyObjectSchema, error: "JSON nâng cao chưa hợp lệ." };
    }
  }, [value]);
  const properties = isRecord(parsed.schema.properties) ? parsed.schema.properties : {};
  const required = Array.isArray(parsed.schema.required)
    ? parsed.schema.required.filter((item): item is string => typeof item === "string")
    : [];

  function commit(nextProperties: Record<string, unknown>, nextRequired = required) {
    const nextSchema: Record<string, unknown> = {
      ...parsed.schema,
      type: "object",
      properties: nextProperties,
      additionalProperties: false,
    };
    if (nextRequired.length) nextSchema.required = nextRequired;
    else delete nextSchema.required;
    onChange(JSON.stringify(nextSchema, null, 2));
  }

  function addField() {
    let suffix = Object.keys(properties).length + 1;
    let name = `field_${suffix}`;
    while (name in properties) name = `field_${++suffix}`;
    commit({ ...properties, [name]: { type: "string" } });
  }

  function renameField(currentName: string, nextName: string) {
    if (nextName !== currentName && nextName in properties) return;
    const nextProperties: Record<string, unknown> = {};
    for (const [name, definition] of Object.entries(properties)) {
      nextProperties[name === currentName ? nextName : name] = definition;
    }
    commit(
      nextProperties,
      required.map((name) => name === currentName ? nextName : name),
    );
  }

  function setFieldType(name: string, type: SchemaType) {
    commit({ ...properties, [name]: schemaForType(type) });
  }

  function setArrayItemType(name: string, type: SchemaType) {
    const definition = isRecord(properties[name]) ? properties[name] : {};
    commit({ ...properties, [name]: { ...definition, items: schemaForType(type) } });
  }

  function setRequired(name: string, checked: boolean) {
    commit(
      properties,
      checked ? [...new Set([...required, name])] : required.filter((item) => item !== name),
    );
  }

  function removeField(name: string) {
    const nextProperties = { ...properties };
    delete nextProperties[name];
    commit(nextProperties, required.filter((item) => item !== name));
  }

  return (
    <section className="schema-builder">
      <div className="schema-builder-heading">
        <div><strong>{label}</strong><span>Khai báo các trường dữ liệu JSON</span></div>
        <button type="button" onClick={addField}>+ Thêm trường</button>
      </div>
      <div className="schema-field-list">
        {Object.entries(properties).map(([name, rawDefinition], index) => {
          const definition = isRecord(rawDefinition) ? rawDefinition : {};
          const type = schemaTypes.some((item) => item.value === definition.type)
            ? definition.type as SchemaType
            : "string";
          const items = isRecord(definition.items) ? definition.items : {};
          const itemType = schemaTypes.some((item) => item.value === items.type)
            ? items.type as SchemaType
            : "string";
          return (
            <div className={type === "array" ? "schema-field with-items" : "schema-field"} key={index}>
              <label>Tên trường<input value={name} placeholder="Ví dụ: email" onChange={(event) => renameField(name, event.target.value)} /></label>
              <label>Kiểu dữ liệu<select value={type} onChange={(event) => setFieldType(name, event.target.value as SchemaType)}>{schemaTypes.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}</select></label>
              {type === "array" && (
                <label>Kiểu phần tử<select value={itemType} onChange={(event) => setArrayItemType(name, event.target.value as SchemaType)}>{schemaTypes.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}</select></label>
              )}
              <label className="schema-required"><input type="checkbox" checked={required.includes(name)} onChange={(event) => setRequired(name, event.target.checked)} /> Bắt buộc</label>
              <button type="button" className="schema-remove" aria-label={`Xóa trường ${name}`} onClick={() => removeField(name)}>Xóa</button>
            </div>
          );
        })}
        {!Object.keys(properties).length && <p className="schema-empty">Chưa có trường dữ liệu. Chọn “Thêm trường” để bắt đầu.</p>}
      </div>
      <details className="schema-advanced">
        <summary>JSON Schema nâng cao</summary>
        <textarea className="schema-textarea" value={value} spellCheck={false} onChange={(event) => onChange(event.target.value)} />
        {parsed.error && <p>{parsed.error}</p>}
      </details>
    </section>
  );
}
