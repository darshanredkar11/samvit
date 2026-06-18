import { useQuery } from "@tanstack/react-query";
import { api, type SystemStatus, type Task, type GuardViolation } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Activity, Users, ListChecks, ShieldAlert, Database, HardDrive } from "lucide-react";

function statCard(icon: React.ReactNode, label: string, value: string | number, sub?: string) {
  return (
    <Card>
      <CardContent className="p-4 flex items-center gap-3">
        <div className="text-muted-foreground">{icon}</div>
        <div>
          <p className="text-2xl font-bold">{value}</p>
          <p className="text-xs text-muted-foreground">{label}</p>
          {sub && <p className="text-xs text-muted-foreground">{sub}</p>}
        </div>
      </CardContent>
    </Card>
  );
}

function statusBadge(status: string) {
  const map: Record<string, string> = {
    pending: "warning",
    claimed: "default",
    done: "success",
    failed: "destructive",
    cancelled: "secondary",
  };
  return <Badge variant={(map[status] || "outline") as any}>{status}</Badge>;
}

function timeAgo(iso: string) {
  const sec = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const h = Math.floor(min / 60);
  return `${h}h ago`;
}

export default function Dashboard() {
  const { data: status, isLoading } = useQuery<SystemStatus>({
    queryKey: ["admin-status"],
    queryFn: () => api.get("/v1/admin/status"),
    refetchInterval: 5000,
  });

  const { data: tasksData } = useQuery<{ tasks: Task[] }>({
    queryKey: ["admin-tasks-recent"],
    queryFn: () => api.get("/v1/admin/tasks?limit=10"),
    refetchInterval: 10000,
  });

  const { data: guardData } = useQuery<{ violations: GuardViolation[] }>({
    queryKey: ["admin-guard-recent"],
    queryFn: () => api.get("/v1/admin/guard/violations?limit=10"),
    refetchInterval: 10000,
  });

  if (isLoading) {
    return <div className="text-muted-foreground">Loading dashboard...</div>;
  }

  const s = status!;

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold tracking-tight">Dashboard</h2>

      <div className="grid grid-cols-4 gap-4">
        {statCard(<Users className="h-5 w-5" />, "Live Agents", s.agents.total, `${s.agents.active} active`)}
        {statCard(<ListChecks className="h-5 w-5" />, "Pending Tasks", s.tasks.pending)}
        {statCard(<Activity className="h-5 w-5" />, "Claimed", s.tasks.claimed)}
        {statCard(<ShieldAlert className="h-5 w-5" />, "Violations (24h)", s.guard.violations_24h)}
        {statCard(<Database className="h-5 w-5" />, "KV Memories", s.storage.kv_count)}
        {statCard(<HardDrive className="h-5 w-5" />, "Vector Memories", s.storage.vector_count)}
        {statCard(
          <Activity className="h-5 w-5" />,
          "Events",
          s.events.connected ? "Connected" : "Degraded",
          `${s.events.published} published`
        )}
        {statCard(
          <Activity className="h-5 w-5" />,
          "Done / Failed",
          `${s.tasks.done} / ${s.tasks.failed}`
        )}
      </div>

      <div className="grid grid-cols-2 gap-6">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Recent Tasks</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Title</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Age</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {tasksData?.tasks?.slice(0, 10).map((t) => (
                  <TableRow key={t.id}>
                    <TableCell className="font-mono text-xs max-w-[200px] truncate">{t.title}</TableCell>
                    <TableCell>{statusBadge(t.status)}</TableCell>
                    <TableCell className="text-muted-foreground">{timeAgo(t.created_at)}</TableCell>
                  </TableRow>
                ))}
                {(!tasksData?.tasks || tasksData.tasks.length === 0) && (
                  <TableRow><TableCell colSpan={3} className="text-muted-foreground">No tasks</TableCell></TableRow>
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Recent Guard Violations</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Pattern</TableHead>
                  <TableHead>Severity</TableHead>
                  <TableHead>Age</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {guardData?.violations?.slice(0, 10).map((v) => (
                  <TableRow key={v.id}>
                    <TableCell className="font-mono text-xs">{v.pattern}</TableCell>
                    <TableCell>
                      <Badge variant={v.severity === "high" ? "destructive" : "warning"}>
                        {v.severity}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">{timeAgo(v.at)}</TableCell>
                  </TableRow>
                ))}
                {(!guardData?.violations || guardData.violations.length === 0) && (
                  <TableRow><TableCell colSpan={3} className="text-muted-foreground">No violations</TableCell></TableRow>
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
