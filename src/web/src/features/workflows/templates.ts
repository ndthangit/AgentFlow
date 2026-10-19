export const FILESYSTEM_DEMO_MCP = {
  slug: "filesystem-demo",
  name: "Filesystem MCP demo",
  description: "Read, write, and list files in the isolated MCP container volume.",
  transport: "streamable_http" as const,
  url: "http://filesystem-mcp:8002/mcp",
};

export const starterDraft = {
  nodes: [
    {
      id: "start",
      type: "input.schema",
      name: "Start",
      note: "Define the initial workflow input fields.",
      schema: { type: "object", properties: {}, additionalProperties: false },
    },
    {
      id: "agent",
      type: "agent",
      name: "Agent",
      note: "Describe the task this agent should complete.",
      config: {
        instructions: "",
        skillIds: [],
        mcpServerIds: [],
        inputSchema: { type: "object", properties: {}, additionalProperties: false },
        outputSchema: { type: "object", properties: {}, additionalProperties: false },
      },
    },
    {
      id: "end",
      type: "output.schema",
      name: "End",
      note: "Define the fields returned by the workflow.",
      inputs: {},
      schema: { type: "object", properties: {}, additionalProperties: false },
    },
  ],
  edges: [
    { from: "start", to: "agent", port: "success" },
    { from: "agent", to: "end", port: "success" },
  ],
};

const agentPythonWorkflowDraft = {
  settings: { maxParallelNodes: 2 },
  nodes: [
    {
      id: "start",
      type: "input.schema",
      name: "Start",
      note: "Receive the topic supplied by the user.",
      schema: {
        type: "object",
        properties: { topic: { type: "string" } },
        required: ["topic"],
        additionalProperties: false,
      },
    },
    {
      id: "research_agent",
      type: "agent",
      name: "Research Agent",
      note: "Analyze the topic and create a concise structured draft.",
      inputs: { topic: { from: "$input.topic" } },
      config: {
        runtime: "default",
        instructions: "Analyze the topic in {{input.topic}} and write a concise draft covering the main ideas. Return exactly one JSON object matching the output schema with a single draft string. Do not return Markdown, code fences, or commentary outside the JSON object.",
        skillIds: [],
        mcpServerIds: [],
        inputSchema: {
          type: "object",
          properties: { topic: { type: "string" } },
          required: ["topic"],
          additionalProperties: false,
        },
        outputSchema: {
          type: "object",
          properties: { draft: { type: "string" } },
          required: ["draft"],
          additionalProperties: false,
        },
      },
    },
    {
      id: "parallel_split",
      type: "parallel",
      name: "Parallel Split",
      note: "Send the same draft to the Python formatter and review agent in parallel.",
    },
    {
      id: "python_formatter",
      type: "code.python",
      name: "Python Formatter",
      note: "Normalize whitespace and count the words in the draft.",
      inputs: { draft: { from: "$nodes.parallel_split.output.draft" } },
      config: {
        language: "python",
        code: [
          "def main(inputs):",
          "    cleaned_text = ' '.join(inputs['draft'].split())",
          "    return {",
          "        'cleaned_text': cleaned_text,",
          "        'word_count': len(cleaned_text.split()),",
          "    }",
        ].join("\n"),
        inputSchema: {
          type: "object",
          properties: { draft: { type: "string" } },
          required: ["draft"],
          additionalProperties: false,
        },
        outputSchema: {
          type: "object",
          properties: {
            cleaned_text: { type: "string" },
            word_count: { type: "integer" },
          },
          required: ["cleaned_text", "word_count"],
          additionalProperties: false,
        },
      },
    },
    {
      id: "review_agent",
      type: "agent",
      name: "Review Agent",
      note: "Review the draft while the Python branch formats it.",
      inputs: { draft: { from: "$nodes.parallel_split.output.draft" } },
      config: {
        runtime: "default",
        instructions: "Review the draft in {{input.draft}}. Identify the most important improvement in a concise paragraph. Return exactly one JSON object matching the output schema with a single review string. Do not return Markdown, code fences, or commentary outside the JSON object.",
        skillIds: [],
        mcpServerIds: [],
        inputSchema: {
          type: "object",
          properties: { draft: { type: "string" } },
          required: ["draft"],
          additionalProperties: false,
        },
        outputSchema: {
          type: "object",
          properties: { review: { type: "string" } },
          required: ["review"],
          additionalProperties: false,
        },
      },
    },
    {
      id: "final_agent",
      type: "agent",
      name: "Final Agent",
      note: "Wait for both branches and produce the final answer.",
      inputs: {
        content: { from: "$nodes.python_formatter.output.cleaned_text" },
        word_count: { from: "$nodes.python_formatter.output.word_count" },
        review: { from: "$nodes.review_agent.output.review" },
      },
      config: {
        runtime: "default",
        instructions: "Use the normalized content in {{input.content}} and the review in {{input.review}} to produce a polished final answer in English. Return exactly one JSON object matching the output schema: {\"final_answer\":\"your answer\"}. Do not return Markdown, code fences, arrays, or any text outside the JSON object.",
        skillIds: [],
        mcpServerIds: [],
        inputSchema: {
          type: "object",
          properties: {
            content: { type: "string" },
            word_count: { type: "integer" },
            review: { type: "string" },
          },
          required: ["content", "word_count", "review"],
          additionalProperties: false,
        },
        outputSchema: {
          type: "object",
          properties: { final_answer: { type: "string" } },
          required: ["final_answer"],
          additionalProperties: false,
        },
      },
    },
    {
      id: "end",
      type: "output.schema",
      name: "End",
      note: "Return the completed answer.",
      inputs: { final_answer: { from: "$nodes.final_agent.output.final_answer" } },
      schema: {
        type: "object",
        properties: { final_answer: { type: "string" } },
        required: ["final_answer"],
        additionalProperties: false,
      },
    },
  ],
  edges: [
    { from: "start", to: "research_agent", port: "success" },
    { from: "research_agent", to: "parallel_split", port: "success" },
    { from: "parallel_split", to: "python_formatter", port: "parallel" },
    { from: "parallel_split", to: "review_agent", port: "parallel" },
    { from: "python_formatter", to: "final_agent", port: "success" },
    { from: "review_agent", to: "final_agent", port: "success" },
    { from: "final_agent", to: "end", port: "success" },
  ],
};

