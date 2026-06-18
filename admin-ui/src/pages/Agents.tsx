import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  api,
  type Agent,
  ApiError,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { RotateCw, UserPlus, Shield } from "lucide-react";

export default function Agents() {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [showRegister, setShowRegister] = useState(false);
  const [newHandle, setNewHandle] = useState("");
  const [newProvider, setNewProvider] = useState("cli");
  const [newRole, setNewRole] = useState("agent");

  const { data, isLoading } = useQuery<{ agents: Agent[]; total: number }>({
    queryKey: ["admin-agents"],
    queryFn: () => api.get("/v1/admin/agents"),
    refetchInterval: 10000,
  });

  const registerMutation = useMutation({
    mutationFn: (body: { handle: string; provider: string; role: string }) =>
      api.post("/v1/admin/agents", body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-agents"] });
      setShowRegister(false);
      setNewHandle("");
      toast({ title: "Agent registered", variant: "success" });
    },
    onError: (err: Error) => {
      toast({ title: "Registration failed", description: err.message, variant: "destructive" });
    },
  });

  const suspendMutation = useMutation({
    mutationFn: (handle: string) => api.post(`/v1/admin/agents/${handle}/suspend`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-agents"] });
      toast({ title: "Agent suspended", variant: "success" });
    },
    onError: (err: Error) => {
      toast({ title: "Failed to suspend", description: err.message, variant: "destructive" });
    },
  });

  const unsuspendMutation = useMutation({
    mutationFn: (handle: string) => api.post(`/v1/admin/agents/${handle}/unsuspend`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-agents"] });
      toast({ title: "Agent unsuspended", variant: "success" });
    },
    onError: (err: Error) => {
      toast({ title: "Failed to unsuspend", description: err.message, variant: "destructive" });
    },
  });

  const setRoleMutation = useMutation({
    mutationFn: ({ handle, role }: { handle: string; role: string }) =>
      api.post(`/v1/admin/agents/${handle}/role`, { role }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-agents"] });
      toast({ title: "Role updated", variant: "success" });
    },
    onError: (err: Error) => {
      toast({ title: "Failed to update role", description: err.message, variant: "destructive" });
    },
  });

  const rotateMutation = useMutation({
    mutationFn: (handle: string) => api.post<{ token: string }>(`/v1/admin/agents/${handle}/rotate`),
    onSuccess: (data) => {
      toast({
        title: "Token rotated",
        description: `New token: ${data.token.substring(0, 20)}...`,
      });
    },
    onError: (err: Error) => {
      toast({ title: "Failed to rotate", description: err.message, variant: "destructive" });
    },
  });

  const handleRegister = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newHandle.trim()) return;
    registerMutation.mutate({
      handle: newHandle.trim(),
      provider: newProvider,
      role: newRole,
    });
  };

  const timeAgo = (iso: string) => {
    const sec = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
    if (sec < 60) return `${sec}s ago`;
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min}m ago`;
    const h = Math.floor(min / 60);
    return `${h}h ago`;
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold tracking-tight">Agents</h2>
        <Button onClick={() => setShowRegister(true)}>
          <UserPlus className="h-4 w-4 mr-2" />
          Register Agent
        </Button>
      </div>

      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Handle</TableHead>
              <TableHead>Provider</TableHead>
              <TableHead>Role</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Created</TableHead>
              <TableHead>Created Tasks</TableHead>
              <TableHead>Claimed Tasks</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading && (
              <TableRow>
                <TableCell colSpan={8} className="text-center text-muted-foreground">
                  Loading...
                </TableCell>
              </TableRow>
            )}
            {data?.agents.map((agent) => (
              <TableRow key={agent.id}>
                <TableCell className="font-mono text-xs">
                  <Link to={`/admin/agents/${agent.handle}`} className="hover:underline">
                    {agent.handle}
                  </Link>
                </TableCell>
                <TableCell>{agent.provider}</TableCell>
                <TableCell>
                  <div className="flex items-center gap-2">
                    <Select
                      value={agent.role}
                      onValueChange={(val) => setRoleMutation.mutate({ handle: agent.handle, role: val })}
                    >
                      <SelectTrigger className="h-7 w-28">
                        <Shield className="h-3 w-3 mr-1" />
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="agent">Agent</SelectItem>
                        <SelectItem value="operator">Operator</SelectItem>
                        <SelectItem value="auditor">Auditor</SelectItem>
                        <SelectItem value="admin">Admin</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </TableCell>
                <TableCell>
                  {agent.suspended_at ? (
                    <Badge variant="destructive">Suspended</Badge>
                  ) : (
                    <Badge variant="success">Active</Badge>
                  )}
                </TableCell>
                <TableCell className="text-muted-foreground text-xs">
                  {timeAgo(agent.created_at)}
                </TableCell>
                <TableCell>{agent.tasks_created}</TableCell>
                <TableCell>{agent.tasks_claimed}</TableCell>
                <TableCell className="text-right">
                  <div className="flex justify-end gap-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => rotateMutation.mutate(agent.handle)}
                      title="Rotate token"
                    >
                      <RotateCw className="h-3 w-3" />
                    </Button>
                    {agent.suspended_at ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => unsuspendMutation.mutate(agent.handle)}
                      >
                        Unsuspend
                      </Button>
                    ) : (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => suspendMutation.mutate(agent.handle)}
                        disabled={agent.role === "admin"}
                        title={agent.role === "admin" ? "Cannot suspend admins" : "Suspend"}
                      >
                        Suspend
                      </Button>
                    )}
                  </div>
                </TableCell>
              </TableRow>
            ))}
            {data?.agents.length === 0 && (
              <TableRow>
                <TableCell colSpan={8} className="text-center text-muted-foreground">
                  No agents found
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      <Dialog open={showRegister} onOpenChange={setShowRegister}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Register Agent</DialogTitle>
            <DialogDescription>
              Create a new agent with an admin-assigned role.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleRegister} className="space-y-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">Handle</label>
              <Input
                value={newHandle}
                onChange={(e) => setNewHandle(e.target.value)}
                placeholder="agent-handle"
                required
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Provider</label>
              <Input
                value={newProvider}
                onChange={(e) => setNewProvider(e.target.value)}
                placeholder="claude, codex, openai, hermes, test, ..."
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Role</label>
              <Select value={newRole} onValueChange={setNewRole}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="agent">Agent</SelectItem>
                  <SelectItem value="operator">Operator</SelectItem>
                  <SelectItem value="auditor">Auditor</SelectItem>
                  <SelectItem value="admin">Admin</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <DialogFooter>
              <Button type="submit" disabled={registerMutation.isPending}>
                {registerMutation.isPending ? "Registering..." : "Register"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
