// Run with Node.js 20+ while the sample HTTP agent is listening.
const baseUrl = process.env.AGENT_URL ?? "http://127.0.0.1:8001";

try {
  const response = await fetch(`${baseUrl.replace(/\/$/, "")}/v1/runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      task: "Propose a flow that receives orders, validates data, and waits for approval",
      context: "The result will be used as the output of an Agent node in AgentFlow.",
    }),
    signal: AbortSignal.timeout(130_000),
  });
  const result = await response.json();
  if (!response.ok || result.status !== "succeeded") {
    throw new Error(result.error?.message ?? `Agent returned HTTP ${response.status}`);
  }
  // The runtime only returns live model output.
  console.log(JSON.stringify({ mode: result.mode, output: result.output }, null, 2));
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