export function agentPythonMcpWorkflowDraft(mcpServerId: string) {
  const draft = structuredClone(agentPythonWorkflowDraft);
  const finalAgent = draft.nodes.find((node) => node.id === "final_agent") as
    | { config: { mcpServerIds: string[] } }
    | undefined;
  if (!finalAgent) throw new Error("The final agent is missing from the demo workflow");
  finalAgent.config.mcpServerIds = [mcpServerId];
  return draft;
}

export const sumWorkflowDraft = {
  nodes: [
    {
      id: "input",
      type: "input.schema",
      name: "Number Input",
      note: "Receive num1 and num2 from the run input.",
      schema: {
        type: "object",
        properties: { num1: { type: "number" }, num2: { type: "number" } },
        required: ["num1", "num2"],
        additionalProperties: false,
      },
    },
    {
      id: "sum",
      type: "math.add",
      name: "Add Numbers",
      note: "Calculate num1 + num2 without calling an LLM.",
      inputs: { left: { from: "$input.num1" }, right: { from: "$input.num2" } },
      config: { outputKey: "sum" },
    },
    {
      id: "output",
      type: "output.schema",
      name: "Result",
      note: "Return the calculated sum.",
      inputs: { sum: { from: "$nodes.sum.output.sum" } },
      schema: {
        type: "object",
        properties: { sum: { type: "number" } },
        required: ["sum"],
        additionalProperties: false,
      },
    },
  ],
  edges: [
    { from: "input", to: "sum", port: "success" },
    { from: "sum", to: "output", port: "success" },
  ],
};
