import { useEffect, useState } from "react";
import { ApiError } from "../api/client";
import { getConfig, putConfig, type StudyConfigPayload } from "../api/config";
import { getOverview } from "../api/dashboard";
import type { DashboardOverview } from "../api/types";

const DEFAULT_CONFIG: StudyConfigPayload = {
  start_date: new Date().toISOString().slice(0, 10),
  total_weeks: 19,
  new_card_weeks: 16,
  review_weeks: 3,
  daily_minimum: 18,
};

function StudyConfigCard({ onSaved }: { onSaved: () => void }) {
  const [config, setConfig] = useState<StudyConfigPayload>(DEFAULT_CONFIG);
  const [exists, setExists] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getConfig().then((c) => {
      if (c) {
        setConfig(c);
        setExists(true);
      }
    });
  }, []);

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      await putConfig(config);
      setExists(true);
      onSaved();
    } catch {
      setError("Failed to save study config");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="card">
      <h3 style={{ marginTop: 0 }}>Study config {!exists && <span className="pill warn">not set</span>}</h3>
      <div className="stat-row" style={{ alignItems: "flex-end" }}>
        <div>
          <div className="label">start date</div>
          <input
            type="date"
            value={config.start_date}
            onChange={(e) => setConfig({ ...config, start_date: e.target.value })}
          />
        </div>
        <div>
          <div className="label">new-card weeks</div>
          <input
            type="number"
            style={{ width: "4.5rem" }}
            value={config.new_card_weeks}
            onChange={(e) => setConfig({ ...config, new_card_weeks: Number(e.target.value) })}
          />
        </div>
        <div>
          <div className="label">review weeks</div>
          <input
            type="number"
            style={{ width: "4.5rem" }}
            value={config.review_weeks}
            onChange={(e) => setConfig({ ...config, review_weeks: Number(e.target.value) })}
          />
        </div>
        <div>
          <div className="label">daily minimum</div>
          <input
            type="number"
            style={{ width: "4.5rem" }}
            value={config.daily_minimum}
            onChange={(e) => setConfig({ ...config, daily_minimum: Number(e.target.value) })}
          />
        </div>
        <button className="primary" onClick={handleSave} disabled={saving}>
          Save
        </button>
      </div>
      {error && <div className="error-box">{error}</div>}
    </section>
  );
}

export default function DashboardPage() {
  const [overview, setOverview] = useState<DashboardOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

  function load() {
    getOverview()
      .then(setOverview)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Failed to load overview"));
  }

  useEffect(load, []);

  return (
    <div>
      <StudyConfigCard onSaved={load} />

      {error && <div className="error-box">{error}</div>}
      {!overview && !error && <p>Loading…</p>}

      {overview && (
        <>
          <section className="card">
            <h2>Overview</h2>
            <div className="stat-row">
              <div className="stat-tile">
                <div className="value">{overview.words_total}</div>
                <div className="label">total vocab</div>
              </div>
              <div className="stat-tile">
                <div className="value">{overview.words_available}</div>
                <div className="label">remaining to assign</div>
              </div>
              <div className="stat-tile">
                <div className="value">{overview.words_assigned}</div>
                <div className="label">assigned to batches</div>
              </div>
              <div className="stat-tile">
                <div className="value">{overview.words_seen_in_class}</div>
                <div className="label">already seen in class</div>
              </div>
              <div className="stat-tile">
                <div className="value">{overview.weeks_remaining ?? "—"}</div>
                <div className="label">weeks remaining</div>
              </div>
              <div className="stat-tile">
                <span className={`pill ${overview.behind_pace ? "behind" : "ok"}`}>
                  {overview.behind_pace ? "behind pace" : "on pace"}
                </span>
                <div className="label">
                  {overview.study_end_date ? `ends ${overview.study_end_date}` : "no study_config yet"}
                </div>
              </div>
            </div>
          </section>

          <section className="card">
            <h3 style={{ marginTop: 0 }}>Batch history</h3>
            {overview.batches.length === 0 ? (
              <p style={{ color: "#666" }}>No batches generated yet.</p>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Batch</th>
                    <th>Status</th>
                    <th>Weekly target</th>
                    <th>Words</th>
                  </tr>
                </thead>
                <tbody>
                  {overview.batches.map((b) => (
                    <tr key={b.batch_number}>
                      <td>{b.batch_number}</td>
                      <td>
                        <span className="pill">{b.status}</span>
                      </td>
                      <td>{b.weekly_target_used}</td>
                      <td>{b.word_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </>
      )}
    </div>
  );
}
