// Every call from the page to the agent service goes through this file.
//
// We never see or store a token here. When you log in, the agent service sets a "session_id"
// cookie that JavaScript can't even read (httponly). The browser sends it by itself with every call.

// Who is logged in (from GET /me).
export type Me = {
  email: string;
  role: string | null;
};

// One answer from the chat (from POST /chat).
export type ChatReply = {
  reply: string;
  tool_calls: string[]; // the tools the agent used, e.g. ["get_my_profile"]
};

// An error from the agent service, with its status code (401 = not logged in, 503 = AI is down...).
export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// Send one request to the agent service and return the JSON answer.
// "/api" is forwarded to the agent service by Vite (see vite.config.ts).
async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch("/api" + path, {
    method: method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (!response.ok) {
    throw new ApiError(response.status, await errorMessage(response));
  }
  return (await response.json()) as T;
}

// FastAPI sends errors as {"detail": "..."}. Turn that into a plain sentence.
async function errorMessage(response: Response): Promise<string> {
  try {
    const data = await response.json();
    if (typeof data.detail === "string") {
      return data.detail;
    }
  } catch {
    // The answer was not JSON (for example: the agent service is not running).
  }
  if (response.status === 502 || response.status === 504) {
    return "The agent service is not running.";
  }
  return `Something went wrong (error ${response.status}).`;
}

export function logIn(email: string, password: string): Promise<unknown> {
  return request("POST", "/auth/login", { email: email, password: password });
}

export function logOut(): Promise<unknown> {
  return request("POST", "/auth/logout");
}

export function getMe(): Promise<Me> {
  return request<Me>("GET", "/me");
}

export function sendMessage(message: string): Promise<ChatReply> {
  return request<ChatReply>("POST", "/chat", { message: message });
}

export function startNewChat(): Promise<unknown> {
  return request("POST", "/chat/new");
}
