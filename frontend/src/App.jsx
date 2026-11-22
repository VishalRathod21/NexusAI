import { Navigate, Route, Routes } from "react-router-dom";
import { Protected } from "./auth.jsx";
import Shell from "./components/Shell.jsx";
import Account from "./pages/Account.jsx";
import Admin from "./pages/Admin.jsx";
import AuthPage from "./pages/AuthPage.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import Landing from "./pages/Landing.jsx";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/login" element={<AuthPage mode="login" />} />
      <Route path="/signup" element={<AuthPage mode="signup" />} />
      <Route
        path="/app"
        element={
          <Protected>
            <Shell wide>
              <Dashboard />
            </Shell>
          </Protected>
        }
      />
      <Route
        path="/account"
        element={
          <Protected>
            <Shell>
              <Account />
            </Shell>
          </Protected>
        }
      />
      <Route
        path="/admin"
        element={
          <Protected admin>
            <Shell>
              <Admin />
            </Shell>
          </Protected>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
