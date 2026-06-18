import { useRef, useEffect, useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type GraphData, type GraphNode, type TimelineEvent } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

function timeAgo(iso: string) {
  const sec = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const h = Math.floor(min / 60);
  return `${h}h ago`;
}

type Pos = { x: number; y: number };

function simulateForceLayout(nodes: GraphNode[], edges: { source: string; target: string }[], w: number, h: number): Pos[] {
  const n = nodes.length;
  const center = { x: w / 2, y: h / 2 };
  const radius = Math.min(w, h) * 0.35;

  const pos: Pos[] = nodes.map((_, i) => {
    const angle = (2 * Math.PI * i) / n - Math.PI / 2;
    return { x: center.x + radius * Math.cos(angle), y: center.y + radius * Math.sin(angle) };
  });

  const rep = 4000;
  const attr = 0.005;
  const grav = 0.008;
  const iter = 120;

  for (let k = 0; k < iter; k++) {
    const f: Pos[] = pos.map(() => ({ x: 0, y: 0 }));
    const cool = 1 - k / iter;

    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const dx = pos[i].x - pos[j].x;
        const dy = pos[i].y - pos[j].y;
        const d = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
        const force = rep / (d * d);
        f[i].x += (dx / d) * force;
        f[i].y += (dy / d) * force;
        f[j].x -= (dx / d) * force;
        f[j].y -= (dy / d) * force;
      }
    }

    for (const edge of edges) {
      const si = nodes.findIndex((x) => x.handle === edge.source);
      const ti = nodes.findIndex((x) => x.handle === edge.target);
      if (si === -1 || ti === -1) continue;
      const dx = pos[ti].x - pos[si].x;
      const dy = pos[ti].y - pos[si].y;
      const d = Math.sqrt(dx * dx + dy * dy);
      const force = attr * d;
      f[si].x += dx * force;
      f[si].y += dy * force;
      f[ti].x -= dx * force;
      f[ti].y -= dy * force;
    }

    for (let i = 0; i < n; i++) {
      f[i].x += (center.x - pos[i].x) * grav;
      f[i].y += (center.y - pos[i].y) * grav;
    }

    for (let i = 0; i < n; i++) {
      pos[i].x += f[i].x * cool;
      pos[i].y += f[i].y * cool;
    }
  }

  return pos;
}

const ROLE_COLORS: Record<string, string> = {
  admin: "#ef4444",
  operator: "#f59e0b",
  auditor: "#8b5cf6",
  agent: "#22c55e",
};

const STATUS_COLORS: Record<string, string> = {
  pending: "#f59e0b",
  claimed: "#3b82f6",
  done: "#22c55e",
  failed: "#ef4444",
  cancelled: "#6b7280",
};

function timelineIcon(type: string) {
  switch (type) {
    case "agent_created": return "🟢";
    case "task_created": return "📋";
    case "task_claimed": return "✋";
    case "task_done": return "✅";
    case "task_failed": return "❌";
    case "message": return "💬";
    case "broadcast": return "📢";
    default: return "•";
  }
}

