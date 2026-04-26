type ApiErrorStateProps = {
  title: string;
  message: string;
};

export function ApiErrorState({ title, message }: ApiErrorStateProps) {
  return (
    <div className="clinical-panel border-l-[3px] border-l-act p-5">
      <p className="label-caps text-act">API unavailable</p>
      <h2 className="mt-3 font-display text-xl font-semibold">{title}</h2>
      <p className="mt-2 max-w-[66ch] text-sm leading-6 text-ink-muted">{message}</p>
      <p className="mt-4 rounded border border-line bg-surface-muted p-3 font-data text-xs text-ink-muted">
        Start the FastAPI backend in the intended mode, then run the Vite frontend so relative
        <span className="font-semibold text-ink"> /api </span>
        calls proxy to the backend. Use demo and fixture modes only for explicit seeded-demo runs.
      </p>
    </div>
  );
}

export function EmptyState({ title, message }: { title: string; message: string }) {
  return (
    <div className="clinical-panel p-8 text-center">
      <p className="label-caps text-ink-muted">No records</p>
      <h2 className="mt-3 font-display text-xl font-semibold">{title}</h2>
      <p className="mx-auto mt-2 max-w-[56ch] text-sm leading-6 text-ink-muted">{message}</p>
    </div>
  );
}

export function TableSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="clinical-panel overflow-hidden">
      <div className="border-b border-line bg-surface-muted p-5">
        <div className="h-3 w-36 animate-pulse rounded bg-surface-strong" />
      </div>
      <div className="divide-y divide-line">
        {Array.from({ length: rows }).map((_, index) => (
          <div className="grid grid-cols-4 gap-4 p-5" key={index}>
            <div className="h-3 animate-pulse rounded bg-surface-strong" />
            <div className="h-3 animate-pulse rounded bg-surface-strong" />
            <div className="h-3 animate-pulse rounded bg-surface-strong" />
            <div className="h-3 animate-pulse rounded bg-surface-strong" />
          </div>
        ))}
      </div>
    </div>
  );
}

export function PanelSkeleton() {
  return (
    <div className="grid gap-gutter lg:grid-cols-[1.25fr_0.75fr]">
      <div className="clinical-panel p-5">
        <div className="h-3 w-40 animate-pulse rounded bg-surface-strong" />
        <div className="mt-5 h-8 w-3/4 animate-pulse rounded bg-surface-strong" />
        <div className="mt-4 h-3 w-full animate-pulse rounded bg-surface-strong" />
        <div className="mt-2 h-3 w-5/6 animate-pulse rounded bg-surface-strong" />
      </div>
      <div className="grid gap-gutter sm:grid-cols-2 lg:grid-cols-1">
        {Array.from({ length: 3 }).map((_, index) => (
          <div className="clinical-panel p-5" key={index}>
            <div className="h-4 w-20 animate-pulse rounded bg-surface-strong" />
            <div className="mt-5 h-5 w-2/3 animate-pulse rounded bg-surface-strong" />
            <div className="mt-3 h-3 w-full animate-pulse rounded bg-surface-strong" />
          </div>
        ))}
      </div>
    </div>
  );
}
