export type StudioTaskStatus = "proposed" | "blocked" | "ready" | "running" | "waiting_external" | "succeeded" | "failed" | "cancelled";

export interface StudioTask {
  id: string;
  capability_id: string;
  objective: string;
  status: StudioTaskStatus;
  depends_on: string[];
}
