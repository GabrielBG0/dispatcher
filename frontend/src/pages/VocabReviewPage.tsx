import { useEffect, useState } from "react";
import { confirmKanaOnly, deleteVocab, getKanjiCandidates, listVocab, updateVocab } from "../api/vocab";
import { ApiError } from "../api/client";
import type { KanjiCandidate, VocabListItem } from "../api/types";

const PAGE_SIZE = 25;

// Matches backend's extract_kanji() range (app/kanji_utils.py) -- used
// client-side only to decide whether "No kanji form exists" makes sense
// for the word's current (possibly edited) kanji_form.
const CJK_PATTERN = /[一-鿿㐀-䶿豈-﫿]/;

function VocabReviewRow({
  item,
  showAll,
  onResolved,
  onSaved,
}: {
  item: VocabListItem;
  showAll: boolean;
  onResolved: (id: number) => void;
  onSaved: (id: number, updated: { kanji_form: string; hiragana_form: string; meaning: string; usually_kana: boolean }) => void;
}) {
  const [kanjiForm, setKanjiForm] = useState(item.kanji_form);
  const [hiraganaForm, setHiraganaForm] = useState(item.hiragana_form);
  const [meaning, setMeaning] = useState(item.meaning);
  const [usuallyKana, setUsuallyKana] = useState(item.usually_kana);
  const [candidates, setCandidates] = useState<KanjiCandidate[] | null>(null);
  const [lookupBusy, setLookupBusy] = useState(false);
  const [saveBusy, setSaveBusy] = useState(false);
  const [confirmBusy, setConfirmBusy] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isDuplicateError, setIsDuplicateError] = useState(false);

  const dirty =
    kanjiForm !== item.kanji_form ||
    hiraganaForm !== item.hiragana_form ||
    meaning !== item.meaning ||
    usuallyKana !== item.usually_kana;

  async function handleLookup() {
    setLookupBusy(true);
    setError(null);
    try {
      // Search whatever's currently in the reading field, not necessarily
      // the row's saved hiragana_form -- lets a typo or alternate reading
      // be tried without saving it first.
      const { candidates } = await getKanjiCandidates(item.id, hiraganaForm);
      setCandidates(candidates);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Jisho lookup failed");
    } finally {
      setLookupBusy(false);
    }
  }

  function applyCandidate(candidate: KanjiCandidate) {
    setKanjiForm(candidate.word);
    setUsuallyKana(candidate.usually_kana);
    setMeaning(candidate.meaning);
  }

  async function handleSave() {
    setSaveBusy(true);
    setError(null);
    setIsDuplicateError(false);
    try {
      const updated = await updateVocab(item.id, {
        kanji_form: kanjiForm,
        hiragana_form: hiraganaForm,
        meaning,
        usually_kana: usuallyKana,
      });
      if (!showAll && CJK_PATTERN.test(updated.kanji_form)) {
        onResolved(item.id); // no longer kana-only -- drops out of the review queue on its own
      } else {
        onSaved(item.id, updated); // resets the row's dirty baseline to the saved values
      }
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Save failed";
      setError(message);
      // update_vocab's collision message -- the edit turned this row into a
      // duplicate of one that already has the right data, so the fix is
      // deleting the row being edited, not re-editing it further.
      setIsDuplicateError(message.includes("this looks like a duplicate"));
    } finally {
      setSaveBusy(false);
    }
  }

  async function handleConfirmNoKanji() {
    setConfirmBusy(true);
    setError(null);
    try {
      await confirmKanaOnly(item.id);
      if (!showAll) onResolved(item.id); // drops out of the kana-only queue; in "all vocab" search it just stays put
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to confirm");
    } finally {
      setConfirmBusy(false);
    }
  }

  async function handleDelete() {
    if (!window.confirm(`Delete this vocab entry (${item.hiragana_form})? This can't be undone.`)) return;
    setDeleteBusy(true);
    setError(null);
    try {
      await deleteVocab(item.id);
      onResolved(item.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Delete failed");
    } finally {
      setDeleteBusy(false);
    }
  }

  return (
    <div className="word-row" style={{ flexDirection: "column", alignItems: "stretch", gap: "0.5rem" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
        <div className="word-main">
          <span className="word-kanji">
            {item.kanji_form !== item.hiragana_form ? `${item.kanji_form} (${item.hiragana_form})` : item.hiragana_form}
          </span>{" "}
          <span className="pill">{item.status}</span>
          {item.assigned_batch !== null && <span className="pill"> batch {item.assigned_batch}</span>}
        </div>
      </div>

      <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
        <label style={{ fontSize: "0.8rem", color: "#666" }}>
          reading
          <input
            value={hiraganaForm}
            onChange={(e) => setHiraganaForm(e.target.value)}
            style={{ marginLeft: "0.4rem", width: "8rem" }}
          />
        </label>
        <label style={{ fontSize: "0.8rem", color: "#666" }}>
          kanji form
          <input
            value={kanjiForm}
            onChange={(e) => setKanjiForm(e.target.value)}
            style={{ marginLeft: "0.4rem", width: "8rem" }}
          />
        </label>
        <label style={{ fontSize: "0.8rem", color: "#666", flex: 1, minWidth: "16rem" }}>
          meaning
          <input
            value={meaning}
            onChange={(e) => setMeaning(e.target.value)}
            style={{ marginLeft: "0.4rem", width: "calc(100% - 4.5rem)" }}
          />
        </label>
        <label style={{ fontSize: "0.8rem", color: "#666" }}>
          <input
            type="checkbox"
            checked={usuallyKana}
            onChange={(e) => setUsuallyKana(e.target.checked)}
            style={{ marginRight: "0.3rem" }}
          />
          usually kana
        </label>
      </div>

      <div className="word-actions">
        <button onClick={handleLookup} disabled={lookupBusy}>
          {lookupBusy ? "Looking up…" : "Look up on Jisho"}
        </button>
        <button onClick={handleSave} disabled={saveBusy || !dirty}>
          {saveBusy ? "Saving…" : "Save"}
        </button>
        {!CJK_PATTERN.test(kanjiForm) && (
          <button onClick={handleConfirmNoKanji} disabled={confirmBusy}>
            {confirmBusy ? "…" : "No kanji form exists"}
          </button>
        )}
        <button onClick={handleDelete} disabled={deleteBusy} className="danger">
          {deleteBusy ? "Deleting…" : "Delete entry"}
        </button>
      </div>

      {candidates !== null && (
        <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap" }}>
          {candidates.length === 0 && <small style={{ color: "#999" }}>No kanji-bearing entries on Jisho.</small>}
          {candidates.map((c) => (
            <button
              key={c.word}
              onClick={() => applyCandidate(c)}
              className="target-kanji-chip"
              style={{ fontSize: "0.85rem", textAlign: "left" }}
              title={c.definitions.join(", ")}
            >
              {c.word}
              <span className="cover-count">
                {c.meaning} ({c.score.toFixed(2)})
                {c.usually_kana ? " · usually kana" : ""}
              </span>
            </button>
          ))}
        </div>
      )}

      {error && (
        <div className="error-box" style={{ marginBottom: 0 }}>
          {error}
          {isDuplicateError && (
            <div style={{ marginTop: "0.5rem" }}>
              <button onClick={handleDelete} disabled={deleteBusy} className="danger">
                {deleteBusy ? "Deleting…" : "Delete this entry"}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function VocabReviewPage() {
  const [items, setItems] = useState<VocabListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const result = await listVocab({
        kanaOnly: !showAll,
        includeReviewed: showAll,
        search: search || undefined,
        limit: PAGE_SIZE,
        offset,
      });
      setItems(result.items);
      setTotal(result.total);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load vocab");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [offset, search, showAll]);

  function handleResolved(id: number) {
    setItems((prev) => prev.filter((i) => i.id !== id));
    setTotal((prev) => Math.max(0, prev - 1));
  }

  function handleSaved(id: number, updated: { kanji_form: string; hiragana_form: string; meaning: string; usually_kana: boolean }) {
    setItems((prev) => prev.map((i) => (i.id === id ? { ...i, ...updated } : i)));
  }

  function handleSearchSubmit(e: React.FormEvent) {
    e.preventDefault();
    setOffset(0);
    setSearch(searchInput.trim());
  }

  function handleShowAllChange(checked: boolean) {
    setShowAll(checked);
    setOffset(0);
  }

  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <section className="card">
      <h2>Vocab review</h2>
      <p style={{ color: "#666", fontSize: "0.88rem", marginTop: 0 }}>
        Words still spelled with kana only (no real kanji recorded) -- left behind after the "Kana-only word
        kanji forms" enrichment job in Import couldn't confidently resolve them on its own. For each word, look
        up Jisho's candidate spellings and pick one, edit the meaning directly, or confirm the word is genuinely
        kana-only so it stops showing up here. Check "search all vocabulary" to find and edit any word instead.
      </p>

      <form onSubmit={handleSearchSubmit} className="upload-row">
        <input
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          placeholder="Search reading, kanji, or meaning…"
          style={{ flex: 1 }}
        />
        <button type="submit">Search</button>
        <label style={{ fontSize: "0.85rem", color: "#666", display: "flex", alignItems: "center", gap: "0.3rem" }}>
          <input
            type="checkbox"
            checked={showAll}
            onChange={(e) => handleShowAllChange(e.target.checked)}
          />
          search all vocabulary
        </label>
        <span>
          {total} word{total === 1 ? "" : "s"} {showAll ? "match" : "to review"}
        </span>
      </form>

      {error && <div className="error-box">{error}</div>}
      {loading && <p>Loading…</p>}

      <div className="word-list">
        {!loading &&
          items.map((item) => (
            <VocabReviewRow
              key={item.id}
              item={item}
              showAll={showAll}
              onResolved={handleResolved}
              onSaved={handleSaved}
            />
          ))}
        {!loading && items.length === 0 && (
          <p>{showAll ? "No vocab words match this search." : "No unresolved kana-only words match this search."}</p>
        )}
      </div>

      {total > PAGE_SIZE && (
        <div className="upload-row" style={{ marginTop: "1rem" }}>
          <button onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))} disabled={offset === 0 || loading}>
            Previous
          </button>
          <span>
            page {page} of {pageCount}
          </span>
          <button
            onClick={() => setOffset((o) => o + PAGE_SIZE)}
            disabled={offset + PAGE_SIZE >= total || loading}
          >
            Next
          </button>
        </div>
      )}
    </section>
  );
}
