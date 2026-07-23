import { useState } from "react";
import { ApiError } from "../api/client";
import { downloadTextFile, getKanjiTsv, getPdfWarnings, getVocabTsv, pdfDownloadUrl } from "../api/exports";
import type { PdfWarning } from "../api/types";

export default function ExportPage() {
  const [batchN, setBatchN] = useState(1);
  const [splitByPos, setSplitByPos] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pdfWarnings, setPdfWarnings] = useState<PdfWarning[] | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleVocabDownload() {
    setError(null);
    setBusy(true);
    try {
      const files = await getVocabTsv(batchN, splitByPos);
      for (const [filename, content] of Object.entries(files)) {
        downloadTextFile(filename, content);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to export vocab TSV");
    } finally {
      setBusy(false);
    }
  }

  async function handleKanjiDownload() {
    setError(null);
    setBusy(true);
    try {
      const files = await getKanjiTsv(batchN);
      for (const [filename, content] of Object.entries(files)) {
        downloadTextFile(filename, content);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to export kanji reading TSV");
    } finally {
      setBusy(false);
    }
  }

  async function handleCheckPdfWarnings() {
    setError(null);
    try {
      setPdfWarnings(await getPdfWarnings(batchN));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to check PDF warnings");
    }
  }

  return (
    <div>
      <section className="card">
        <h2>Export a finalized batch</h2>
        <div className="upload-row">
          <label>Batch number</label>
          <input
            type="number"
            min={1}
            value={batchN}
            onChange={(e) => setBatchN(Number(e.target.value) || 1)}
            style={{ width: "5rem" }}
          />
        </div>
        {error && <div className="error-box">{error}</div>}
      </section>

      <section className="card">
        <h3 style={{ marginTop: 0 }}>Vocab Anki deck</h3>
        <div className="upload-row">
          <label>
            <input type="checkbox" checked={splitByPos} onChange={(e) => setSplitByPos(e.target.checked)} />{" "}
            split by part of speech
          </label>
          <button className="primary" onClick={handleVocabDownload} disabled={busy}>
            Download vocab TSV{splitByPos ? "s" : ""}
          </button>
        </div>
      </section>

      <section className="card">
        <h3 style={{ marginTop: 0 }}>Kanji reading deck</h3>
        <button className="primary" onClick={handleKanjiDownload} disabled={busy}>
          Download kanji-reading TSV
        </button>
      </section>

      <section className="card">
        <h3 style={{ marginTop: 0 }}>Weekly kanji PDF</h3>
        <div className="upload-row">
          <button onClick={handleCheckPdfWarnings}>Check for missing data</button>
          <a href={pdfDownloadUrl(batchN)}>
            <button className="primary">Download PDF</button>
          </a>
        </div>
        {pdfWarnings && pdfWarnings.length > 0 && (
          <div className="warning-box">
            <strong>{pdfWarnings.length} kanji missing data:</strong>
            <ul>
              {pdfWarnings.map((w, i) => (
                <li key={i}>
                  {w.kanji}: {w.detail}
                </li>
              ))}
            </ul>
          </div>
        )}
        {pdfWarnings && pdfWarnings.length === 0 && (
          <p style={{ color: "#666" }}>All target kanji have stroke data and enrichment data.</p>
        )}
      </section>
    </div>
  );
}
