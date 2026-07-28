export interface ImportResult {
  parsed_rows: number;
  warnings: { row_number: number; reason: string; raw?: unknown }[];
  inserted?: number;
  skipped_existing?: number;
  updated?: number;
  vocab_marked_seen_in_class?: number;
  kanji_coverage_inserted?: number;
  kanji_coverage_already_present?: number;
}

export interface EnrichmentJobStatus {
  id: number;
  job_type: string;
  status: "pending" | "running" | "completed" | "failed";
  total: number;
  completed: number;
  not_found: number;
  error: string | null;
}

export interface GenerateBatchResult {
  batch_number: number;
  weekly_target_used: number;
  pacing_floor: number;
  behind_pace: boolean;
  selected_count: number;
  target_kanji_coverage: Record<string, number[]>;
  warnings: {
    kind: string;
    detail: string;
    kanji: string | null;
    cause?: string | null;
    blocking_kanji?: string | null;
  }[];
}

export interface BatchWord {
  vocab_id: number;
  kanji_form: string;
  hiragana_form: string;
  meaning: string;
  is_target_linked: boolean;
  needs_kanji_reading: boolean;
  usually_kana: boolean;
  covers_target_kanji: string[];
  used_seen_in_class_fallback: boolean;
}

export interface BatchDetail {
  batch_number: number;
  status: "draft" | "finalized" | "exported";
  weekly_target_used: number;
  target_kanji: string[];
  target_kanji_coverage: Record<string, number[]>;
  words: BatchWord[];
}

export interface KanjiWordOption {
  vocab_id: number;
  kanji_form: string;
  hiragana_form: string;
  meaning: string;
  usually_kana: boolean;
  status: string;
  assigned_batch: number | null;
  assigned_batch_status: string | null;
  core_rank: number | null;
}

export interface JishoWordSuggestion {
  kanji_form: string;
  hiragana_form: string;
  meaning: string;
  part_of_speech: string;
  jlpt: string[];
  is_common: boolean;
  includable: boolean;
  blocking_kanji: string | null;
  blocking_batch: number | null;
  seen_kanji: string[];
}

export interface KanjiWordOptions {
  kanji: string;
  in_batch: KanjiWordOption[];
  other_batches: KanjiWordOption[];
  top_common: KanjiWordOption[];
}

export interface ReplacementCandidate {
  vocab_id: number;
  kanji_form: string;
  hiragana_form: string;
  usually_kana: boolean;
}

export interface ReplaceResult {
  removed_vocab_id: number;
  added: ReplacementCandidate | null;
}

export interface BulkRemoveResult {
  removed_vocab_ids: number[];
}

export interface BulkReplaceResult {
  results: ReplaceResult[];
}

export interface PdfWarning {
  kanji: string;
  detail: string;
}

export interface ExportPreviewCard {
  front: string;
  back: string;
  deck: string;
  tags: string;
}

export interface ExportPreviewWord {
  vocab_id: number;
  kanji_form: string;
  hiragana_form: string;
  meaning: string;
  part_of_speech: string;
  usually_kana: boolean;
  needs_kanji_reading: boolean;
  covers_target_kanji: string[];
  vocab_card: ExportPreviewCard;
  kanji_reading_card: ExportPreviewCard | null;
}

export interface EditWordPayload {
  kanji_form?: string;
  hiragana_form?: string;
  meaning?: string;
  part_of_speech?: string;
  usually_kana?: boolean;
}

export interface BatchSummary {
  batch_number: number;
  status: string;
  weekly_target_used: number;
  word_count: number;
}

export interface VocabListItem {
  id: number;
  kanji_form: string;
  hiragana_form: string;
  meaning: string;
  part_of_speech: string;
  status: string;
  assigned_batch: number | null;
  usually_kana: boolean;
  source: string;
}

export interface VocabListResponse {
  total: number;
  items: VocabListItem[];
}

export interface KanjiCandidate {
  word: string;
  definitions: string[];
  meaning: string;
  score: number;
  usually_kana: boolean;
}

export interface UpdateVocabPayload {
  kanji_form?: string;
  hiragana_form?: string;
  meaning?: string;
  usually_kana?: boolean;
  part_of_speech?: string;
}

export interface DuplicateVocabRow {
  id: number;
  meaning: string;
  status: string;
  assigned_batch: number | null;
  source: string;
}

export interface DuplicateGroup {
  kanji_form: string;
  hiragana_form: string;
  similarity: number;
  suggested_keep_id: number;
  auto_resolvable: boolean;
  reason: string;
  rows: DuplicateVocabRow[];
}

export interface ResolveDuplicatesResult {
  kept: number;
  deleted: number[];
}

export interface DashboardOverview {
  words_total: number;
  words_seen_in_class: number;
  words_available: number;
  words_assigned: number;
  words_excluded: number;
  study_end_date: string | null;
  weeks_remaining: number | null;
  behind_pace: boolean;
  batches: BatchSummary[];
}
