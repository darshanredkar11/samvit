import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type Settings as SettingsType } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { useToast } from "@/components/ui/toast";
import { RefreshCw, Radio, Activity } from "lucide-react";

export default function Settings() {
  const queryClient = useQueryClient();
  const { toast } = useToast();

  const { data: settings, isLoading } = useQuery<SettingsType>({
    queryKey: ["admin-settings"],
    queryFn: () => api.get("/v1/admin/settings"),
  });

  const updateMutation = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.post("/v1/admin/settings", body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-settings"] });
      toast({ title: "Settings updated", variant: "success" });
    },
    onError: (err: Error) => {
      toast({ title: "Failed to update settings", description: err.message, variant: "destructive" });
    },
  });

  const maintenanceMutation = useMutation({
    mutationFn: (enabled: boolean) => api.post("/v1/admin/maintenance", { enabled }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-settings"] });
      toast({ title: "Maintenance mode updated", variant: "success" });
    },
  });

  const hermesMutation = useMutation({
    mutationFn: () => api.post<{ crons_found?: number }>("/v1/admin/hermes/cron-sync"),
    onSuccess: (data) => {
      toast({ title: `Cron sync complete — ${data.crons_found || 0} crons found`, variant: "success" });
    },
    onError: (err: Error) => {
      toast({ title: "Cron sync failed", description: err.message, variant: "destructive" });
    },
  });

  const { data: eventsStatus } = useQuery<{ connected: boolean; degraded: boolean; published: number; failed: number }>({
    queryKey: ["admin-events-status"],
    queryFn: () => api.get("/v1/admin/events/status"),
    refetchInterval: 15000,
  });

  const guardMode = settings?.guard_mode || "redact";
  const maintenanceMode = settings?.maintenance_mode || false;

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold tracking-tight">Settings</h2>

      {isLoading && <p className="text-muted-foreground">Loading settings...</p>}

      <Card>
        <CardHeader>
          <CardTitle>Guard Mode</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground mb-4">
            Controls how sensitive data patterns are handled by the ethical guard.
          </p>
          <div className="flex items-center gap-4">
            <Select
              value={guardMode}
              onValueChange={(val) => updateMutation.mutate({ guard_mode: val })}
            >
              <SelectTrigger className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="off">Off</SelectItem>
                <SelectItem value="warn">Warn</SelectItem>
                <SelectItem value="redact">Redact</SelectItem>
                <SelectItem value="block">Block</SelectItem>
              </SelectContent>
            </Select>
            <span className="text-sm text-muted-foreground">
              Current: <strong>{guardMode}</strong>
            </span>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Maintenance Mode</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground mb-4">
            When enabled, all non-admin requests will be rejected.
          </p>
          <div className="flex items-center gap-3">
            <Switch
              checked={maintenanceMode}
              onCheckedChange={(checked) => maintenanceMutation.mutate(checked)}
            />
            <span className="text-sm">
              {maintenanceMode ? "Maintenance mode is ON" : "Maintenance mode is OFF"}
            </span>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Hermes Integration</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground mb-4">
            Sync cron definitions from Hermes config to Samvit tasks.
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => hermesMutation.mutate()}
            disabled={hermesMutation.isPending}
          >
            <RefreshCw className={`h-4 w-4 mr-2 ${hermesMutation.isPending ? "animate-spin" : ""}`} />
            {hermesMutation.isPending ? "Syncing..." : "Sync Cron Jobs"}
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Event Bus</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground mb-4">
            Redpanda/Kafka event bus telemetry.
          </p>
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <Radio className={`h-4 w-4 ${eventsStatus?.connected ? "text-green-500" : "text-red-500"}`} />
              <span className="text-sm">
                {eventsStatus?.connected ? "Connected" : "Disconnected"}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <Activity className="h-4 w-4 text-muted-foreground" />
              <span className="text-sm">{eventsStatus?.published || 0} published</span>
            </div>
            {eventsStatus && eventsStatus.failed > 0 && (
              <Badge variant="destructive">{eventsStatus.failed} failed</Badge>
            )}
          </div>
        </CardContent>
      </Card>

      <Separator />

      <p className="text-xs text-muted-foreground">
        Settings are persisted in the system_settings table and take effect immediately.
      </p>
    </div>
  );
}