export default function Graph() {
  const svgRef = useRef<SVGSVGElement>(null);
  const [dim, setDim] = useState({ w: 800, h: 500 });
  const [tooltip, setTooltip] = useState<{ node: GraphNode; x: number; y: number } | null>(null);

  const { data, isLoading } = useQuery<GraphData>({
    queryKey: ["admin-graph"],
    queryFn: () => api.get("/v1/admin/graph"),
    refetchInterval: 5000,
  });

  useEffect(() => {
    const el = svgRef.current?.parentElement;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect;
      setDim({ w: Math.max(width, 400), h: Math.max(height, 400) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const allEdges = useMemo(() => {
    if (!data) return [];
    return [
      ...data.edges.tasks.map((e) => ({ source: e.source, target: e.target, type: "task" as const, data: e })),
      ...data.edges.messages.map((e) => ({ source: e.source, target: e.target, type: "message" as const, data: e })),
    ];
  }, [data]);

  const nodePositions = useMemo(() => {
    if (!data) return [];
    return simulateForceLayout(data.nodes, allEdges, dim.w, dim.h);
  }, [data, allEdges, dim]);

  const degrees = useMemo(() => {
    const map = new Map<string, number>();
    for (const e of allEdges) {
      map.set(e.source, (map.get(e.source) || 0) + 1);
      map.set(e.target, (map.get(e.target) || 0) + 1);
    }
    return map;
  }, [allEdges]);

  if (isLoading) {
    return <div className="text-muted-foreground">Loading graph...</div>;
  }

  if (!data || data.nodes.length === 0) {
    return (
      <div className="space-y-6">
        <h2 className="text-2xl font-bold tracking-tight">Graph</h2>
        <p className="text-muted-foreground">No agents registered yet.</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold tracking-tight">Memory Graph</h2>

      <Card>
        <CardContent className="p-0 relative" style={{ height: dim.h }}>
          <svg ref={svgRef} width={dim.w} height={dim.h} className="overflow-visible">
            <defs>
              <marker id="arrowhead" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
                <polygon points="0 0, 8 3, 0 6" fill="#94a3b8" />
              </marker>
            </defs>

            {allEdges.map((e, i) => {
              const si = data.nodes.findIndex((x) => x.handle === e.source);
              const ti = data.nodes.findIndex((x) => x.handle === e.target);
              if (si === -1 || ti === -1) return null;
              const p1 = nodePositions[si];
              const p2 = nodePositions[ti];
              const color = e.type === "task" ? STATUS_COLORS[e.data.status] || "#94a3b8" : "#60a5fa";
              const dash = e.type === "message" ? "6,3" : undefined;
              const width = e.type === "task" ? 2 : 1.5;
              return (
                <line
                  key={`edge-${i}`}
                  x1={p1.x} y1={p1.y} x2={p2.x} y2={p2.y}
                  stroke={color} strokeWidth={width} strokeDasharray={dash}
                  opacity={0.5}
                />
              );
            })}

            {data.nodes.map((node, i) => {
              const p = nodePositions[i];
              const deg = degrees.get(node.handle) || 0;
              const r = 18 + Math.min(deg, 5) * 4;
              const fill = ROLE_COLORS[node.role] || "#22c55e";
              return (
                <g key={node.id} style={{ cursor: "pointer" }}
                  onMouseEnter={(e) => {
                    const rect = (e.target as SVGElement).closest("svg")!.getBoundingClientRect();
                    setTooltip({ node, x: p.x + r + 8, y: p.y - 10 });
                  }}
                  onMouseLeave={() => setTooltip(null)}
                >
                  {node.suspended && (
                    <circle cx={p.x} cy={p.y} r={r + 3} fill="none" stroke="#ef4444" strokeWidth={1.5} strokeDasharray="4,3" opacity={0.6} />
                  )}
                  <circle cx={p.x} cy={p.y} r={r} fill={fill} opacity={0.85} stroke="#fff" strokeWidth={2} />
                  <text x={p.x} y={p.y + 1} textAnchor="middle" dominantBaseline="central"
                    fill="#fff" fontSize={12} fontWeight={600} style={{ pointerEvents: "none" }}>
                    {node.handle.charAt(0).toUpperCase()}
                  </text>
                  <text x={p.x} y={p.y + r + 14} textAnchor="middle"
                    fill="currentColor" fontSize={11} className="fill-foreground">
                    {node.handle}
                  </text>
                  {node.suspended && (
                    <text x={p.x + r - 4} y={p.y - r + 4} textAnchor="middle"
                      fill="#ef4444" fontSize={10} fontWeight={600} style={{ pointerEvents: "none" }}>
                      ⛔
                    </text>
                  )}
                </g>
              );
            })}

            {tooltip && (
              <g>
                <rect x={tooltip.x} y={tooltip.y - 50} width={160} height={60} rx={4}
                  fill="#1e293b" opacity={0.95} />
                <text x={tooltip.x + 8} y={tooltip.y - 34} fill="#fff" fontSize={11} fontWeight={600}>
                  {tooltip.node.handle}
                </text>
                <text x={tooltip.x + 8} y={tooltip.y - 20} fill="#94a3b8" fontSize={10}>
                  Role: {tooltip.node.role}
                </text>
                <text x={tooltip.x + 8} y={tooltip.y - 8} fill="#94a3b8" fontSize={10}>
                  Provider: {tooltip.node.provider}
                </text>
                <text x={tooltip.x + 8} y={tooltip.y + 4} fill="#94a3b8" fontSize={10}>
                  Edges: {degrees.get(tooltip.node.handle) || 0}
                </text>
              </g>
            )}
          </svg>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Activity Timeline</CardTitle>
        </CardHeader>
        <CardContent className="max-h-64 overflow-y-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-muted-foreground border-b">
                <th className="text-left py-1 pr-2 w-6"></th>
                <th className="text-left py-1 pr-4">Event</th>
                <th className="text-left py-1 pr-4">Summary</th>
                <th className="text-right py-1">Time</th>
              </tr>
            </thead>
            <tbody>
              {data.timeline.map((ev: TimelineEvent, i: number) => (
                <tr key={i} className="border-b border-muted/30 hover:bg-muted/20">
                  <td className="py-1.5 pr-2">{timelineIcon(ev.type)}</td>
                  <td className="py-1.5 pr-4 font-mono text-muted-foreground">{ev.type}</td>
                  <td className="py-1.5 pr-4 max-w-[300px] truncate">{ev.summary}</td>
                  <td className="py-1.5 text-right text-muted-foreground whitespace-nowrap">{timeAgo(ev.at)}</td>
                </tr>
              ))}
              {data.timeline.length === 0 && (
                <tr><td colSpan={4} className="py-4 text-center text-muted-foreground">No activity yet</td></tr>
              )}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}
