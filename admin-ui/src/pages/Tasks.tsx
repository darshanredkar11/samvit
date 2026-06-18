import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type Task } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";

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

export default function Tasks() {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [statusFilter, setStatusFilter] = useState("all");
  const [tagFilter, setTagFilter] = useState("");

  const { data, isLoading } = useQuery<{ tasks: Task[] }>({
    queryKey: ["admin-tasks", statusFilter, tagFilter],
    queryFn: () => {
      let path = "/v1/admin/tasks?limit=50";
      if (statusFilter !== "all") path += `&status=${statusFilter}`;
      return api.get(path);
    },
    refetchInterval: 10000,
  });

  const releaseMutation = useMutation({
    mutationFn: (taskId: string) => api.post(`/v1/admin/tasks/${taskId}/release`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-tasks"] });
      toast({ title: "Task released", variant: "success" });
    },
    onError: (err: Error) => {
      toast({ title: "Failed to release", description: err.message, variant: "destructive" });
    },
  });

  const cancelMutation = useMutation({
    mutationFn: (taskId: string) => api.post(`/v1/admin/tasks/${taskId}/cancel`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-tasks"] });
      toast({ title: "Task cancelled", variant: "success" });
    },
    onError: (err: Error) => {
      toast({ title: "Failed to cancel", description: err.message, variant: "destructive" });
    },
  });

  const releaseStaleMutation = useMutation({
    mutationFn: () => api.post("/v1/admin/tasks/release-stale"),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-tasks"] });
      toast({ title: "Stale claims released", variant: "success" });
    },
  });

  const filteredTasks = (data?.tasks || []).filter((t) => {
    if (tagFilter && !t.tags?.some((tag) => tag.includes(tagFilter))) return false;
    return true;
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold tracking-tight">Tasks</h2>
        <Button variant="outline" size="sm" onClick={() => releaseStaleMutation.mutate()}>
          Release Stale Claims
        </Button>
      </div>

      <div className="flex gap-4 items-center">
        <div className="w-40">
          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Statuses</SelectItem>
              <SelectItem value="pending">Pending</SelectItem>
              <SelectItem value="claimed">Claimed</SelectItem>
              <SelectItem value="done">Done</SelectItem>
              <SelectItem value="failed">Failed</SelectItem>
              <SelectItem value="cancelled">Cancelled</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <Input
          placeholder="Filter by tag..."
          value={tagFilter}
          onChange={(e) => setTagFilter(e.target.value)}
          className="w-48"
        />
        <span className="text-sm text-muted-foreground">
          {filteredTasks.length} tasks
        </span>
      </div>

      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-[100px]">ID</TableHead>
              <TableHead>Title</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Priority</TableHead>
              <TableHead>Tags</TableHead>
              <TableHead>Claimed By</TableHead>
              <TableHead>Age</TableHead>
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
            {filteredTasks.map((t) => (
              <TableRow key={t.id}>
                <TableCell className="font-mono text-xs">{t.id.substring(0, 8)}</TableCell>
                <TableCell className="max-w-[200px] truncate">{t.title}</TableCell>
                <TableCell>{statusBadge(t.status)}</TableCell>
                <TableCell>{t.priority}</TableCell>
                <TableCell>
                  <div className="flex gap-1 flex-wrap">
                    {t.tags?.map((tag) => (
                      <Badge key={tag} variant="outline" className="text-xs">
                        {tag}
                      </Badge>
                    ))}
                  </div>
                </TableCell>
                <TableCell className="font-mono text-xs">{t.claimed_by || "—"}</TableCell>
                <TableCell className="text-muted-foreground">{timeAgo(t.created_at)}</TableCell>
                <TableCell className="text-right">
                  <div className="flex justify-end gap-1">
                    {t.status === "claimed" && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => releaseMutation.mutate(t.id)}
                      >
                        Release
                      </Button>
                    )}
                    {t.status === "pending" && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => cancelMutation.mutate(t.id)}
                      >
                        Cancel
                      </Button>
                    )}
                  </div>
                </TableCell>
              </TableRow>
            ))}
            {filteredTasks.length === 0 && !isLoading && (
              <TableRow>
                <TableCell colSpan={8} className="text-center text-muted-foreground">
                  No tasks found
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
