import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Search, Eye } from "lucide-react";

function timeAgo(iso: string) {
  const sec = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const h = Math.floor(min / 60);
  return `${h}h ago`;
}

export default function KVMemory() {
  const [namespace, setNamespace] = useState("global");
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  const { data: keysData, isLoading } = useQuery({
    queryKey: ["admin-kv-list", namespace],
    queryFn: () => api.get<{ keys: { key: string; agent: string; updated_at: string }[] }>(
      `/v1/admin/memory/kv/${encodeURIComponent(namespace)}`
    ),
    enabled: namespace.length > 0,
    refetchInterval: 10000,
  });

  const { data: valueData } = useQuery({
    queryKey: ["admin-kv-get", namespace, selectedKey],
    queryFn: () => api.get<{ key: string; value: unknown; agent: string; updated_at: string }>(
      `/v1/admin/memory/kv/${encodeURIComponent(namespace)}/${encodeURIComponent(selectedKey!)}`
    ),
    enabled: selectedKey !== null,
  });

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold tracking-tight">KV Memory</h2>

      <div className="flex gap-4 items-end">
        <div className="space-y-2">
          <label className="text-sm font-medium">Namespace</label>
          <Input
            value={namespace}
            onChange={(e) => { setNamespace(e.target.value); setSelectedKey(null); }}
            placeholder="global"
            className="w-60"
          />
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => setNamespace(namespace)}
        >
          <Search className="h-4 w-4 mr-2" />
          Refresh
        </Button>
      </div>

      <div className="grid grid-cols-2 gap-6">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Keys in "{namespace}"</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Key</TableHead>
                  <TableHead>Agent</TableHead>
                  <TableHead>Updated</TableHead>
                  <TableHead></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {isLoading && (
                  <TableRow><TableCell colSpan={4} className="text-center text-muted-foreground">Loading...</TableCell></TableRow>
                )}
                {(keysData?.keys || []).map((k) => (
                  <TableRow key={k.key} className={selectedKey === k.key ? "bg-muted/50" : ""}>
                    <TableCell className="font-mono text-xs max-w-[200px] truncate">{k.key}</TableCell>
                    <TableCell className="text-xs">{k.agent}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">{timeAgo(k.updated_at)}</TableCell>
                    <TableCell>
                      <Button variant="ghost" size="sm" onClick={() => setSelectedKey(k.key)}>
                        <Eye className="h-3 w-3" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
                {keysData?.keys?.length === 0 && (
                  <TableRow><TableCell colSpan={4} className="text-center text-muted-foreground">No keys found</TableCell></TableRow>
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Value</CardTitle>
          </CardHeader>
          <CardContent>
            {selectedKey ? (
              valueData ? (
                <div className="space-y-4">
                  <div className="text-xs text-muted-foreground">
                    <span className="font-medium text-foreground">Key:</span> {valueData.key}
                    <br />
                    <span className="font-medium text-foreground">Agent:</span> {valueData.agent}
                    <br />
                    <span className="font-medium text-foreground">Updated:</span> {timeAgo(valueData.updated_at)}
                  </div>
                  <pre className="bg-muted p-3 rounded-md text-xs overflow-auto max-h-96 whitespace-pre-wrap break-all">
                    {typeof valueData.value === "object"
                      ? JSON.stringify(valueData.value, null, 2)
                      : String(valueData.value)}
                  </pre>
                </div>
              ) : (
                <p className="text-muted-foreground">Loading...</p>
              )
            ) : (
              <p className="text-muted-foreground">Select a key to view its value</p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
