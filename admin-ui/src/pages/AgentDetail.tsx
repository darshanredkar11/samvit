import { useQuery } from "@tanstack/react-query";
import { useParams, Link } from "react-router-dom";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ArrowLeft } from "lucide-react";

function timeAgo(iso: string) {
  const sec = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const h = Math.floor(min / 60);
  return `${h}h ago`;
}

function timelineIcon(type: string) {
  switch (type) {
    case "task_created": return "📋";
    case "task_claimed": return "✋";
    default: return "•";
  }
}

interface AgentTimelineEvent {
  type: string;
  ref_id: string | null;
  summary: string;
  at: string;
}

interface AgentDetailData {
  id: string;
  handle: string;
  provider: string;
  role: string;
  suspended_at: string | null;
  created_at: string;
  tasks_created: number;
  tasks_claimed: number;
  tasks_completed: number;
  timeline: AgentTimelineEvent[];
}

export default function AgentDetail() {
  const { handle } = useParams<{ handle: string }>();

  const { data, isLoading } = useQuery<AgentDetailData>({
    queryKey: ["admin-agent-detail", handle],
    queryFn: () => api.get(`/v1/admin/agents/${handle}`),
    refetchInterval: 10000,
  });

  if (isLoading) {
    return <div className="text-muted-foreground">Loading agent details...</div>;
  }

  if (!data) {
    return <div className="text-muted-foreground">Agent not found</div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <Button variant="ghost" size="sm" asChild>
          <Link to="/admin/agents">
            <ArrowLeft className="h-4 w-4 mr-1" />
            Back
          </Link>
        </Button>
        <h2 className="text-2xl font-bold tracking-tight">{data.handle}</h2>
        <Badge variant={data.role === "admin" ? "default" : "secondary"}>{data.role}</Badge>
        {data.suspended_at ? (
          <Badge variant="destructive">Suspended</Badge>
        ) : (
          <Badge variant="success">Active</Badge>
        )}
      </div>

      <div className="grid grid-cols-4 gap-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-muted-foreground">Provider</CardTitle>
          </CardHeader>
          <CardContent><p className="text-lg font-medium">{data.provider}</p></CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-muted-foreground">Tasks Created</CardTitle>
          </CardHeader>
          <CardContent><p className="text-lg font-medium">{data.tasks_created}</p></CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-muted-foreground">Tasks Claimed</CardTitle>
          </CardHeader>
          <CardContent><p className="text-lg font-medium">{data.tasks_claimed}</p></CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-muted-foreground">Tasks Completed</CardTitle>
          </CardHeader>
          <CardContent><p className="text-lg font-medium">{data.tasks_completed}</p></CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Activity Timeline</CardTitle>
        </CardHeader>
        <CardContent>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-muted-foreground border-b">
                <th className="text-left py-1 pr-2 w-6"></th>
                <th className="text-left py-1 pr-4">Type</th>
                <th className="text-left py-1 pr-4">Summary</th>
                <th className="text-right py-1">Time</th>
              </tr>
            </thead>
            <tbody>
              {(data.timeline || []).map((ev: AgentTimelineEvent, i: number) => (
                <tr key={i} className="border-b border-muted/30 hover:bg-muted/20">
                  <td className="py-1.5 pr-2">{timelineIcon(ev.type)}</td>
                  <td className="py-1.5 pr-4 font-mono text-muted-foreground">{ev.type}</td>
                  <td className="py-1.5 pr-4 max-w-[300px] truncate">{ev.summary}</td>
                  <td className="py-1.5 text-right text-muted-foreground">{timeAgo(ev.at)}</td>
                </tr>
              ))}
              {(!data.timeline || data.timeline.length === 0) && (
                <tr><td colSpan={4} className="py-4 text-center text-muted-foreground">No activity</td></tr>
              )}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}
