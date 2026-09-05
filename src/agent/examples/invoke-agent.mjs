// Run with Node.js 20+ while the sample HTTP agent is listening.
const baseUrl = process.env.AGENT_URL ?? "http://127.0.0.1:8001";

try {
  const response = await fetch(`${baseUrl.replace(/\/$/, "")}/v1/runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      task: "Đề xuất flow nhận đơn hàng, kiểm tra dữ liệu và chờ duyệt",
      context: "Kết quả sẽ được dùng làm output của một agent node trong AgentFlow.",
    }),
    signal: AbortSignal.timeout(130_000),
  });
  const result = await response.json();
  if (!response.ok || result.status !== "succeeded") {
    throw new Error(result.error?.message ?? `Agent returned HTTP ${response.status}`);
  }
  // Preserve mode so callers never mistake a scripted demo for model output.
  console.log(JSON.stringify({ mode: result.mode, output: result.output }, null, 2));
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
