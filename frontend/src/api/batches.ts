import { apiDelete, apiGet, apiPost } from "./client";
import type { BatchDetail, GenerateBatchResult, ReplacementCandidate } from "./types";

export const generateDraft = (batchN: number) =>
  apiPost<GenerateBatchResult>(`/api/batches/${batchN}/generate`);

export const getBatch = (batchN: number) => apiGet<BatchDetail>(`/api/batches/${batchN}`);

export const getEligibleReplacements = (batchN: number) =>
  apiGet<ReplacementCandidate[]>(`/api/batches/${batchN}/eligible-replacements`);

export const removeWord = (batchN: number, vocabId: number) =>
  apiDelete<{ ok: boolean }>(`/api/batches/${batchN}/words/${vocabId}`);

export const addWord = (batchN: number, vocabId: number) =>
  apiPost<{ ok: boolean }>(`/api/batches/${batchN}/words/${vocabId}`);

export const toggleReading = (batchN: number, vocabId: number) =>
  apiPost<{ needs_kanji_reading: boolean }>(`/api/batches/${batchN}/words/${vocabId}/toggle-reading`);

export const swapWord = (batchN: number, oldVocabId: number, newVocabId: number) =>
  apiPost<{ ok: boolean }>(
    `/api/batches/${batchN}/words/${oldVocabId}/swap?replacement_vocab_id=${newVocabId}`,
  );

export const finalizeBatch = (batchN: number) =>
  apiPost<{ batch_number: number; status: string }>(`/api/batches/${batchN}/finalize`);

export const unfinalizeBatch = (batchN: number) =>
  apiPost<{ batch_number: number; status: string }>(`/api/batches/${batchN}/unfinalize`);
