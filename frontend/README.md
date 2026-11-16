# Gatekeeper HR — Chat page

The page people use: a login form and a chat with the HR assistant. React + TypeScript, built with Vite.

```
Browser (this page)  -->  agent service (../agent)  -->  CRUD API (../backend)  -->  database
```

## What you need

- [Node.js](https://nodejs.org) 20 or newer
- The backend (port 8000), Ollama and the agent service (port 8001) running: see their READMEs

## Setup and run

From the `frontend` folder:

```
npm install
npm run dev
```

Open http://localhost:5173 and log in with a demo user, for example `sara.wilson@gatekeeper.local`
(employee), `leila.ali@gatekeeper.local` (manager) or `grace.raza@gatekeeper.local` (HR).
The password of every demo user is `Password123!`.

`npm run typecheck` checks the TypeScript, `npm run build` checks it and builds the page into `dist/`.

## The files

| File | What it does |
| --- | --- |
| `src/main.tsx` | Starts React |
| `src/App.tsx` | Shows the login form, or the chat if you are logged in (asks `GET /me`) |
| `src/LoginPage.tsx` | The login form |
| `src/ChatPage.tsx` | The chat: messages, "New chat" and "Log out" |
| `src/ConfirmCard.tsx` | The Confirm / Cancel card for a big change the agent proposed |
| `src/api.ts` | Every call to the agent service |
| `src/styles.css` | All the styling |
| `vite.config.ts` | Forwards `/api/...` to the agent service |

## How the page talks to the agent service

The page calls `/api/chat`, `/api/me`... and Vite forwards them to http://localhost:8001 (without `/api`).
So for the browser, the page and the agent service are **one address**: the login cookie just works,
and no CORS settings are needed.

**The page never sees a token.** After login, the agent service sets a `session_id` cookie that is
`httponly`: JavaScript can't read it, the browser just sends it along. The tokens stay in the agent
service, and the password is only ever typed into the login form, never into the chat.

## Big changes: the Confirm card

When HR asks for a big change ("Give Leila Ali 170000 from 1 January"), the agent only proposes it.
The chat reply then has a `pending_action`, and the page shows it as a card under the answer:

```
Please check this change
Change the salary of Leila Ali from 163500.00 to 170000, starting 2027-01-01.
Nothing has changed yet. Confirm before 11:28 PM.
[Confirm] [Cancel]
```

- The text is the **CRUD API's summary**, built from the real records, not the AI's words.
- **Only clicking Confirm makes the change.** Typing "yes" in the chat never does.
- After a click the card shows the result: done, cancelled, or why it was not done (for example
  "This action has expired" after 5 minutes, or "The record changed since the preview" when the
  data changed in the meantime). Then just ask again for a fresh preview.

If the agent service restarts, everyone's session is gone: the page notices the 401 and shows the
login form again.
