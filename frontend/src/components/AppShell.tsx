import {
  Biohazard,
  Bell,
  BracketsCurly,
  Database,
  GearSix,
  Lifebuoy,
  ListChecks,
  MagnifyingGlass,
  Plus,
  Question,
  ShieldCheck,
  SquaresFour,
  Terminal,
} from "@phosphor-icons/react";
import { useState, type FormEvent } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";

import { fetchHealthStatus } from "../api/client";
import type { RuntimeModeReport } from "../api/types";
import { useApiResource } from "../hooks/useApiResource";

type NavItem = {
  label: string;
  to: string;
  icon: typeof SquaresFour;
  end?: boolean;
};

const primaryNav: NavItem[] = [
  { label: "Dashboard", to: "/", icon: SquaresFour, end: true },
  { label: "Analyst Queue", to: "/queue", icon: ListChecks },
  { label: "Fallback Renderer", to: "/fallback-renderer", icon: BracketsCurly },
  { label: "Evaluation", to: "/evaluation", icon: ShieldCheck },
];

const secondaryNav: NavItem[] = [
  { label: "System Logs", to: "/evaluation", icon: Terminal },
  { label: "Support", to: "/evaluation", icon: Lifebuoy },
];

function navClass(isActive: boolean) {
  return [
    "group flex items-center gap-3 border-r-[3px] px-4 py-3 text-xs font-bold uppercase tracking-[0.12em] transition duration-200",
    isActive
      ? "border-ink bg-white text-ink"
      : "border-transparent text-ink-muted hover:bg-white/70 hover:text-ink",
  ].join(" ");
}

function SidebarLink({ item }: { item: NavItem }) {
  const Icon = item.icon;
  return (
    <NavLink to={item.to} end={item.end} className={({ isActive }) => navClass(isActive)}>
      <Icon size={20} weight="duotone" />
      <span>{item.label}</span>
    </NavLink>
  );
}

function runtimeTone(runtime: RuntimeModeReport): string {
  if (runtime.live_mode_ready) {
    return "border-emerald-300 bg-emerald-50 text-emerald-900";
  }
  return "border-amber-300 bg-amber-50 text-amber-950";
}

function formatRuntimeLabel(runtime: RuntimeModeReport): string {
  return runtime.live_mode_ready ? "Live candidate" : "Mixed non-live";
}

function formatRuntimeDetail(runtime: RuntimeModeReport): string {
  return `${runtime.job_data_mode.replace("_", " ")} / ${runtime.evidence_mode} evidence / LLM ${runtime.llm_mode}`;
}

function formatBlockerLabel(blocker: string): string {
  return blocker.replace(/_/g, " ");
}

function RuntimeModeBanner() {
  const health = useApiResource((signal) => fetchHealthStatus(signal), []);

  if (health.status === "loading") {
    return (
      <section className="clinical-panel border-l-[3px] border-l-slate-500 px-4 py-3">
        <div className="flex flex-wrap items-center gap-3">
          <p className="label-caps text-ink-muted">Backend runtime</p>
          <p className="font-data text-xs uppercase tracking-[0.14em] text-ink-muted">Checking operating mode</p>
        </div>
      </section>
    );
  }

  if (health.status === "error") {
    return (
      <section className="clinical-panel border-l-[3px] border-l-act px-4 py-3">
        <div className="flex flex-wrap items-center gap-3">
          <p className="label-caps text-act">Backend runtime</p>
          <p className="font-data text-xs uppercase tracking-[0.14em] text-ink-muted">
            Mode unavailable
          </p>
          <p className="text-sm text-ink-muted">{health.error}</p>
        </div>
      </section>
    );
  }

  const runtime = health.data.runtime;
  return (
    <section className="clinical-panel border-l-[3px] border-l-slate-700 px-4 py-3">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <p className="label-caps text-ink-muted">Backend runtime</p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <span className={`rounded border px-2.5 py-1 font-data text-[0.7rem] font-bold uppercase tracking-[0.14em] ${runtimeTone(runtime)}`}>
              {formatRuntimeLabel(runtime)}
            </span>
            <span className="font-data text-[0.7rem] uppercase tracking-[0.14em] text-ink-muted">
              {formatRuntimeDetail(runtime)}
            </span>
            <span className="font-data text-[0.7rem] uppercase tracking-[0.14em] text-ink-muted">
              env {runtime.app_env}
            </span>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {runtime.live_mode_blockers.length === 0 ? (
            <span className="rounded border border-emerald-300 bg-emerald-50 px-2.5 py-1 font-data text-[0.7rem] font-bold uppercase tracking-[0.14em] text-emerald-900">
              No live blockers
            </span>
          ) : (
            runtime.live_mode_blockers.map((blocker) => (
              <span
                className="rounded border border-amber-300 bg-amber-50 px-2.5 py-1 font-data text-[0.7rem] font-bold uppercase tracking-[0.14em] text-amber-950"
                key={blocker}
              >
                {formatBlockerLabel(blocker)}
              </span>
            ))
          )}
        </div>
      </div>
    </section>
  );
}

