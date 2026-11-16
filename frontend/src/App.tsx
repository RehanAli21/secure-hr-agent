// The whole app: show the login form, or the chat if you are logged in.

import { useEffect, useState } from "react";

import ChatPage from "./ChatPage";
import LoginPage from "./LoginPage";
import { Me, getMe, logOut } from "./api";

export default function App() {
  // undefined = still checking, null = not logged in, Me = logged in
  const [me, setMe] = useState<Me | null | undefined>(undefined);

  // Ask the agent service who is logged in. If the cookie is missing or the session is gone,
  // GET /me gives 401 and we show the login form.
  async function checkLogin() {
    try {
      setMe(await getMe());
    } catch {
      setMe(null);
    }
  }

  // When the page opens, check once: maybe you are still logged in from before.
  useEffect(() => {
    checkLogin();
  }, []);

  async function handleLogOut() {
    try {
      await logOut();
    } finally {
      setMe(null);
    }
  }

  if (me === undefined) {
    return <p className="muted center">Loading...</p>;
  }
  if (me === null) {
    return <LoginPage onLoggedIn={checkLogin} />;
  }
  return <ChatPage me={me} onLogOut={handleLogOut} onSessionLost={() => setMe(null)} />;
}
