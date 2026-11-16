// The chat: a list of messages, and a box to type the next one.
//
// The messages shown here are only for the screen. What the AGENT remembers lives on the server
// (agent/app/memory.py): "New chat" clears both.

import { FormEvent, useEffect, useRef, useState } from "react";

import ConfirmCard from "./ConfirmCard";
import { ApiError, Me, PendingAction, sendMessage, startNewChat } from "./api";

// One bubble on the screen.
type Message = {
  from: "you" | "assistant" | "error";
  text: string;
  tools?: string[]; // the tools the agent used for this answer
  action?: PendingAction; // a big change waiting for Confirm (shown as a card under the answer)
};

type Props = {
  me: Me;
  onLogOut: () => void;
  onSessionLost: () => void; // the session is gone (for example: the agent service restarted)
};

export default function ChatPage({ me, onLogOut, onSessionLost }: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [waiting, setWaiting] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);

  // Scroll down to the newest message whenever the list changes.
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, waiting]);

  function addMessage(message: Message) {
    // Use the "previous list" form, because two messages can be added right after each other.
    setMessages((previous) => [...previous, message]);
  }

  async function handleSend(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (text === "" || waiting) {
      return;
    }

    addMessage({ from: "you", text: text });
    setDraft("");
    setWaiting(true);
    try {
      const answer = await sendMessage(text);
      addMessage({
        from: "assistant",
        text: answer.reply,
        tools: answer.tool_calls,
        action: answer.pending_action ?? undefined,
      });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onSessionLost();
        return;
      }
      addMessage({ from: "error", text: err instanceof Error ? err.message : "Something went wrong." });
    } finally {
      setWaiting(false);
    }
  }

  async function handleNewChat() {
    try {
      await startNewChat();
      setMessages([]);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onSessionLost();
      }
    }
  }

  return (
    <div className="chat-page">
      <header>
        <strong>Gatekeeper HR</strong>
        <span className="muted">
          {me.email} ({me.role ?? "superuser"})
        </span>
        <button className="secondary" onClick={handleNewChat} disabled={waiting}>
          New chat
        </button>
        <button className="secondary" onClick={onLogOut}>
          Log out
        </button>
      </header>

      <main className="messages">
        {messages.length === 0 && (
          <p className="muted hint">Ask about your HR records, for example: "How many sick days did I take this year?"</p>
        )}

        {messages.map((message, index) => (
          <div key={index} className="message-group">
            <div className={"bubble " + message.from}>
              {message.text}
              {message.tools && message.tools.length > 0 && (
                <div className="tools">Used: {message.tools.join(", ")}</div>
              )}
            </div>
            {message.action && <ConfirmCard action={message.action} onSessionLost={onSessionLost} />}
          </div>
        ))}

        {waiting && <div className="bubble assistant muted">Thinking...</div>}
        <div ref={bottom} />
      </main>

      <form className="composer" onSubmit={handleSend}>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Type your question..."
          maxLength={2000}
          disabled={waiting}
          autoFocus
        />
        <button type="submit" disabled={waiting || draft.trim() === ""}>
          Send
        </button>
      </form>
    </div>
  );
}
