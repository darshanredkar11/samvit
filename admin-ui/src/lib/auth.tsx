import { createContext, useContext, useCallback, useState, useEffect, type ReactNode } from "react";
import { isAuthenticated, setToken, clearToken, api, type Agent } from "@/lib/api";

interface AuthContextType {
  agent: Agent | null;
  isAuth: boolean;
  login: (token: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextType>({
  agent: null,
  isAuth: false,
  login: async () => {},
  logout: () => {},
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [agent, setAgent] = useState<Agent | null>(null);
  const [initialised, setInitialised] = useState(false);

  const login = useCallback(async (tokenStr: string) => {
    clearToken();
    setToken(tokenStr);
    const data = await api.get<{ agent: Agent }>("/v1/admin/me");
    setAgent(data.agent);
  }, []);

  const logout = useCallback(() => {
    clearToken();
    setAgent(null);
  }, []);

  useEffect(() => {
    if (isAuthenticated()) {
      api.get<{ agent: Agent }>("/v1/admin/me")
        .then((data) => setAgent(data.agent))
        .catch(() => clearToken())
        .finally(() => setInitialised(true));
    } else {
      api.get<{ agent: Agent }>("/v1/admin/me")
        .then((data) => setAgent(data.agent))
        .catch(() => {})
        .finally(() => setInitialised(true));
    }
  }, []);

  if (!initialised) return null;

  return (
    <AuthContext.Provider
      value={{ agent, isAuth: !!agent, login, logout }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
