import { useQuery } from "@tanstack/react-query";
import { api, type GuardViolation } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

function severityBadge(severity: string) {
  const map: Record<string, string> = {
    high: "destructive",
    medium: "warning",
    low: "secondary",
  };
  return <Badge variant={(map[severity] || "outline") as any}>{severity}</Badge>;
}

function timeAgo(iso: string) {
  const sec = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const h = Math.floor(min / 60);
  return `${h}h ago`;
}

export default function Guard() {
  const { data, isLoading } = useQuery<{ violations: GuardViolation[] }>({
    queryKey: ["admin-guard"],
    queryFn: () => api.get("/v1/admin/guard/violations?limit=100"),
    refetchInterval: 10000,
  });

  const { data: stats } = useQuery<{ total: number; by_pattern: Record<string, number>; by_agent: Record<string, number> }>({
    queryKey: ["admin-guard-stats"],
    queryFn: () => api.get("/v1/admin/guard/stats"),
    refetchInterval: 30000,
  });

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold tracking-tight">Guard Violations</h2>

      {stats && (
        <div className="grid grid-cols-3 gap-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm text-muted-foreground">Total Violations</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-3xl font-bold">{stats.total}</p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm text-muted-foreground">Top Pattern</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-lg font-bold">
                {Object.entries(stats.by_pattern).sort((a, b) => b[1] - a[1])[0]?.[0] || "—"}
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm text-muted-foreground">Top Agent</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-lg font-bold">
                {Object.entries(stats.by_agent).sort((a, b) => b[1] - a[1])[0]?.[0] || "—"}
              </p>
            </CardContent>
          </Card>
        </div>
      )}

      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Time</TableHead>
              <TableHead>Agent</TableHead>
              <TableHead>Direction</TableHead>
              <TableHead>Tool</TableHead>
              <TableHead>Pattern</TableHead>
              <TableHead>Category</TableHead>
              <TableHead>Severity</TableHead>
              <TableHead>Snippet</TableHead>
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
            {data?.violations.map((v) => (
              <TableRow key={v.id}>
                <TableCell className="text-xs text-muted-foreground">{timeAgo(v.at)}</TableCell>
                <TableCell className="font-mono text-xs">{v.pattern}</TableCell>
                <TableCell>
                  <Badge variant="outline">{v.direction}</Badge>
                </TableCell>
                <TableCell className="font-mono text-xs">{v.tool}</TableCell>
                <TableCell className="font-mono text-xs">{v.pattern}</TableCell>
                <TableCell>{v.category}</TableCell>
                <TableCell>{severityBadge(v.severity)}</TableCell>
                <TableCell className="font-mono text-xs max-w-[200px] truncate">
                  {v.snippet}
                </TableCell>
              </TableRow>
            ))}
            {data?.violations.length === 0 && (
              <TableRow>
                <TableCell colSpan={8} className="text-center text-muted-foreground">
                  No violations found
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