export function AppShell() {
  const navigate = useNavigate();
  const [globalSearch, setGlobalSearch] = useState("");

  function handleGlobalSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const query = globalSearch.trim();
    if (!query) {
      navigate("/queue");
      return;
    }
    if (query.toLowerCase().startsWith("job_")) {
      navigate(`/cases/${encodeURIComponent(query)}`);
      return;
    }
    navigate(`/queue?q=${encodeURIComponent(query)}`);
  }

  return (
    <div className="min-h-[100dvh]">
      <header className="fixed inset-x-0 top-0 z-30 border-b border-line bg-white/92 backdrop-blur">
        <div className="flex h-14 items-center justify-between px-4 md:px-6">
          <NavLink to="/" className="flex items-center gap-2 font-display text-lg font-bold tracking-tight">
            <Biohazard size={24} weight="fill" />
            <span>SafeSurveil-AIxBio</span>
          </NavLink>
          <div className="hidden items-center gap-3 md:flex">
            <form className="relative block" onSubmit={handleGlobalSearch}>
              <label>
                <span className="sr-only">Search genomic job</span>
                <MagnifyingGlass
                  size={16}
                  className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-muted"
                />
                <input
                  className="w-72 rounded border border-line bg-surface-muted py-2 pl-9 pr-3 font-data text-xs tracking-wide text-ink outline-none transition focus:border-ink"
                  onChange={(event) => setGlobalSearch(event.target.value)}
                  placeholder="Query sample, job, or organism"
                  type="search"
                  value={globalSearch}
                />
              </label>
            </form>
            <Link
              aria-label="Open analyst queue"
              className="rounded p-2 text-ink-muted transition hover:bg-surface-muted hover:text-ink active:scale-[0.98]"
              to="/queue"
            >
              <Bell size={20} />
            </Link>
            <Link
              aria-label="Open system readiness"
              className="rounded p-2 text-ink-muted transition hover:bg-surface-muted hover:text-ink active:scale-[0.98]"
              to="/evaluation"
            >
              <GearSix size={20} />
            </Link>
            <Link
              aria-label="Open frontend acceptance checklist"
              className="rounded p-2 text-ink-muted transition hover:bg-surface-muted hover:text-ink active:scale-[0.98]"
              to="/evaluation"
            >
              <Question size={20} />
            </Link>
          </div>
        </div>
      </header>

      <aside className="fixed left-0 top-14 z-20 hidden h-[calc(100dvh-3.5rem)] w-64 flex-col border-r border-line bg-surface-muted/92 py-4 backdrop-blur md:flex">
        <div className="border-b border-line px-5 pb-5">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded bg-ink text-white">
              <span className="font-display text-sm font-bold">SS</span>
            </div>
            <div>
              <div className="font-display text-sm font-bold">Clinical Analyst</div>
              <div className="mt-1 font-data text-[0.68rem] uppercase tracking-[0.14em] text-ink-muted">
                ID: SS-04291
              </div>
            </div>
          </div>
          <Link
            className="mt-5 flex w-full items-center justify-center gap-2 rounded bg-ink px-4 py-2.5 text-xs font-bold uppercase tracking-[0.14em] text-white transition hover:bg-slate-800 active:scale-[0.98]"
            to="/analysis/new"
          >
            <Plus size={16} weight="bold" />
            New Analysis
          </Link>
        </div>

        <nav className="flex flex-1 flex-col gap-1 pt-4">
          {primaryNav.map((item) => (
            <SidebarLink item={item} key={item.label} />
          ))}
        </nav>

        <nav className="border-t border-line pt-3">
          {secondaryNav.map((item) => (
            <SidebarLink item={item} key={item.label} />
          ))}
        </nav>
      </aside>

      <main className="min-h-[100dvh] pt-14 md:pl-64">
        <div className="mx-auto flex w-full max-w-clinical flex-col gap-8 px-4 py-6 md:px-8 md:py-8">
          <RuntimeModeBanner />
          <Outlet />
        </div>
      </main>
    </div>
  );
}
