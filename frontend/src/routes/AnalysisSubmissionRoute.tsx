import {
  ArrowRight,
  Database,
  FileArrowUp,
  Flask,
  LinkSimple,
  ShieldCheck,
} from "@phosphor-icons/react";
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { ApiError, submitAnalyzeJob } from "../api/client";
import type { AnalyzeJobRequest } from "../api/types";
import { RouteHeader } from "../components/RouteHeader";

type SubmissionFormState = {
  sample_id: string;
  organism_hint: string;
  target_drug: string;
  fasta_path: string;
  accession: string;
  collection_date: string;
  source: string;
  country: string;
  provenance_source: string;
};

type FormErrors = Partial<Record<keyof SubmissionFormState, string>>;

const sourceOptions = [
  { value: "agricultural_surveillance_proxy", label: "Agricultural surveillance proxy" },
  { value: "bovine_mastitis", label: "Bovine mastitis" },
  { value: "local_upload", label: "Local upload" },
  { value: "other", label: "Other" },
];

const provenanceOptions = [
  { value: "other", label: "Other" },
  { value: "ncbi_datasets", label: "NCBI Datasets" },
  { value: "bv_brc", label: "BV-BRC" },
  { value: "local_upload", label: "Local upload" },
];

const organismOptions = [
  { value: "", label: "No hint" },
  { value: "e_coli", label: "E. coli" },
  { value: "s_aureus", label: "S. aureus" },
];

const initialFormState: SubmissionFormState = {
  sample_id: "",
  organism_hint: "e_coli",
  target_drug: "tetracycline",
  fasta_path: "",
  accession: "",
  collection_date: "",
  source: "agricultural_surveillance_proxy",
  country: "",
  provenance_source: "other",
};

function validateForm(form: SubmissionFormState): FormErrors {
  const errors: FormErrors = {};
  if (form.sample_id.trim().length < 3) {
    errors.sample_id = "Provide a stable sample ID with at least 3 characters.";
  }
  if (form.target_drug.trim().length < 3) {
    errors.target_drug = "Provide the target drug exactly as the backend expects it.";
  }
  if (!form.fasta_path.trim()) {
    errors.fasta_path = "Provide a repo-relative or local FASTA path.";
  }
  return errors;
}

function InputField(props: {
  label: string;
  helper?: string;
  error?: string;
  children: React.ReactNode;
}) {
  const { label, helper, error, children } = props;
  return (
    <label className="grid gap-2">
      <span className="label-caps text-ink">{label}</span>
      {children}
      {error ? <span className="text-xs font-semibold text-act">{error}</span> : null}
      {!error && helper ? <span className="text-xs text-ink-muted">{helper}</span> : null}
    </label>
  );
}

function inputClass(hasError: boolean): string {
  return [
    "w-full rounded border bg-white px-3 py-3 text-sm text-ink outline-none transition",
    hasError ? "border-act focus:border-act" : "border-line focus:border-ink",
  ].join(" ");
}

function buildPayload(form: SubmissionFormState): AnalyzeJobRequest {
  return {
    sample_id: form.sample_id.trim(),
    organism_hint: form.organism_hint.trim() || undefined,
    target_drug: form.target_drug.trim(),
    fasta_path: form.fasta_path.trim(),
    metadata: {
      accession: form.accession.trim() || undefined,
      collection_date: form.collection_date || undefined,
      source: form.source,
      country: form.country.trim() || undefined,
      provenance_source: form.provenance_source,
    },
  };
}

