import { apiDelete, apiGet, apiPatch, apiPost } from "./client";
import type {
  DuplicateGroup,
  KanjiCandidate,
  ResolveDuplicatesResult,
  UpdateVocabPayload,
  VocabListResponse,
} from "./types";

export interface ListVocabParams {
  kanaOnly?: boolean;
  includeReviewed?: boolean;
  search?: string;
  limit?: number;
  offset?: number;
}

export function listVocab(params: ListVocabParams = {}) {
  const query = new URLSearchParams();
  if (params.kanaOnly !== undefined) query.set("kana_only", String(params.kanaOnly));
  if (params.includeReviewed !== undefined) query.set("include_reviewed", String(params.includeReviewed));
  if (params.search) query.set("search", params.search);
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  if (params.offset !== undefined) query.set("offset", String(params.offset));
  return apiGet<VocabListResponse>(`/api/vocab?${query.toString()}`);
}

export const getKanjiCandidates = (vocabId: number, reading?: string) => {
  const query = reading ? `?reading=${encodeURIComponent(reading)}` : "";
  return apiGet<{ candidates: KanjiCandidate[] }>(`/api/vocab/${vocabId}/kanji-candidates${query}`);
};

export const updateVocab = (vocabId: number, payload: UpdateVocabPayload) =>
  apiPatch<{ id: number; kanji_form: string; hiragana_form: string; meaning: string; usually_kana: boolean }>(
    `/api/vocab/${vocabId}`,
    payload,
  );

export const confirmKanaOnly = (vocabId: number) =>
  apiPost<{ id: number; source: string }>(`/api/vocab/${vocabId}/confirm-kana-only`);

export const deleteVocab = (vocabId: number) => apiDelete<{ deleted: number }>(`/api/vocab/${vocabId}`);

export const getDuplicateVocabGroups = () => apiGet<DuplicateGroup[]>("/api/vocab/duplicates");

export const resolveDuplicateVocabGroup = (keepId: number, deleteIds: number[]) =>
  apiPost<ResolveDuplicatesResult>("/api/vocab/duplicates/resolve", {
    keep_id: keepId,
    delete_ids: deleteIds,
  });
