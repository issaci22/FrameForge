import { useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { keys } from "../api/hooks";
import { Callout, Field, Logo } from "../components/ui";

export function LoginPage() {
  const qc = useQueryClient();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/auth/login", { username, password });
      await qc.invalidateQueries({ queryKey: keys.auth });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-wrap">
      <div className="auth-inner">
        <div className="auth-brand">
          <Logo size="lg" />
          <p>Sign in to manage your libraries and transcoding.</p>
        </div>
        <form className="auth-card" onSubmit={submit}>
          <Field label="Username" htmlFor="login-user">
            <input id="login-user" className="input" autoFocus autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} />
          </Field>
          <Field label="Password" htmlFor="login-pass">
            <input id="login-pass" className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
          {error && <Callout kind="err" title={error} />}
          <button className="btn primary auth-submit" disabled={busy || !username || !password}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}
