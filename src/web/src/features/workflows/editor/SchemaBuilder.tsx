import { useMemo } from "react";

import type { SchemaType } from "./types";

const emptyObjectSchema = {
  type: "object",
  properties: {},
  additionalProperties: false,
};

const schemaTypes: { value: SchemaType; label: string }[] = [
  { value: "string", label: "Text (string)" },
  { value: "number", label: "Number" },
  { value: "integer", label: "Integer" },
  { value: "boolean", label: "True / false (boolean)" },
  { value: "object", label: "Object" },
  { value: "array", label: "List (array)" },
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
        : { schema: emptyObjectSchema, error: "The schema must be a JSON object." };
    } catch {
      return { schema: emptyObjectSchema, error: "The advanced JSON is invalid." };
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
        <div><strong>{label}</strong><span>Define the JSON data fields</span></div>
        <button type="button" onClick={addField}>+ Add field</button>
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
              <label>Field name<input value={name} placeholder="Example: email" onChange={(event) => renameField(name, event.target.value)} /></label>
              <label>Data type<select value={type} onChange={(event) => setFieldType(name, event.target.value as SchemaType)}>{schemaTypes.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}</select></label>
              {type === "array" && (
                <label>Item type<select value={itemType} onChange={(event) => setArrayItemType(name, event.target.value as SchemaType)}>{schemaTypes.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}</select></label>
              )}
              <label className="schema-required"><input type="checkbox" checked={required.includes(name)} onChange={(event) => setRequired(name, event.target.checked)} /> Required</label>
              <button type="button" className="schema-remove" aria-label={`Remove field ${name}`} onClick={() => removeField(name)}>Remove</button>
            </div>
          );
        })}
        {!Object.keys(properties).length && <p className="schema-empty">No data fields yet. Select “Add field” to begin.</p>}
      </div>
      <details className="schema-advanced">
        <summary>Advanced JSON Schema</summary>
        <textarea className="schema-textarea" value={value} spellCheck={false} onChange={(event) => onChange(event.target.value)} />
        {parsed.error && <p>{parsed.error}</p>}
      </details>
    </section>
  );
}
