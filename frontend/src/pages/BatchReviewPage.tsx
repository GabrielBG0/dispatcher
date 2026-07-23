import { useEffect, useState } from "react";
import { ApiError } from "../api/client";
import {
  addWord,
  finalizeBatch,
  generateDraft,
  getBatch,
  getEligibleReplacements,
  removeWord,
  swapWord,
  toggleReading,
  unfinalizeBatch,
} from "../api/batches";
import type { BatchDetail, GenerateBatchResult, ReplacementCandidate } from "../api/types";

export default function BatchReviewPage() {
  const [batchN, setBatchN] = useState(1);
  const [detail, setDetail] = useState<BatchDetail | null>(null);
  const [generateResult, setGenerateResult] = useState<GenerateBatchResult | null>(null);
  const [replacements, setReplacements] = useState<ReplacementCandidate[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [swapTarget, setSwapTarget] = useState<number | null>(null);
  const [addPickId, setAddPickId] = useState<number | "">("");

  async function load(n: number) {
    setLoading(true);
    setError(null);
    try {
      const [d, r] = await Promise.all([getBatch(n), getEligibleReplacements(n)]);
      setDetail(d);
      setReplacements(r);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setDetail(null);
      } else {
        setError(err instanceof ApiError ? err.message : "Failed to load batch");
      }
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    setGenerateResult(null);
    load(batchN);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batchN]);

  async function handleGenerate() {
    setError(null);
    try {
      const result = await generateDraft(batchN);
      setGenerateResult(result);
      await load(batchN);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to generate draft");
    }
  }

  async function handleRemove(vocabId: number) {
    setError(null);
    try {
      await removeWord(batchN, vocabId);
      await load(batchN);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to remove word");
    }
  }

  async function handleToggle(vocabId: number) {
    setError(null);
    try {
      await toggleReading(batchN, vocabId);
      await load(batchN);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to toggle reading card");
    }
  }

  async function handleSwap(oldVocabId: number, newVocabId: number) {
    setError(null);
    try {
      await swapWord(batchN, oldVocabId, newVocabId);
      setSwapTarget(null);
      await load(batchN);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to swap word");
    }
  }

  async function handleAdd() {
    if (addPickId === "") return;
    setError(null);
    try {
      await addWord(batchN, addPickId as number);
      setAddPickId("");
      await load(batchN);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to add word");
    }
  }

  async function handleFinalize() {
    setError(null);
    try {
      await finalizeBatch(batchN);
      await load(batchN);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to finalize batch");
    }
  }

  async function handleUnfinalize() {
    setError(null);
    try {
      await unfinalizeBatch(batchN);
      await load(batchN);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to un-finalize batch");
    }
  }

  const isDraft = detail?.status === "draft";
  const targetLinkedCount = detail?.words.filter((w) => w.is_target_linked).length ?? 0;
  const fillerCount = (detail?.words.length ?? 0) - targetLinkedCount;
  const assignedIds = new Set(detail?.words.map((w) => w.vocab_id));
  const availableForAdd = replacements.filter((r) => !assignedIds.has(r.vocab_id));

  return (
    <div>
      <section className="card">
        <h2>Batch review</h2>
        <div className="upload-row">
          <label>Batch number</label>
          <input
            type="number"
            min={1}
            value={batchN}
            onChange={(e) => setBatchN(Number(e.target.value) || 1)}
            style={{ width: "5rem" }}
          />
          <button className="primary" onClick={handleGenerate} disabled={detail !== null && !isDraft}>
            {detail ? "Regenerate draft" : "Generate draft"}
          </button>
          {detail?.status === "draft" && <button onClick={handleFinalize}>Finalize</button>}
          {detail?.status === "finalized" && (
            <button className="danger" onClick={handleUnfinalize}>
              Un-finalize
            </button>
          )}
          {detail && <span className="pill">{detail.status}</span>}
        </div>
        {error && <div className="error-box">{error}</div>}
        {loading && <p>Loading…</p>}
      </section>

      {generateResult && (
        <section className="card">
          <h3 style={{ marginTop: 0 }}>Generation result</h3>
          <div className="stat-row">
            <div className="stat-tile">
              <div className="value">{generateResult.weekly_target_used}</div>
              <div className="label">weekly target used</div>
            </div>
            <div className="stat-tile">
              <div className="value">{generateResult.selected_count}</div>
              <div className="label">words selected</div>
            </div>
            <div className="stat-tile">
              <span className={`pill ${generateResult.behind_pace ? "behind" : "ok"}`}>
                {generateResult.behind_pace ? "behind pace" : "on pace"}
              </span>
              <div className="label">
                floor: {generateResult.pacing_floor}
              </div>
            </div>
          </div>
          {generateResult.warnings.length > 0 && (
            <div className="warning-box">
              <strong>{generateResult.warnings.length} warning(s):</strong>
              <ul>
                {generateResult.warnings.map((w, i) => (
                  <li key={i}>{w.detail}</li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}

      {detail && (
        <>
          <section className="card">
            <h3 style={{ marginTop: 0 }}>Target kanji coverage ({detail.target_kanji.length})</h3>
            <div className="target-kanji-grid">
              {detail.target_kanji.map((k) => {
                const count = detail.target_kanji_coverage[k]?.length ?? 0;
                return (
                  <div key={k} className={`target-kanji-chip ${count === 0 ? "uncovered" : ""}`}>
                    {k}
                    <span className="cover-count">{count} word(s)</span>
                  </div>
                );
              })}
            </div>
          </section>

          <section className="card">
            <h3 style={{ marginTop: 0 }}>
              Words ({detail.words.length}) — {targetLinkedCount} target-linked, {fillerCount} filler
            </h3>

            {isDraft && (
              <div className="upload-row">
                <label>Add word</label>
                <select value={addPickId} onChange={(e) => setAddPickId(e.target.value ? Number(e.target.value) : "")}>
                  <option value="">Select a word…</option>
                  {availableForAdd.map((r) => (
                    <option key={r.vocab_id} value={r.vocab_id}>
                      {r.kanji_form}（{r.hiragana_form}）
                    </option>
                  ))}
                </select>
                <button onClick={handleAdd} disabled={addPickId === ""}>
                  Add
                </button>
              </div>
            )}

            <div className="word-list">
              {detail.words.map((w) => (
                <div className="word-row" key={w.vocab_id}>
                  <div className="word-main">
                    <div className="word-kanji">
                      {w.kanji_form}
                      {w.kanji_form !== w.hiragana_form ? `（${w.hiragana_form}）` : ""}
                    </div>
                    <div className="word-meaning">{w.meaning || "(no meaning yet)"}</div>
                  </div>
                  <span className={`pill ${w.is_target_linked ? "ok" : ""}`}>
                    {w.is_target_linked ? `covers ${w.covers_target_kanji.join(", ")}` : "filler"}
                  </span>
                  {isDraft && (
                    <div className="word-actions">
                      <label style={{ fontSize: "0.8rem" }}>
                        <input
                          type="checkbox"
                          checked={w.needs_kanji_reading}
                          onChange={() => handleToggle(w.vocab_id)}
                        />{" "}
                        reading card
                      </label>
                      {swapTarget === w.vocab_id ? (
                        <select
                          autoFocus
                          onChange={(e) => {
                            if (e.target.value) handleSwap(w.vocab_id, Number(e.target.value));
                          }}
                        >
                          <option value="">Pick replacement…</option>
                          {availableForAdd.map((r) => (
                            <option key={r.vocab_id} value={r.vocab_id}>
                              {r.kanji_form}（{r.hiragana_form}）
                            </option>
                          ))}
                        </select>
                      ) : (
                        <button onClick={() => setSwapTarget(w.vocab_id)}>Swap</button>
                      )}
                      <button className="danger" onClick={() => handleRemove(w.vocab_id)}>
                        Remove
                      </button>
                    </div>
                  )}
                </div>
              ))}
              {detail.words.length === 0 && <p style={{ color: "#666" }}>No words assigned yet.</p>}
            </div>
          </section>
        </>
      )}

      {!detail && !loading && (
        <p style={{ color: "#666" }}>No batch {batchN} yet — click "Generate draft" to create one.</p>
      )}
    </div>
  );
}
