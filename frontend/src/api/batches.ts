import { apiDelete, apiGet, apiPost } from "./client";
import type {
  BatchDetail,
  BulkRemoveResult,
  BulkReplaceResult,
  EditWordPayload,
  GenerateBatchResult,
  JishoWordSuggestion,
  KanjiWordOptions,
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

export const getKanjiWordOptions = (batchN: number, kanji: string) =>
  apiGet<KanjiWordOptions>(`/api/batches/${batchN}/kanji/${encodeURIComponent(kanji)}`);

export const searchJishoWordSuggestions = (batchN: number, kanji: string) =>
  apiGet<{ kanji: string; jisho_suggestions: JishoWordSuggestion[] }>(
    `/api/batches/${batchN}/kanji/${encodeURIComponent(kanji)}/jisho-search`,
  );

export const includeKanjiWord = (batchN: number, vocabId: number) =>
  apiPost<{ ok: boolean }>(`/api/batches/${batchN}/kanji-words/${vocabId}/include`);

export const excludeKanjiWord = (batchN: number, vocabId: number) =>
  apiPost<{ ok: boolean }>(`/api/batches/${batchN}/kanji-words/${vocabId}/exclude`);

export const importJishoWord = (batchN: number, suggestion: JishoWordSuggestion) =>
  apiPost<{ ok: boolean; vocab_id: number }>(`/api/batches/${batchN}/kanji-words/import-jisho`, {
    kanji_form: suggestion.kanji_form,
    hiragana_form: suggestion.hiragana_form,
    meaning: suggestion.meaning,
    part_of_speech: suggestion.part_of_speech,
  });

export const editWord = (batchN: number, vocabId: number, payload: EditWordPayload) =>
  apiPost<{ ok: boolean }>(`/api/batches/${batchN}/words/${vocabId}/edit`, payload);

export const finalizeBatch = (batchN: number) =>
  apiPost<{ batch_number: number; status: string }>(`/api/batches/${batchN}/finalize`);

export const unfinalizeBatch = (batchN: number) =>
  apiPost<{ batch_number: number; status: string }>(`/api/batches/${batchN}/unfinalize`);