export function AnalysisSubmissionRoute() {
  const navigate = useNavigate();
  const [form, setForm] = useState<SubmissionFormState>(initialFormState);
  const [errors, setErrors] = useState<FormErrors>({});
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const payloadPreview = useMemo(() => buildPayload(form), [form]);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextErrors = validateForm(form);
    setErrors(nextErrors);
    setSubmitError(null);
    if (Object.keys(nextErrors).length > 0) {
      return;
    }

    setSubmitting(true);
    try {
      const response = await submitAnalyzeJob(payloadPreview);
      navigate(`/cases/${response.job_id}`);
    } catch (error) {
      if (error instanceof ApiError) {
        setSubmitError(error.detail);
      } else if (error instanceof Error) {
        setSubmitError(error.message);
      } else {
        setSubmitError("Unable to submit the analysis request.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <RouteHeader
        eyebrow="Phase 8 / live analysis intake"
        title="Create a real backend job from the frontend shell"
        description="This intake route speaks the existing AnalyzeJobRequest contract directly, then redirects into the persisted case detail view once the backend creates the job."
      />

      <section className="grid gap-gutter xl:grid-cols-[minmax(0,0.7fr)_minmax(19rem,0.3fr)]">
        <form className="clinical-panel overflow-hidden" onSubmit={handleSubmit}>
          <div className="border-b border-line bg-surface-muted px-5 py-4">
            <p className="label-caps text-ink-muted">Submission contract</p>
            <h2 className="mt-3 font-display text-2xl font-semibold tracking-tight">
              Primary frontend entry path for <span className="text-ink">POST /jobs/analyze</span>
            </h2>
            <p className="mt-3 max-w-[62ch] text-sm leading-6 text-ink-muted">
              Use a local FASTA path that the backend process can read. The backend remains the source of truth for
              validation, artifacts, queue state, and case detail rendering.
            </p>
          </div>

          <div className="grid gap-8 p-5">
            <div className="grid gap-5 border-b border-line pb-8 md:grid-cols-2">
              <InputField
                label="Sample ID"
                helper="Stable slug-like ID shown in queue and case detail."
                error={errors.sample_id}
              >
                <input
                  className={inputClass(Boolean(errors.sample_id))}
                  name="sample_id"
                  onChange={(event) => setForm((current) => ({ ...current, sample_id: event.target.value }))}
                  placeholder="live_smoke_ecoli_tet_001"
                  type="text"
                  value={form.sample_id}
                />
              </InputField>

              <InputField
                label="Target drug"
                helper="The current smoke path is locked around tetracycline."
                error={errors.target_drug}
              >
                <input
                  className={inputClass(Boolean(errors.target_drug))}
                  name="target_drug"
                  onChange={(event) => setForm((current) => ({ ...current, target_drug: event.target.value }))}
                  placeholder="tetracycline"
                  type="text"
                  value={form.target_drug}
                />
              </InputField>

              <InputField label="Organism hint" helper="Optional, but useful for the first live smoke pass.">
                <select
                  className={inputClass(false)}
                  name="organism_hint"
                  onChange={(event) => setForm((current) => ({ ...current, organism_hint: event.target.value }))}
                  value={form.organism_hint}
                >
                  {organismOptions.map((option) => (
                    <option key={option.value || "none"} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </InputField>

              <InputField label="Collection date" helper="Optional metadata passed through unchanged.">
                <input
                  className={inputClass(false)}
                  name="collection_date"
                  onChange={(event) => setForm((current) => ({ ...current, collection_date: event.target.value }))}
                  type="date"
                  value={form.collection_date}
                />
              </InputField>
            </div>

            <div className="grid gap-5 border-b border-line pb-8">
              <div>
                <p className="label-caps text-ink">FASTA locator</p>
                <p className="mt-2 text-sm leading-6 text-ink-muted">
                  Provide the local or repo-relative FASTA path accepted by the current backend analysis endpoint.
                </p>
              </div>

              <InputField
                label="FASTA path"
                helper="Use a repo-relative or local path the backend process can actually read."
                error={errors.fasta_path}
              >
                <input
                  className={inputClass(Boolean(errors.fasta_path))}
                  name="fasta_path"
                  onChange={(event) => setForm((current) => ({ ...current, fasta_path: event.target.value }))}
                  placeholder="artifacts/runs/phase6b_acceptance/latest/live_data/downloads/fasta/GCA_001283625.2/GCA_001283625.2_8205_3_53_genomic.fna"
                  type="text"
                  value={form.fasta_path}
                />
              </InputField>
            </div>

            <div className="grid gap-5 md:grid-cols-2">
              <InputField label="Accession" helper="Optional accession used for provenance and downstream traceability.">
                <input
                  className={inputClass(false)}
                  name="accession"
                  onChange={(event) => setForm((current) => ({ ...current, accession: event.target.value }))}
                  placeholder="GCF_000000000.1"
                  type="text"
                  value={form.accession}
                />
              </InputField>

              <InputField label="Country" helper="Optional country metadata.">
                <input
                  className={inputClass(false)}
                  name="country"
                  onChange={(event) => setForm((current) => ({ ...current, country: event.target.value }))}
                  placeholder="India"
                  type="text"
                  value={form.country}
                />
              </InputField>

              <InputField label="Source context" helper="Matches the backend enum values.">
                <select
                  className={inputClass(false)}
                  name="source"
                  onChange={(event) => setForm((current) => ({ ...current, source: event.target.value }))}
                  value={form.source}
                >
                  {sourceOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </InputField>

              <InputField label="Provenance source" helper="Used for auditability, not front-end decoration.">
                <select
                  className={inputClass(false)}
                  name="provenance_source"
                  onChange={(event) =>
                    setForm((current) => ({ ...current, provenance_source: event.target.value }))
                  }
                  value={form.provenance_source}
                >
                  {provenanceOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </InputField>
            </div>

            {submitError ? (
              <div className="rounded border border-act/30 bg-red-50 px-4 py-3">
                <p className="label-caps text-act">Submission rejected</p>
                <p className="mt-2 text-sm leading-6 text-ink-muted">{submitError}</p>
              </div>
            ) : null}

            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-6">
              <p className="max-w-[40rem] text-sm leading-6 text-ink-muted">
                Successful submission redirects directly into the persisted case screen so queue, evidence, copilot, and
                semantic UI are all reading the same backend job.
              </p>
              <div className="flex flex-wrap items-center gap-3">
                <Link
                  className="rounded border border-line px-4 py-2.5 text-xs font-bold uppercase tracking-[0.14em] text-ink transition hover:border-ink active:scale-[0.98]"
                  to="/queue"
                >
                  Back to queue
                </Link>
                <button
                  className="inline-flex items-center gap-2 rounded bg-ink px-4 py-2.5 text-xs font-bold uppercase tracking-[0.14em] text-white transition hover:bg-slate-800 active:scale-[0.98] disabled:cursor-not-allowed disabled:bg-slate-400"
                  disabled={submitting}
                  type="submit"
                >
                  {submitting ? "Submitting" : "Create analysis"}
                  <ArrowRight size={16} weight="bold" />
                </button>
              </div>
            </div>
          </div>
        </form>

        <aside className="grid gap-gutter self-start">
          <section className="clinical-panel p-5">
            <p className="label-caps text-ink-muted">Contract rules</p>
            <div className="mt-4 grid gap-4">
              {[
                {
                  icon: FileArrowUp,
                  title: "Local FASTA locator",
                  text: "The current backend analysis endpoint accepts local fasta_path inputs and rejects remote URI submissions.",
                },
                {
                  icon: Flask,
                  title: "First smoke stays narrow",
                  text: "The safest first live pass remains E. coli plus tetracycline unless the backend support matrix expands.",
                },
                {
                  icon: Database,
                  title: "Persistence is the truth source",
                  text: "Queue rows, artifacts, decision state, and case detail all come from the created job, not local form state.",
                },
                {
                  icon: ShieldCheck,
                  title: "Backend errors stay visible",
                  text: "Validation and environment failures are shown inline instead of being translated into friendly but misleading UI copy.",
                },
              ].map((item) => {
                const Icon = item.icon;
                return (
                  <article className="grid gap-2" key={item.title}>
                    <div className="flex items-center gap-3">
                      <Icon size={18} className="text-ink" weight="duotone" />
                      <h2 className="font-display text-base font-semibold tracking-tight">{item.title}</h2>
                    </div>
                    <p className="text-sm leading-6 text-ink-muted">{item.text}</p>
                  </article>
                );
              })}
            </div>
          </section>

          <section className="clinical-panel p-5">
            <p className="label-caps text-ink-muted">Payload preview</p>
            <div className="mt-4 rounded border border-line bg-surface-muted p-4">
              <div className="flex items-center gap-2 text-xs uppercase tracking-[0.14em] text-ink-muted">
                <LinkSimple size={14} />
                Backend request shape
              </div>
              <pre className="mt-3 overflow-x-auto whitespace-pre-wrap break-words font-data text-[0.72rem] leading-6 text-ink">
                {JSON.stringify(payloadPreview, null, 2)}
              </pre>
            </div>
            <p className="mt-3 text-xs leading-5 text-ink-muted">
              Submitted enum values stay exact, including tokens such as
              <span className="font-data text-ink"> {form.source}</span> and
              <span className="font-data text-ink"> {form.provenance_source}</span>.
            </p>
          </section>
        </aside>
      </section>
    </>
  );
}
