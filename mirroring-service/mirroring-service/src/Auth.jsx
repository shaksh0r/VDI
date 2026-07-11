import { useState } from "react";
import "./auth.css";

export default function Auth({ onLogin }) {
    const [mode, setMode] = useState("login"); // "login" | "signup"
    const [error, setError] = useState("");
    const [loading, setLoading] = useState(false);

    // ── Login state ───────────────────────────────────────────────────────────
    const [loginForm, setLoginForm] = useState({ username: "", password: "" });

    // ── Signup state ──────────────────────────────────────────────────────────
    const [signupForm, setSignupForm] = useState({
        username: "",
        email: "",
        password: "",
        full_name: "",
        student_id: "",
        department: "",
    });

    function updateLogin(e) {
        setLoginForm({ ...loginForm, [e.target.name]: e.target.value });
    }

    function updateSignup(e) {
        setSignupForm({ ...signupForm, [e.target.name]: e.target.value });
    }

    function switchMode(m) {
        setMode(m);
        setError("");
    }

    // ── Login submit ──────────────────────────────────────────────────────────
    async function handleLogin(e) {
        e.preventDefault();
        setError("");
        setLoading(true);

        try {
            const resp = await fetch("/auth/login", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(loginForm),
            });

            const body = await resp.json();

            if (!resp.ok) {
                throw new Error(body.detail || "Login failed");
            }

            onLogin(body.access_token);
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    }

    // ── Signup submit ─────────────────────────────────────────────────────────
    async function handleSignup(e) {
        e.preventDefault();
        setError("");
        setLoading(true);

        try {
            // 1. Create account
            const signupResp = await fetch("/auth/signup", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(signupForm),
            });

            const signupBody = await signupResp.json();

            if (!signupResp.ok) {
                throw new Error(signupBody.detail || "Signup failed");
            }

            // 2. Auto-login with the same credentials
            const loginResp = await fetch("/auth/login", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    username: signupForm.username,
                    password: signupForm.password,
                }),
            });

            const loginBody = await loginResp.json();

            if (!loginResp.ok) {
                throw new Error(loginBody.detail || "Auto-login after signup failed");
            }

            onLogin(loginBody.access_token);
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    }

    // ── Render ────────────────────────────────────────────────────────────────
    return (
        <div className="auth-shell">
            <div className="auth-card">

                {/* Brand */}
                <div className="auth-brand">
                    <div className="auth-mark">
                        <svg width="20" height="20" viewBox="0 0 16 16" fill="none">
                            <rect x="1" y="1" width="6" height="6" rx="1" fill="currentColor" opacity="0.9" />
                            <rect x="9" y="1" width="6" height="6" rx="1" fill="currentColor" opacity="0.5" />
                            <rect x="1" y="9" width="6" height="6" rx="1" fill="currentColor" opacity="0.5" />
                            <rect x="9" y="9" width="6" height="6" rx="1" fill="currentColor" opacity="0.25" />
                        </svg>
                    </div>
                    <span className="auth-brand__name">VDI Mirror</span>
                </div>

                {/* Tab toggle */}
                <div className="auth-tabs">
                    <button
                        className={"auth-tab" + (mode === "login" ? " auth-tab--active" : "")}
                        onClick={() => switchMode("login")}
                    >
                        Sign In
                    </button>
                    <button
                        className={"auth-tab" + (mode === "signup" ? " auth-tab--active" : "")}
                        onClick={() => switchMode("signup")}
                    >
                        Sign Up
                    </button>
                </div>

                {/* Error */}
                {error && <div className="auth-error">{error}</div>}

                {/* ── Login form ── */}
                {mode === "login" && (
                    <form className="auth-form" onSubmit={handleLogin}>
                        <div className="auth-field">
                            <label className="auth-label">Username</label>
                            <input
                                className="auth-input"
                                type="text"
                                name="username"
                                value={loginForm.username}
                                onChange={updateLogin}
                                autoComplete="username"
                                required
                            />
                        </div>

                        <div className="auth-field">
                            <label className="auth-label">Password</label>
                            <input
                                className="auth-input"
                                type="password"
                                name="password"
                                value={loginForm.password}
                                onChange={updateLogin}
                                autoComplete="current-password"
                                required
                            />
                        </div>

                        <button className="auth-submit" type="submit" disabled={loading}>
                            {loading ? "Signing in…" : "Sign In"}
                        </button>
                    </form>
                )}

                {/* ── Signup form ── */}
                {mode === "signup" && (
                    <form className="auth-form" onSubmit={handleSignup}>
                        <div className="auth-row">
                            <div className="auth-field">
                                <label className="auth-label">Full Name</label>
                                <input
                                    className="auth-input"
                                    type="text"
                                    name="full_name"
                                    value={signupForm.full_name}
                                    onChange={updateSignup}
                                    required
                                />
                            </div>
                            <div className="auth-field">
                                <label className="auth-label">Username</label>
                                <input
                                    className="auth-input"
                                    type="text"
                                    name="username"
                                    value={signupForm.username}
                                    onChange={updateSignup}
                                    autoComplete="username"
                                    required
                                />
                            </div>
                        </div>

                        <div className="auth-field">
                            <label className="auth-label">Email</label>
                            <input
                                className="auth-input"
                                type="email"
                                name="email"
                                value={signupForm.email}
                                onChange={updateSignup}
                                autoComplete="email"
                                required
                            />
                        </div>

                        <div className="auth-field">
                            <label className="auth-label">Password</label>
                            <input
                                className="auth-input"
                                type="password"
                                name="password"
                                value={signupForm.password}
                                onChange={updateSignup}
                                autoComplete="new-password"
                                required
                            />
                        </div>

                        <div className="auth-row">
                            <div className="auth-field">
                                <label className="auth-label">Student ID</label>
                                <input
                                    className="auth-input"
                                    type="text"
                                    name="student_id"
                                    value={signupForm.student_id}
                                    onChange={updateSignup}
                                    required
                                />
                            </div>
                            <div className="auth-field">
                                <label className="auth-label">Department</label>
                                <input
                                    className="auth-input"
                                    type="text"
                                    name="department"
                                    value={signupForm.department}
                                    onChange={updateSignup}
                                    required
                                />
                            </div>
                        </div>

                        <button className="auth-submit" type="submit" disabled={loading}>
                            {loading ? "Creating account…" : "Create Account"}
                        </button>
                    </form>
                )}

            </div>
        </div>
    );
}