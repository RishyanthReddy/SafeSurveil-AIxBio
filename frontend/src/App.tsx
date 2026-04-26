import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "./components/AppShell";
import { AnalystQueueRoute } from "./routes/AnalystQueueRoute";
import { AnalysisSubmissionRoute } from "./routes/AnalysisSubmissionRoute";
import { CaseDetailRoute } from "./routes/CaseDetailRoute";
import { DashboardOverviewRoute } from "./routes/DashboardOverviewRoute";
import { EvaluationRoute } from "./routes/EvaluationRoute";
import { FallbackRendererRoute } from "./routes/FallbackRendererRoute";

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<DashboardOverviewRoute />} />
        <Route path="analysis/new" element={<AnalysisSubmissionRoute />} />
        <Route path="queue" element={<AnalystQueueRoute />} />
        <Route path="cases/:jobId" element={<CaseDetailRoute />} />
        <Route path="evaluation" element={<EvaluationRoute />} />
        <Route path="fallback-renderer" element={<FallbackRendererRoute />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
