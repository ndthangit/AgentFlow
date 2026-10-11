export const FILESYSTEM_DEMO_MCP = {
  slug: "filesystem-demo",
  name: "Filesystem MCP demo",
  description: "Đọc, ghi và liệt kê file trong volume cô lập của MCP container.",
  transport: "streamable_http" as const,
  url: "http://filesystem-mcp:8002/mcp",
};

export const starterDraft = {
  nodes: [
    {
      id: "start",
      type: "input.schema",
      name: "Bắt đầu",
      note: "Định nghĩa các biến đầu vào ban đầu của workflow.",
      schema: { type: "object", properties: {}, additionalProperties: false },
    },
    {
      id: "agent",
      type: "agent",
      name: "Agent",
      note: "Mô tả ngắn nhiệm vụ của agent.",
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
      name: "Kết thúc",
      note: "Định nghĩa các biến workflow trả về.",
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
      name: "Bắt đầu",
      note: "Nhận chủ đề ban đầu từ người dùng.",
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
      name: "Agent phân tích",
      note: "Phân tích chủ đề và tạo nội dung nháp có cấu trúc.",
      inputs: { topic: { from: "$input.topic" } },
      config: {
        instructions: "Phân tích chủ đề người dùng cung cấp. Trả về draft là một đoạn nội dung ngắn, rõ ràng và có các ý chính.",
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
      name: "Chạy xử lý song song",
      note: "Truyền cùng bản nháp cho Python chuẩn hóa và Agent phản biện.",
    },
    {
      id: "python_formatter",
      type: "code.python",
      name: "Python chuẩn hóa",
      note: "Chuẩn hóa khoảng trắng và thống kê số từ trong bản nháp.",
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
      name: "Agent phản biện",
      note: "Đánh giá bản nháp độc lập trong lúc Python đang chuẩn hóa.",
      inputs: { draft: { from: "$nodes.parallel_split.output.draft" } },
      config: {
        instructions: "Đánh giá draft, chỉ ra điểm cần cải thiện ngắn gọn và trả về trường review.",
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
      name: "Agent tổng hợp",
      note: "Đợi hai nhánh hoàn tất rồi tạo câu trả lời cuối cùng.",
      inputs: {
        content: { from: "$nodes.python_formatter.output.cleaned_text" },
        word_count: { from: "$nodes.python_formatter.output.word_count" },
        review: { from: "$nodes.review_agent.output.review" },
      },
      config: {
        instructions: "Kết hợp content đã chuẩn hóa với review để tạo câu trả lời hoàn chỉnh bằng tiếng Việt. Bắt buộc dùng tool filesystem-demo_write_file để ghi câu trả lời vào demo/workflow-result.txt, sau đó dùng filesystem-demo_read_file đọc lại chính file đó. Trả nội dung đã đọc trong trường final_answer.",
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
      name: "Kết thúc",
      note: "Trả về câu trả lời hoàn chỉnh.",
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
  if (!finalAgent) throw new Error("Không tìm thấy Agent tổng hợp trong workflow demo");
  finalAgent.config.mcpServerIds = [mcpServerId];
  return draft;
}

export const sumWorkflowDraft = {
  nodes: [
    {
      id: "input",
      type: "input.schema",
      name: "Hai số đầu vào",
      note: "Nhận num1 và num2 từ dữ liệu chạy.",
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
      name: "Cộng hai số",
      note: "Tính num1 + num2 mà không cần gọi LLM.",
      inputs: { left: { from: "$input.num1" }, right: { from: "$input.num2" } },
      config: { outputKey: "sum" },
    },
    {
      id: "output",
      type: "output.schema",
      name: "Kết quả",
      note: "Trả về tổng của hai số.",
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
