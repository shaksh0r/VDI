import { useState, useEffect } from "react";
import App from "./App.jsx";
import Auth from "./Auth.jsx";

export default function Root() {
    const [token, setToken] = useState(() => localStorage.getItem("token") || "");

    function handleLogin(newToken) {
        localStorage.setItem("token", newToken);
        setToken(newToken);
    }

    function handleLogout() {
        localStorage.removeItem("token");
        setToken("");
    }

    if (!token) {
        return <Auth onLogin={handleLogin} />;
    }

    return <App onLogout={handleLogout} />;
}