// Every call from the page to the agent service goes through this file.
//
// We never see or store a token here. When you log in, the agent service sets a "session_id"
// cookie that JavaScript can't even read (httponly). The browser sends it by itself with every call.

// Who is logged in (from GET /me).
export type Me = {
  email: string;
  role: string | null;
};

// A big change (like a new salary) waiting for the user to click Confirm.
// The summary is written by the CRUD API from the real data, never by the AI.
export type PendingAction = {
  id: number;
  summary: string;
  expires_at: string; // after this time (5 minutes), it can't be confirmed any more
};

// Numbers to draw under an answer. They come from the CRUD API's analytics, not from the AI's words.
export type Chart = {
  title: string; // e.g. "Average current salary of active employees (no currency)"
  details: string; // e.g. "by department"
  kind: "bar" | "line"; // a line for months and years, bars for everything else
  points: { label: string; value: number }[];
};

// One answer from the chat (from POST /chat).
export type ChatReply = {
  reply: string;
  tool_calls: string[]; // the tools the agent used, e.g. ["get_my_profile"]
  pending_action: PendingAction | null;
  chart: Chart | null;
};

// The answer to a Confirm or Cancel click.
export type ActionResult = {
  message: string; // "Done" or "Cancelled"
  summary: string;
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

// Only these two run or drop a big change, and only when a PERSON clicks the button.
// The AI has no way to call them: typing "yes" in the chat never confirms anything.
export function confirmAction(id: number): Promise<ActionResult> {
  return request<ActionResult>("POST", `/actions/${id}/confirm`);
}

export function cancelAction(id: number): Promise<ActionResult> {
  return request<ActionResult>("POST", `/actions/${id}/cancel`);
}
