import { apiGet } from "./client";
import type { DashboardOverview } from "./types";

export const getOverview = () => apiGet<DashboardOverview>("/api/dashboard/overview");
