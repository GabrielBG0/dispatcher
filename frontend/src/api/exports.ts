import { apiGet } from "./client";
import type { ExportPreviewWord, PdfWarning } from "./types";

export const getVocabTsv = (batchN: number, splitByPos: boolean) =>
  apiGet<Record<string, string>>(`/api/exports/${batchN}/vocab-tsv?split_by_pos=${splitByPos}`);

export const getKanjiTsv = (batchN: number) =>
  apiGet<Record<string, string>>(`/api/exports/${batchN}/kanji-tsv`);

export const getExportPreview = (batchN: number, splitByPos: boolean) =>
  apiGet<ExportPreviewWord[]>(`/api/exports/${batchN}/preview?split_by_pos=${splitByPos}`);

export const getPdfWarnings = (batchN: number) => apiGet<PdfWarning[]>(`/api/exports/${batchN}/pdf/warnings`);

export const pdfDownloadUrl = (batchN: number) => `/api/exports/${batchN}/pdf`;

export function downloadTextFile(filename: string, content: string) {
  const blob = new Blob([content], { type: "text/tab-separated-values;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
