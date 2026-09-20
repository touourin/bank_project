/** Shared HTTP boundary; credentials stay in memory and failed mutations are never retried. */
export async function request<T>(
  path: string,
  token: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let response: Response;
  try {
    response = await fetch(path, { ...init, headers });
  } catch (error) {
    if (init.signal?.aborted) throw error;
    throw new Error("连接中断。请刷新结果确认操作状态，再决定是否重试。");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const message =
      typeof body?.detail === "string"
        ? body.detail
        : `请求未完成（${response.status}）`;
    throw new Error(`${body?.location ? body.location + "：" : ""}${message}`);
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

export const jsonBody = (value: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(value),
});

export const errorMessage = (error: unknown) =>
  error instanceof Error ? error.message : "操作失败，请重试";
