import { apiGet } from "./client";

export interface StudyConfigPayload {
  start_date: string;
  total_weeks: number;
  new_card_weeks: number;
  review_weeks: number;
  daily_minimum: number;
}

export const getConfig = () => apiGet<StudyConfigPayload | null>("/api/config");

export async function putConfig(payload: StudyConfigPayload): Promise<StudyConfigPayload> {
  const res = await fetch("/api/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
