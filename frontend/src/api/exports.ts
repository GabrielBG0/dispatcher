import { apiGet } from "./client";
import type { ExportPreviewWord, PdfWarning } from "./types";

// fromBatch is the first week of a cumulative export's range (ignored when
// cumulative is false); batchN is always the last week.
export const getVocabTsv = (batchN: number, splitByPos: boolean, cumulative = false, fromBatch = 1) =>
  apiGet<Record<string, string>>(
    `/api/exports/${batchN}/vocab-tsv?split_by_pos=${splitByPos}&cumulative=${cumulative}&from_batch=${fromBatch}`,
  );

export const getKanjiTsv = (batchN: number, cumulative = false, fromBatch = 1) =>
  apiGet<Record<string, string>>(
    `/api/exports/${batchN}/kanji-tsv?cumulative=${cumulative}&from_batch=${fromBatch}`,
  );

export const getVocabTxt = (batchN: number, cumulative = false, fromBatch = 1) =>
  apiGet<Record<string, string>>(
    `/api/exports/${batchN}/vocab-txt?cumulative=${cumulative}&from_batch=${fromBatch}`,
  );

export const getExportPreview = (batchN: number, splitByPos: boolean) =>
  apiGet<ExportPreviewWord[]>(`/api/exports/${batchN}/preview?split_by_pos=${splitByPos}`);

export const getPdfWarnings = (batchN: number, cumulative = false, fromBatch = 1) =>
  apiGet<PdfWarning[]>(`/api/exports/${batchN}/pdf/warnings?cumulative=${cumulative}&from_batch=${fromBatch}`);

export const pdfDownloadUrl = (batchN: number, cumulative = false, fromBatch = 1) =>
  `/api/exports/${batchN}/pdf?cumulative=${cumulative}&from_batch=${fromBatch}`;

export function downloadTextFile(filename: string, content: string) {
  const blob = new Blob([content], { type: "text/tab-separated-values;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
