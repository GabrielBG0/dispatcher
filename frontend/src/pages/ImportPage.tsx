import { useRef, useState } from "react";
import {
  getJobStatus,
  startKanjiMeaningEnrichment,
  startKanjivgEnrichment,
  startVocabWordEnrichment,
  uploadAnkiExport,
  uploadKanjiSchedule,
  uploadVocabList,
} from "../api/imports";
import { ApiError } from "../api/client";
import type { EnrichmentJobStatus, ImportResult } from "../api/types";

type Uploader = (file: File) => Promise<ImportResult>;

function UploadRow({ label, uploader }: { label: string; uploader: Uploader }) {
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  async function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const res = await uploader(file);
      setResult(res);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Upload failed");
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  return (
    <div>
      <div className="upload-row">
        <label>{label}</label>
        <input ref={inputRef} type="file" onChange={handleChange} disabled={busy} />
        {busy && <span>uploading…</span>}
      </div>
      {error && <div className="error-box">{error}</div>}
      {result && (
        <div className={result.warnings.length ? "warning-box" : "warning-box"} style={{ marginLeft: "1rem" }}>
          Parsed {result.parsed_rows} rows.{" "}
          {result.inserted !== undefined && (
            <>
              Inserted {result.inserted}, skipped {result.skipped_existing} already-existing
              {result.updated ? `, updated ${result.updated}` : ""}.
            </>
          )}
          {result.vocab_marked_seen_in_class !== undefined && (
            <>
              Marked {result.vocab_marked_seen_in_class} vocab rows seen_in_class; recorded{" "}
              {result.kanji_coverage_inserted} new pre_n3 kanji coverage rows (
              {result.kanji_coverage_already_present} already present).
            </>
          )}
          {result.warnings.length > 0 && (
            <ul>
              {result.warnings.slice(0, 20).map((w, i) => (
                <li key={i}>
                  row {w.row_number}: {w.reason}
                </li>
              ))}
              {result.warnings.length > 20 && <li>…and {result.warnings.length - 20} more</li>}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function EnrichmentJobButton({
  label,
  start,
}: {
  label: string;
  start: () => Promise<{ job_id: number }>;
}) {
  const [job, setJob] = useState<EnrichmentJobStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  function stopPolling() {
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }

  async function handleStart() {
    setError(null);
    try {
      const { job_id } = await start();
      pollRef.current = window.setInterval(async () => {
        try {
          const status = await getJobStatus(job_id);
          setJob(status);
          if (status.status === "completed" || status.status === "failed") {
            stopPolling();
          }
        } catch {
          stopPolling();
        }
      }, 1500);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to start job");
    }
  }

  const running = job !== null && (job.status === "pending" || job.status === "running");
  const pct = job && job.total > 0 ? Math.round((job.completed / job.total) * 100) : 0;

  return (
    <div className="upload-row">
      <label>{label}</label>
      <button onClick={handleStart} disabled={running}>
        {running ? "Running…" : "Start"}
      </button>
      {job && (
        <div style={{ flex: 1 }}>
          <div className="progress-bar">
            <div className="progress-bar-fill" style={{ width: `${pct}%` }} />
          </div>
          <small>
            {job.status} — {job.completed}/{job.total}
            {job.error ? `: ${job.error}` : ""}
          </small>
          {job.status === "completed" && job.not_found > 0 && (
            <div className="warning-box">
              {job.not_found} of {job.total} item{job.not_found === 1 ? "" : "s"} not found on Jisho — left
              unenriched, review before finalizing batches.
            </div>
          )}
        </div>
      )}
      {error && <div className="error-box">{error}</div>}
    </div>
  );
}

export default function ImportPage() {
  return (
    <div>
      <section className="card">
        <h2>Import source files</h2>
        <UploadRow label="N3 vocab list (.xls)" uploader={uploadVocabList} />
        <UploadRow label="Kanji weekly schedule (.xlsx)" uploader={uploadKanjiSchedule} />
        <UploadRow label="Genki Anki export (.tsv)" uploader={uploadAnkiExport} />
      </section>

      <section className="card">
        <h2>Enrichment</h2>
        <p style={{ color: "#666", fontSize: "0.88rem", marginTop: 0 }}>
          Fetches missing data from Jisho.org and KanjiVG. Cached in the database -- safe to re-run, already
          enriched rows are skipped.
        </p>
        <EnrichmentJobButton label="Kanji stroke data (KanjiVG)" start={startKanjivgEnrichment} />
        <EnrichmentJobButton label="Kanji meanings/readings (Jisho)" start={startKanjiMeaningEnrichment} />
        <EnrichmentJobButton label="Vocab word meanings (Jisho)" start={startVocabWordEnrichment} />
      </section>
    </div>
  );
}
