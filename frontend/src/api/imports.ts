import { apiGet, apiPost, apiUpload } from "./client";
import type { EnrichmentJobStatus, ImportResult } from "./types";

export const uploadVocabList = (file: File) => apiUpload<ImportResult>("/api/imports/vocab-list", file);
export const uploadKanjiSchedule = (file: File) => apiUpload<ImportResult>("/api/imports/kanji-schedule", file);
export const uploadAnkiExport = (file: File) => apiUpload<ImportResult>("/api/imports/anki-export", file);

export const startVocabWordEnrichment = () => apiPost<{ job_id: number }>("/api/imports/enrich/vocab-words");
export const startVocabMeaningStandardization = () =>
  apiPost<{ job_id: number }>("/api/imports/enrich/vocab-meaning-standardization");
export const startKanaKanjiFormEnrichment = () =>
  apiPost<{ job_id: number }>("/api/imports/enrich/kana-kanji-forms");
export const startKanjiMeaningEnrichment = () => apiPost<{ job_id: number }>("/api/imports/enrich/kanji-meanings");
export const startKanjivgEnrichment = () => apiPost<{ job_id: number }>("/api/imports/enrich/kanjivg");

export const getJobStatus = (jobId: number) => apiGet<EnrichmentJobStatus>(`/api/imports/jobs/${jobId}`);
