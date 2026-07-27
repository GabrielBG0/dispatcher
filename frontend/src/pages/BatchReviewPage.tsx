import { useEffect, useState } from "react";
import { ApiError } from "../api/client";
import {
  addWord,
  bulkRemoveWords,
  bulkReplaceWords,
  excludeKanjiWord,
  finalizeBatch,
  generateDraft,
  getBatch,
  getEligibleReplacements,
  getKanjiWordOptions,
  importJishoWord,
  includeKanjiWord,
  removeWord,
  replaceWord,
  searchJishoWordSuggestions,
  swapWord,
  toggleReading,
  unfinalizeBatch,
} from "../api/batches";
import type {
  BatchDetail,
  GenerateBatchResult,
  JishoWordSuggestion,
  KanjiWordOption,
  KanjiWordOptions,
  ReplacementCandidate,
} from "../api/types";

type ConfirmAction = { kind: "remove" | "replace"; vocabIds: number[] };

const CAUSE_LABELS: Record<string, string> = {
  no_vocab_in_source: "no N3 word contains this kanji",
  blocked_by_future_kanji: "blocked by the skip-ahead guard",
  other_status_exclusion: "covering word(s) assigned to another batch or excluded",
};

function causeLabel(cause: string): string {
  return CAUSE_LABELS[cause] ?? cause;
}

function kanjiOptionLocationLabel(w: KanjiWordOption, batchN: number): string {
  if (w.assigned_batch === batchN) return "in this batch";
  if (w.assigned_batch !== null) return `in batch ${w.assigned_batch} (${w.assigned_batch_status})`;
  if (w.status === "seen_in_class") return "seen in class";
  if (w.status === "excluded") return "excluded";
  return "available";
}

