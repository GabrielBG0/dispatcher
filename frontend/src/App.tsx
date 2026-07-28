import { NavLink, Route, Routes } from "react-router-dom";
import "./App.css";
import DashboardPage from "./pages/DashboardPage";
import ImportPage from "./pages/ImportPage";
import BatchReviewPage from "./pages/BatchReviewPage";
import ExportPage from "./pages/ExportPage";
import VocabReviewPage from "./pages/VocabReviewPage";

function App() {
  return (
    <div className="app-shell">
      <header className="app-header">
        <h1>dispatcher</h1>
        <nav>
          <NavLink to="/" end>
            Dashboard
          </NavLink>
          <NavLink to="/import">Import</NavLink>
          <NavLink to="/vocab-review">Vocab Review</NavLink>
          <NavLink to="/batches">Batch Review</NavLink>
          <NavLink to="/export">Export</NavLink>
        </nav>
      </header>
      <main className="app-main">
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/import" element={<ImportPage />} />
          <Route path="/vocab-review" element={<VocabReviewPage />} />
          <Route path="/batches" element={<BatchReviewPage />} />
          <Route path="/export" element={<ExportPage />} />
        </Routes>
      </main>
    </div>
  );
}

export default App;
