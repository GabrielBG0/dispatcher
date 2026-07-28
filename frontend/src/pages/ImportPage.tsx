import { useRef, useState } from "react";
import {
  getJobStatus,
  startKanaKanjiFormEnrichment,
  startKanjiMeaningEnrichment,
  startKanjivgEnrichment,
  startVocabMeaningStandardization,
  startVocabWordEnrichment,
  uploadAnkiExport,
  uploadKanjiSchedule,
  uploadVocabList,
} from "../api/imports";
import { getDuplicateVocabGroups, resolveDuplicateVocabGroup } from "../api/vocab";
import { ApiError } from "../api/client";
import type { DuplicateGroup, EnrichmentJobStatus, ImportResult } from "../api/types";

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

function DuplicateGroupCard({
  group,
  onResolved,
}: {
  group: DuplicateGroup;
  onResolved: (group: DuplicateGroup) => void;
}) {
  const [keepId, setKeepId] = useState(group.suggested_keep_id);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleResolve() {
    setBusy(true);
    setError(null);
    try {
      const deleteIds = group.rows.filter((r) => r.id !== keepId).map((r) => r.id);
      await resolveDuplicateVocabGroup(keepId, deleteIds);
      onResolved(group);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to resolve");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="word-list" style={{ marginBottom: "1.25rem" }}>
      <div>
        <strong style={{ fontSize: "1.05rem" }}>{group.kanji_form}</strong>
        {group.kanji_form !== group.hiragana_form && <span>（{group.hiragana_form}）</span>}{" "}
        <span className={`pill ${group.auto_resolvable ? "ok" : "warn"}`}>
          {group.auto_resolvable ? "safe to resolve" : "needs review"}
        </span>
        <div style={{ color: "#666", fontSize: "0.85rem" }}>{group.reason}</div>
      </div>
      {group.rows.map((row) => (
        <label key={row.id} className="word-row" style={{ cursor: "pointer" }}>
          <input
            type="radio"
            name={`dup-${group.kanji_form}-${group.hiragana_form}`}
            checked={keepId === row.id}
            onChange={() => setKeepId(row.id)}
          />
          <div className="word-main">
            <div className="word-meaning">{row.meaning || "(blank)"}</div>
            <small style={{ color: "#999" }}>
              id={row.id} · status={row.status}
              {row.assigned_batch !== null ? ` · batch ${row.assigned_batch}` : ""} · source={row.source}
            </small>
          </div>
        </label>
      ))}
      <div className="upload-row">
        <button onClick={handleResolve} disabled={busy}>
          {busy ? "Resolving…" : `Keep selected, delete the other${group.rows.length > 2 ? "s" : ""}`}
        </button>
      </div>
      {error && <div className="error-box">{error}</div>}
    </div>
  );
}

function DuplicateVocabPanel() {
  const [groups, setGroups] = useState<DuplicateGroup[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function scan() {
    setLoading(true);
    setError(null);
    try {
      setGroups(await getDuplicateVocabGroups());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to scan for duplicates");
    } finally {
      setLoading(false);
    }
  }

  function handleResolved(resolved: DuplicateGroup) {
    setGroups((prev) => (prev ?? []).filter((g) => g !== resolved));
  }

  return (
    <section className="card">
      <h2>Duplicate vocab words</h2>
      <p style={{ color: "#666", fontSize: "0.88rem", marginTop: 0 }}>
        Finds vocab rows with the exact same spelling and an overlapping meaning -- usually left behind by
        importing the same word list twice. Rows with genuinely different meanings that just happen to share a
        spelling (real homophones) are never flagged. Resolving deletes the row(s) you don't keep; the kept row's
        data is never changed.
      </p>
      <div className="upload-row">
        <button onClick={scan} disabled={loading}>
          {loading ? "Scanning…" : "Scan for duplicates"}
        </button>
        {groups !== null && <span>{groups.length} group(s) found</span>}
      </div>
      {error && <div className="error-box">{error}</div>}
      {groups?.map((g) => (
        <DuplicateGroupCard key={`${g.kanji_form} ${g.hiragana_form}`} group={g} onResolved={handleResolved} />
      ))}
      {groups !== null && groups.length === 0 && <p>No duplicates found.</p>}
    </section>
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
        <EnrichmentJobButton
          label="Kana-only word kanji forms (Jisho)"
          start={startKanaKanjiFormEnrichment}
        />
        <p style={{ color: "#666", fontSize: "0.88rem" }}>
          The button below OVERWRITES existing meaning text on rows that don't yet look like a Jisho-formatted
          meaning (numbered "1 - ... 2 - ..." senses, or a single sense with 2+ "/"-joined synonyms) -- use it to
          standardize meanings left over from the original vocab list import. Also re-derives each word's category
          (verb/adjective/adverb/vocab) from the same Jisho lookup, and never pulls in Wikipedia-sourced
          definitions.
        </p>
        <EnrichmentJobButton
          label="Standardize vocab meanings (Jisho)"
          start={startVocabMeaningStandardization}
        />
      </section>

      <DuplicateVocabPanel />
    </div>
  );
}
