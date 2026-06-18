import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "@/lib/auth";
import { ToastProvider } from "@/components/ui/toast";
import Login from "@/pages/Login";
import Dashboard from "@/pages/Dashboard";
import Layout from "@/pages/Layout";
import Agents from "@/pages/Agents";
import Tasks from "@/pages/Tasks";
import Guard from "@/pages/Guard";
import Settings from "@/pages/Settings";
import Graph from "@/pages/Graph";
import AgentDetail from "@/pages/AgentDetail";
import KVMemory from "@/pages/KVMemory";

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuth } = useAuth();
  if (!isAuth) return <Navigate to="/admin/login" replace />;
  return <>{children}</>;
}

function AppRoutes() {
  const { isAuth } = useAuth();

  return (
    <Routes>
      <Route
        path="/admin/login"
        element={isAuth ? <Navigate to="/admin" replace /> : <Login />}
      />
      <Route
        path="/admin"
        element={
          <ProtectedRoute>
            <Layout />
          </ProtectedRoute>
        }
      >
        <Route index element={<Dashboard />} />
        <Route path="agents" element={<Agents />} />
        <Route path="tasks" element={<Tasks />} />
        <Route path="guard" element={<Guard />} />
        <Route path="graph" element={<Graph />} />
        <Route path="settings" element={<Settings />} />
        <Route path="agents/:handle" element={<AgentDetail />} />
        <Route path="memory" element={<KVMemory />} />
      </Route>
      <Route path="*" element={<Navigate to="/admin" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <ToastProvider>
        <AuthProvider>
          <AppRoutes />
        </AuthProvider>
      </ToastProvider>
    </BrowserRouter>
  );
}
