import { useEffect, useState } from "react";
import { ApiError } from "../api/client";
import {
  addWord,
  bulkRemoveWords,
  bulkReplaceWords,
  finalizeBatch,
  generateDraft,
  getBatch,
  getEligibleReplacements,
  removeWord,
  replaceWord,
  swapWord,
  toggleReading,
  unfinalizeBatch,
} from "../api/batches";
import type { BatchDetail, GenerateBatchResult, ReplacementCandidate } from "../api/types";

type ConfirmAction = { kind: "remove" | "replace"; vocabIds: number[] };

export default function BatchReviewPage() {
  const [batchN, setBatchN] = useState(1);
  const [detail, setDetail] = useState<BatchDetail | null>(null);
  const [generateResult, setGenerateResult] = useState<GenerateBatchResult | null>(null);
  const [replacements, setReplacements] = useState<ReplacementCandidate[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [swapTarget, setSwapTarget] = useState<number | null>(null);
  const [addPickId, setAddPickId] = useState<number | "">("");
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [confirmAction, setConfirmAction] = useState<ConfirmAction | null>(null);
  const [justReplacedIds, setJustReplacedIds] = useState<Set<number>>(new Set());

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
    setJustReplacedIds(new Set());
    load(batchN);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batchN]);

  async function handleGenerate() {
    setError(null);
    try {
      const result = await generateDraft(batchN);
      setGenerateResult(result);
      setJustReplacedIds(new Set());
      await load(batchN);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to generate draft");
    }
  }

  function toggleSelected(vocabId: number) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(vocabId)) next.delete(vocabId);
      else next.add(vocabId);
      return next;
    });
  }

  function requestRemove(vocabIds: number[]) {
    setNotice(null);
    setConfirmAction({ kind: "remove", vocabIds });
  }

  function requestReplace(vocabIds: number[]) {
    setNotice(null);
    setConfirmAction({ kind: "replace", vocabIds });
  }

  async function runConfirmedAction(exclude: boolean) {
    if (!confirmAction) return;
    const { kind, vocabIds } = confirmAction;
    setConfirmAction(null);
    setError(null);
    setNotice(null);
    try {
      if (kind === "remove") {
        setJustReplacedIds(new Set());
        if (vocabIds.length === 1) {
          await removeWord(batchN, vocabIds[0], exclude);
        } else {
          await bulkRemoveWords(batchN, vocabIds, exclude);
        }
      } else if (vocabIds.length === 1) {
        const result = await replaceWord(batchN, vocabIds[0], exclude);
        if (!result.added) setNotice("Removed, but no eligible replacement was available.");
        setJustReplacedIds(result.added ? new Set([result.added.vocab_id]) : new Set());
      } else {
        const { results } = await bulkReplaceWords(batchN, vocabIds, exclude);
        const missing = results.filter((r) => !r.added).length;
        if (missing > 0) setNotice(`${missing} of ${results.length} word(s) had no eligible replacement available.`);
        setJustReplacedIds(new Set(results.filter((r) => r.added).map((r) => r.added!.vocab_id)));
      }
      setSelectedIds(new Set());
      await load(batchN);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : `Failed to ${kind} word(s)`);
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
      setJustReplacedIds(new Set([newVocabId]));
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
  const candidateLabel = (r: ReplacementCandidate) =>
    `${r.kanji_form}（${r.hiragana_form}）${r.usually_kana ? " · usu. kana" : ""}`;
  const sortedWords = detail
    ? [...detail.words].sort((a, b) => Number(justReplacedIds.has(b.vocab_id)) - Number(justReplacedIds.has(a.vocab_id)))
    : [];

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
        {notice && <div className="warning-box">{notice}</div>}
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
                      {candidateLabel(r)}
                    </option>
                  ))}
                </select>
                <button onClick={handleAdd} disabled={addPickId === ""}>
                  Add
                </button>
              </div>
            )}

            {isDraft && selectedIds.size > 0 && (
              <div className="upload-row">
                <span className="pill">{selectedIds.size} selected</span>
                <button onClick={() => requestReplace([...selectedIds])}>Replace selected</button>
                <button className="danger" onClick={() => requestRemove([...selectedIds])}>
                  Remove selected
                </button>
                <button onClick={() => setSelectedIds(new Set())}>Clear selection</button>
              </div>
            )}

            <div className="word-list">
              {sortedWords.map((w) => (
                <div
                  className={`word-row ${justReplacedIds.has(w.vocab_id) ? "just-replaced" : ""}`}
                  key={w.vocab_id}
                >
                  {isDraft && (
                    <input
                      type="checkbox"
                      checked={selectedIds.has(w.vocab_id)}
                      onChange={() => toggleSelected(w.vocab_id)}
                    />
                  )}
                  <div className="word-main">
                    <div className="word-kanji">
                      {w.kanji_form}
                      {w.kanji_form !== w.hiragana_form ? `（${w.hiragana_form}）` : ""}
                    </div>
                    <div className="word-meaning">{w.meaning || "(no meaning yet)"}</div>
                  </div>
                  {w.usually_kana && <span className="pill kana">usu. kana</span>}
                  {justReplacedIds.has(w.vocab_id) && <span className="pill ok">new</span>}
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
                      <button onClick={() => requestReplace([w.vocab_id])}>Replace</button>
                      <button className="danger" onClick={() => requestRemove([w.vocab_id])}>
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

      {confirmAction && (
        <div className="modal-overlay" onClick={() => setConfirmAction(null)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <h3>
              {confirmAction.kind === "remove" ? "Remove" : "Replace"} {confirmAction.vocabIds.length}{" "}
              word{confirmAction.vocabIds.length > 1 ? "s" : ""}?
            </h3>
            <p>
              Should {confirmAction.vocabIds.length > 1 ? "these words" : "this word"} also be excluded from
              future batches, or just taken out of this one?
            </p>
            <div className="modal-actions">
              <button onClick={() => setConfirmAction(null)}>Cancel</button>
              <button onClick={() => runConfirmedAction(false)}>
                {confirmAction.kind === "remove" ? "Remove only" : "Replace only"}
              </button>
              <button className="primary" onClick={() => runConfirmedAction(true)}>
                Exclude from future batches
              </button>
            </div>
          </div>
        </div>
      )}

      {!detail && !loading && (
        <p style={{ color: "#666" }}>No batch {batchN} yet — click "Generate draft" to create one.</p>
      )}
    </div>
  );
}
