import { apiDelete, apiGet, apiPost } from "./client";
import type {
  BatchDetail,
  BulkRemoveResult,
  BulkReplaceResult,
  GenerateBatchResult,
  ReplacementCandidate,
  ReplaceResult,
} from "./types";

export const generateDraft = (batchN: number) =>
  apiPost<GenerateBatchResult>(`/api/batches/${batchN}/generate`);

export const getBatch = (batchN: number) => apiGet<BatchDetail>(`/api/batches/${batchN}`);

export const getEligibleReplacements = (batchN: number) =>
  apiGet<ReplacementCandidate[]>(`/api/batches/${batchN}/eligible-replacements`);

export const removeWord = (batchN: number, vocabId: number, exclude = false) =>
  apiDelete<{ ok: boolean }>(`/api/batches/${batchN}/words/${vocabId}?exclude=${exclude}`);

export const bulkRemoveWords = (batchN: number, vocabIds: number[], exclude = false) =>
  apiPost<BulkRemoveResult>(`/api/batches/${batchN}/words/bulk-remove`, { vocab_ids: vocabIds, exclude });

export const replaceWord = (batchN: number, vocabId: number, exclude = false) =>
  apiPost<ReplaceResult>(`/api/batches/${batchN}/words/${vocabId}/replace?exclude=${exclude}`);

export const bulkReplaceWords = (batchN: number, vocabIds: number[], exclude = false) =>
  apiPost<BulkReplaceResult>(`/api/batches/${batchN}/words/bulk-replace`, { vocab_ids: vocabIds, exclude });

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