function KanjiWordGroup({
  title,
  words,
  batchN,
  isDraft,
  pending,
  onInclude,
  onExclude,
  emptyLabel,
}: {
  title: string;
  words: KanjiWordOption[];
  batchN: number;
  isDraft: boolean;
  pending: boolean;
  onInclude: (vocabId: number) => void;
  onExclude: (vocabId: number) => void;
  emptyLabel: string;
}) {
  return (
    <div style={{ marginBottom: "1.25rem" }}>
      <h4 style={{ marginBottom: "0.4rem" }}>{title}</h4>
      <div className="word-list">
        {words.length === 0 && <p style={{ color: "#666", fontSize: "0.85rem" }}>{emptyLabel}</p>}
        {words.map((w) => {
          const inThisBatch = w.assigned_batch === batchN;
          const inOtherDraftBatch =
            w.assigned_batch !== null && w.assigned_batch !== batchN && w.assigned_batch_status === "draft";
          const locked = w.assigned_batch !== null && w.assigned_batch !== batchN && w.assigned_batch_status !== "draft";

          return (
            <div className="word-row" key={w.vocab_id}>
              <div className="word-main">
                <div className="word-kanji">
                  {w.kanji_form}
                  {w.kanji_form !== w.hiragana_form ? `（${w.hiragana_form}）` : ""}
                </div>
                <div className="word-meaning">{w.meaning || "(no meaning yet)"}</div>
              </div>
              <span className="pill">{kanjiOptionLocationLabel(w, batchN)}</span>
              {w.usually_kana && <span className="pill kana">usu. kana</span>}
              {w.core_rank !== null ? (
                <span className="pill">rank {w.core_rank}</span>
              ) : (
                <span className="pill">no frequency data</span>
              )}
              {isDraft && (
                <div className="word-actions">
                  {locked && <span style={{ fontSize: "0.8rem", color: "#666" }}>locked, edit in that batch</span>}
                  {!locked && inThisBatch && (
                    <button className="danger" disabled={pending} onClick={() => onExclude(w.vocab_id)}>
                      Exclude
                    </button>
                  )}
                  {!locked && inOtherDraftBatch && (
                    <button disabled={pending} onClick={() => onInclude(w.vocab_id)}>
                      Move here from batch {w.assigned_batch}
                    </button>
                  )}
                  {!locked && !inThisBatch && !inOtherDraftBatch && (
                    <>
                      <button disabled={pending} onClick={() => onInclude(w.vocab_id)}>
                        Include
                      </button>
                      {w.status !== "excluded" && (
                        <button className="danger" disabled={pending} onClick={() => onExclude(w.vocab_id)}>
                          Exclude
                        </button>
                      )}
                    </>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function jishoSuggestionKey(s: JishoWordSuggestion): string {
  return `${s.kanji_form}::${s.hiragana_form}`;
}

function JishoSuggestionGroup({
  suggestions,
  isDraft,
  pending,
  selectedKeys,
  onToggleSelect,
  onIncludeSelected,
  onInclude,
}: {
  suggestions: JishoWordSuggestion[];
  isDraft: boolean;
  pending: boolean;
  selectedKeys: Set<string>;
  onToggleSelect: (key: string) => void;
  onIncludeSelected: () => void;
  onInclude: (s: JishoWordSuggestion) => void;
}) {
  if (suggestions.length === 0) return null;
  const multiSelectable = isDraft && suggestions.length > 1;
  const selectedCount = suggestions.filter((s) => selectedKeys.has(jishoSuggestionKey(s))).length;
  return (
    <div style={{ marginBottom: "1.25rem" }}>
      <h4 style={{ marginBottom: "0.4rem" }}>More options from Jisho (not already in your local list)</h4>
      {multiSelectable && selectedCount > 0 && (
        <div className="upload-row">
          <span className="pill">{selectedCount} selected</span>
          <button disabled={pending} onClick={onIncludeSelected}>
            Include selected
          </button>
        </div>
      )}
      <div className="word-list">
        {suggestions.map((s) => {
          const key = jishoSuggestionKey(s);
          return (
            <div className="word-row" key={key}>
              {multiSelectable && (
                <input
                  type="checkbox"
                  checked={selectedKeys.has(key)}
                  disabled={!s.includable}
                  onChange={() => onToggleSelect(key)}
                />
              )}
              <div className="word-main">
                <div className="word-kanji">
                  {s.kanji_form}
                  {s.kanji_form !== s.hiragana_form ? `（${s.hiragana_form}）` : ""}
                </div>
                <div className="word-meaning">{s.meaning || "(no meaning yet)"}</div>
              </div>
              {s.jlpt.length > 0 && <span className="pill">{s.jlpt.join(", ")}</span>}
              {s.is_common && <span className="pill ok">common</span>}
              {s.seen_kanji.length > 0 && (
                <span className="pill kana">with seen kanji: {s.seen_kanji.join(", ")}</span>
              )}
              {!s.includable && (
                <span className="pill warn">
                  for later{s.blocking_kanji ? ` — ${s.blocking_kanji} is in batch ${s.blocking_batch}` : ""}
                </span>
              )}
              {isDraft && (
                <div className="word-actions">
                  <button disabled={pending || !s.includable} onClick={() => onInclude(s)}>
                    Include
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

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
  const [selectedKanji, setSelectedKanji] = useState<string | null>(null);
  const [kanjiOptions, setKanjiOptions] = useState<KanjiWordOptions | null>(null);
  const [kanjiLoading, setKanjiLoading] = useState(false);
  const [kanjiError, setKanjiError] = useState<string | null>(null);
  const [kanjiActionPending, setKanjiActionPending] = useState(false);
  const [selectedJishoKeys, setSelectedJishoKeys] = useState<Set<string>>(new Set());
  const [jishoSuggestions, setJishoSuggestions] = useState<JishoWordSuggestion[] | null>(null);
  const [jishoSearchLoading, setJishoSearchLoading] = useState(false);
  const [jishoSearchError, setJishoSearchError] = useState<string | null>(null);

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
    setSelectedKanji(null);
    setKanjiOptions(null);
    load(batchN);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batchN]);

  // Deliberately does not clear kanjiError itself -- callers control that,
  // so a follow-up reload after an action can't stomp on an error message
  // the action just set (e.g. which of several bulk-included words failed).
  async function loadKanjiOptions(kanji: string) {
    setKanjiLoading(true);
    try {
      const opts = await getKanjiWordOptions(batchN, kanji);
      setKanjiOptions(opts);
    } catch (err) {
      setKanjiError(err instanceof ApiError ? err.message : "Failed to load kanji detail");
    } finally {
      setKanjiLoading(false);
    }
  }

  function handleKanjiClick(kanji: string) {
    setSelectedJishoKeys(new Set());
    setKanjiError(null);
    setJishoSuggestions(null);
    setJishoSearchError(null);
    if (selectedKanji === kanji) {
      setSelectedKanji(null);
      setKanjiOptions(null);
      return;
    }
    setSelectedKanji(kanji);
    loadKanjiOptions(kanji);
  }

  // Deliberately does not clear jishoSearchError itself, for the same
  // reason loadKanjiOptions doesn't clear kanjiError -- see above.
  async function runJishoSearch(kanji: string) {
    setJishoSearchLoading(true);
    try {
      const { jisho_suggestions } = await searchJishoWordSuggestions(batchN, kanji);
      setJishoSuggestions(jisho_suggestions);
    } catch (err) {
      setJishoSearchError(err instanceof ApiError ? err.message : "Failed to search Jisho");
    } finally {
      setJishoSearchLoading(false);
    }
  }

  function handleSearchJisho() {
    if (!selectedKanji) return;
    setJishoSearchError(null);
    runJishoSearch(selectedKanji);
  }

  function toggleJishoSelected(key: string) {
    setSelectedJishoKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  async function handleKanjiInclude(vocabId: number) {
    if (!selectedKanji) return;
    setKanjiError(null);
    setKanjiActionPending(true);
    try {
      await includeKanjiWord(batchN, vocabId);
      await Promise.all([load(batchN), loadKanjiOptions(selectedKanji)]);
    } catch (err) {
      setKanjiError(err instanceof ApiError ? err.message : "Failed to include word");
    } finally {
      setKanjiActionPending(false);
    }
  }

  async function handleKanjiExclude(vocabId: number) {
    if (!selectedKanji) return;
    setKanjiError(null);
    setKanjiActionPending(true);
    try {
      await excludeKanjiWord(batchN, vocabId);
      await Promise.all([load(batchN), loadKanjiOptions(selectedKanji)]);
    } catch (err) {
      setKanjiError(err instanceof ApiError ? err.message : "Failed to exclude word");
    } finally {
      setKanjiActionPending(false);
    }
  }

  async function handleKanjiImportJisho(suggestion: JishoWordSuggestion) {
    if (!selectedKanji) return;
    setKanjiError(null);
    setKanjiActionPending(true);
    try {
      await importJishoWord(batchN, suggestion);
      setSelectedJishoKeys((prev) => {
        const next = new Set(prev);
        next.delete(jishoSuggestionKey(suggestion));
        return next;
      });
      const reloads = [load(batchN), loadKanjiOptions(selectedKanji)];
      if (jishoSuggestions !== null) reloads.push(runJishoSearch(selectedKanji));
      await Promise.all(reloads);
    } catch (err) {
      setKanjiError(err instanceof ApiError ? err.message : "Failed to import word from Jisho");
    } finally {
      setKanjiActionPending(false);
    }
  }

  async function handleKanjiImportJishoSelected() {
    if (!selectedKanji || !jishoSuggestions) return;
    const toInclude = jishoSuggestions.filter((s) => selectedJishoKeys.has(jishoSuggestionKey(s)));
    if (toInclude.length === 0) return;
    setKanjiError(null);
    setKanjiActionPending(true);
    const failures: string[] = [];
    for (const s of toInclude) {
      try {
        await importJishoWord(batchN, s);
      } catch (err) {
        failures.push(`${s.kanji_form} (${err instanceof ApiError ? err.message : "failed"})`);
      }
    }
    setSelectedJishoKeys(new Set());
    await Promise.all([load(batchN), loadKanjiOptions(selectedKanji), runJishoSearch(selectedKanji)]);
    if (failures.length > 0) {
      setKanjiError(`${failures.length} of ${toInclude.length} word(s) could not be included: ${failures.join("; ")}`);
    }
    setKanjiActionPending(false);
  }

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
                  <li key={i} className={w.kind === "covered_by_seen_in_class_fallback" ? "info" : undefined}>
                    {w.detail}
                    {w.cause && <span className="pill warn"> {causeLabel(w.cause)}</span>}
                  </li>
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
            <p style={{ marginTop: "-0.5rem", color: "#666", fontSize: "0.85rem" }}>
              Click a kanji to see and adjust the words covering it.
            </p>
            <div className="target-kanji-grid">
              {detail.target_kanji.map((k) => {
                const count = detail.target_kanji_coverage[k]?.length ?? 0;
                return (
                  <div
                    key={k}
                    className={`target-kanji-chip ${count === 0 ? "uncovered" : ""} ${
                      selectedKanji === k ? "selected" : ""
                    }`}
                    onClick={() => handleKanjiClick(k)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") handleKanjiClick(k);
                    }}
                  >
                    {k}
                    <span className="cover-count">{count} word(s)</span>
                  </div>
                );
              })}
            </div>
          </section>

          {selectedKanji && (
            <section className="card">
              <div className="upload-row" style={{ justifyContent: "space-between" }}>
                <h3 style={{ marginTop: 0 }}>Kanji detail: {selectedKanji}</h3>
                <button
                  onClick={() => {
                    setSelectedKanji(null);
                    setKanjiOptions(null);
                  }}
                >
                  Close
                </button>
              </div>
              {kanjiError && <div className="error-box">{kanjiError}</div>}
              {kanjiLoading && <p>Loading…</p>}
              {kanjiOptions && (
                <>
                  <KanjiWordGroup
                    title={`In this batch (${kanjiOptions.in_batch.length})`}
                    words={kanjiOptions.in_batch}
                    batchN={batchN}
                    isDraft={isDraft}
                    pending={kanjiActionPending}
                    onInclude={handleKanjiInclude}
                    onExclude={handleKanjiExclude}
                    emptyLabel="No word in this batch covers this kanji yet."
                  />
                  <KanjiWordGroup
                    title={`In other batches (${kanjiOptions.other_batches.length})`}
                    words={kanjiOptions.other_batches}
                    batchN={batchN}
                    isDraft={isDraft}
                    pending={kanjiActionPending}
                    onInclude={handleKanjiInclude}
                    onExclude={handleKanjiExclude}
                    emptyLabel="No word claimed by another batch contains this kanji."
                  />
                  <KanjiWordGroup
                    title="Top 10 most common words with this kanji (BCCWJ frequency, where available)"
                    words={kanjiOptions.top_common}
                    batchN={batchN}
                    isDraft={isDraft}
                    pending={kanjiActionPending}
                    onInclude={handleKanjiInclude}
                    onExclude={handleKanjiExclude}
                    emptyLabel="No word in the vocab list contains this kanji."
                  />
                  <div style={{ marginBottom: "1.25rem" }}>
                    <div className="upload-row">
                      <button disabled={jishoSearchLoading} onClick={handleSearchJisho}>
                        {jishoSearchLoading
                          ? "Searching Jisho…"
                          : jishoSuggestions === null
                            ? "Search Jisho for more options"
                            : "Search Jisho again"}
                      </button>
                    </div>
                    {jishoSearchError && <div className="error-box">{jishoSearchError}</div>}
                    {jishoSuggestions !== null && jishoSuggestions.length === 0 && !jishoSearchError && (
                      <p style={{ color: "#666", fontSize: "0.85rem" }}>
                        No additional words found on Jisho for this kanji.
                      </p>
                    )}
                    <JishoSuggestionGroup
                      suggestions={jishoSuggestions ?? []}
                      isDraft={isDraft}
                      pending={kanjiActionPending}
                      selectedKeys={selectedJishoKeys}
                      onToggleSelect={toggleJishoSelected}
                      onIncludeSelected={handleKanjiImportJishoSelected}
                      onInclude={handleKanjiImportJisho}
                    />
                  </div>
                </>
              )}
            </section>
          )}

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
                  className={`word-row ${justReplacedIds.has(w.vocab_id) ? "just-replaced" : ""} ${
                    w.used_seen_in_class_fallback ? "seen-in-class-fallback" : ""
                  }`}
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
                  {w.used_seen_in_class_fallback && <span className="pill fallback">already seen in class</span>}
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
