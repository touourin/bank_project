import type { HealthResponse } from "./types";

export async function getHealth(signal: AbortSignal): Promise<HealthResponse> {
  let response: Response;
  try {
    response = await fetch("/health", { signal });
  } catch (error) {
    if (signal.aborted) throw error;
    throw new Error("无法连接后端服务，请检查服务是否启动。");
  }
  if (!response.ok)
    throw new Error(`后端服务暂时不可用（${response.status}）。`);
  const data = await response.json();
  if (data?.status !== "ok" || typeof data.version !== "string") {
    throw new Error("后端返回了无法识别的服务状态。");
  }
  return data;
}
