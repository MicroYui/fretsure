import { useEffect, useMemo, useRef, useState } from "react";
import { arrangeScore, FretsureAPIError, getCapabilities } from "./api";
import type {
  APIProblem,
  ArrangeControls,
  ArrangementResponse,
  CapabilitiesResponse,
  TraceStep,
  Verdict,
} from "./types";

const FALLBACK_CONTROLS: ArrangeControls = {
  engine: "offline",
  profile: "median",
  n: 1,
  maxIters: 0,
  useCritic: false,
  tempoBpm: null,
};

const EVENT_LABELS: Record<string, string> = {
  PIPELINE_CONFIGURED: "Plan",
  CANDIDATE_PROPOSED: "Propose",
  CANDIDATE_FINISHED: "Candidate",
  SOLVER_RETURNED_TAB: "Solve",
  SOLVER_RETURNED_NO_TAB: "No fingering",
  PLAYABILITY_CHECKED: "Oracle",
  REPAIR_EDIT_PROPOSED: "Reason",
  MODEL_CALL_FAILED: "Model stopped",
  EDIT_APPLIED: "Edit applied",
  EDIT_REJECTED: "Edit rejected",
  MODEL_EDIT_INVALID: "Invalid edit",
  RECHECK_STARTED: "Re-check",
  CANDIDATE_SELECTED: "Selected",
  NO_CANDIDATE_SELECTED: "No selection",
};

function Mark(): React.JSX.Element {
  return (
    <svg aria-hidden="true" className="mark" viewBox="0 0 42 42">
      <path d="M8 7.5h26M8 17h26M8 26.5h26M8 36h26" />
      <path d="M14 4v35M28 4v35" />
      <circle cx="14" cy="17" r="3.3" />
      <circle cx="28" cy="26.5" r="3.3" />
    </svg>
  );
}

function ArrowIcon(): React.JSX.Element {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20">
      <path d="M3 10h13M11.5 4.5 17 10l-5.5 5.5" />
    </svg>
  );
}

function UploadIcon(): React.JSX.Element {
  return (
    <svg aria-hidden="true" viewBox="0 0 28 28">
      <path d="M14 19V5m0 0L8.5 10.5M14 5l5.5 5.5" />
      <path d="M5 17.5V23h18v-5.5" />
    </svg>
  );
}

function displayBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function shortHash(hash: string): string {
  return hash.length > 16 ? `${hash.slice(0, 8)}…${hash.slice(-6)}` : hash;
}

function initialControls(capabilities: CapabilitiesResponse): ArrangeControls {
  const defaults = capabilities.controls.arrange.defaults;
  return {
    engine: defaults.engine ?? "offline",
    profile: defaults.profile,
    n: defaults.n,
    maxIters: defaults.max_iters,
    useCritic: defaults.use_critic,
    tempoBpm: defaults.tempo_bpm,
  };
}

function ProblemPanel({ problem, onDismiss }: { problem: APIProblem; onDismiss: () => void }) {
  return (
    <section className="problem" role="alert">
      <div className="problem-code">{problem.code}</div>
      <div>
        <h2>{problem.title}</h2>
        <p>{problem.detail}</p>
        {problem.diagnostics && problem.diagnostics.length > 0 ? (
          <ul>
            {problem.diagnostics.map((item, index) => (
              <li key={`${item.code}-${index}`}>
                <span>{item.code}</span>
                {item.message ? ` — ${item.message}` : ""}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      <button className="quiet-button" onClick={onDismiss} type="button">
        Dismiss
      </button>
    </section>
  );
}

function GateBadge({ verdict }: { verdict: Verdict }) {
  return <span className={`verdict verdict-${verdict.toLowerCase()}`}>{verdict}</span>;
}

function Metric({ label, value }: { label: string; value: number | null }) {
  if (value === null) {
    return (
      <div aria-label={`${label}: N/A`} className="metric">
        <div className="metric-head">
          <span>{label}</span>
          <strong>N/A</strong>
        </div>
      </div>
    );
  }
  const percent = Math.round(value * 100);
  return (
    <div className="metric">
      <div className="metric-head">
        <span>{label}</span>
        <strong>{percent}%</strong>
      </div>
      <progress
        aria-label={`${label}: ${percent}%`}
        className="metric-track"
        max="100"
        value={Math.max(0, Math.min(100, percent))}
      />
    </div>
  );
}

function EvidenceCard({ result }: { result: ArrangementResponse }) {
  const playability = result.playability;
  const faithfulness = result.faithfulness;
  return (
    <aside className="evidence-stack" aria-label="Verification evidence">
      <section className="evidence-card playability-card">
        <div className="card-kicker">
          <span>01</span>
          <span>Physical model</span>
        </div>
        <div className="gate-heading">
          <h3>Playability</h3>
          {playability ? <GateBadge verdict={playability.verdict} /> : <span className="na">N/A</span>}
        </div>
        <p className="gate-copy">
          {playability
            ? playability.verdict === "GREEN"
              ? "Certified inside the versioned hand model. This is not yet a real-player guarantee."
              : `${playability.diagnostics.length} localized constraint${playability.diagnostics.length === 1 ? "" : "s"} remain.`
            : "No tablature was returned, so there is no playability verdict."}
        </p>
        {playability ? (
          <>
            {playability.diagnostics.length > 0 ? (
              <ol className="diagnostic-list" aria-label="Playability diagnostics">
                {playability.diagnostics.map((diagnostic, index) => (
                  <li key={`${diagnostic.measure}-${diagnostic.beat}-${index}`}>
                    <strong>{diagnostic.violation_type.replaceAll("_", " ")}</strong>
                    <span>
                      Measure {diagnostic.measure} · beat {diagnostic.beat} · overage{" "}
                      {diagnostic.overage}
                    </span>
                    {diagnostic.suggested_relaxations.length > 0 ? (
                      <small>{diagnostic.suggested_relaxations.join(" · ")}</small>
                    ) : null}
                  </li>
                ))}
              </ol>
            ) : null}
            <div className="evidence-meta">
              <span>{playability.checker_version}</span>
              <span>{playability.profile_version}</span>
            </div>
          </>
        ) : null}
      </section>

      <section className="evidence-card fidelity-card">
        <div className="card-kicker">
          <span>02</span>
          <span>Source fidelity</span>
        </div>
        <div className="gate-heading">
          <h3>Musical fidelity</h3>
          {faithfulness ? (
            <span className={`pass-pill ${faithfulness.passed ? "is-pass" : "is-fail"}`}>
              {`${faithfulness.passed ? "PASS" : "REVIEW"} · ${faithfulness.evaluated_dimensions.length}/3 available`}
            </span>
          ) : (
            <span className="na">N/A</span>
          )}
        </div>
        {faithfulness ? (
          <div className="metrics">
            <Metric label="Melody" value={faithfulness.melody_f1} />
            <Metric label="Bass root" value={faithfulness.bass_root_accuracy} />
            <Metric label="Harmony" value={faithfulness.harmony_jaccard} />
          </div>
        ) : (
          <p className="gate-copy">Fidelity is only scored when a tablature candidate exists.</p>
        )}
      </section>
    </aside>
  );
}

function getCheckpoint(step: TraceStep): Record<string, unknown> | null {
  const candidate = step.data.tab_checkpoint ?? step.data.target_checkpoint;
  return typeof candidate === "object" && candidate !== null && !Array.isArray(candidate)
    ? (candidate as Record<string, unknown>)
    : null;
}

function TraceViewer({ trace }: { trace: ArrangementResponse["trace"] }) {
  const initial = trace.steps.findIndex((step) => step.event === "EDIT_APPLIED");
  const [selected, setSelected] = useState(initial >= 0 ? initial : Math.max(0, trace.steps.length - 1));
  const step = trace.steps[selected];
  const checkpoint = step ? getCheckpoint(step) : null;
  return (
    <section className="trace-panel" aria-labelledby="trace-title">
      <div className="section-heading trace-heading">
        <div>
          <p className="eyebrow">Replay, not chain-of-thought</p>
          <h2 id="trace-title">What changed, and why</h2>
        </div>
        <div className="schema-chip">{trace.schema_version}</div>
      </div>
      {trace.steps.length === 0 ? (
        <div className="empty-trace">No public trace steps were recorded.</div>
      ) : (
        <div className="trace-layout">
          <ol className="timeline" aria-label="Arrangement trace steps">
            {trace.steps.map((item, index) => (
              <li key={`${item.seq}-${item.event}`}>
                <button
                  aria-current={index === selected ? "step" : undefined}
                  className={index === selected ? "is-active" : ""}
                  onClick={() => setSelected(index)}
                  type="button"
                >
                  <span className={`timeline-dot dot-${item.kind.toLowerCase()}`} />
                  <span className="timeline-copy">
                    <strong>{EVENT_LABELS[item.event] ?? item.event}</strong>
                    <small>
                      {item.candidate_index === null ? "Pipeline" : `Candidate ${item.candidate_index}`}
                      {item.iteration === null ? "" : ` · pass ${item.iteration}`}
                    </small>
                  </span>
                  <span className="step-number">{String(item.seq + 1).padStart(2, "0")}</span>
                </button>
              </li>
            ))}
          </ol>
          <article className="trace-detail" aria-live="polite">
            {step ? (
              <>
                <div className="trace-detail-head">
                  <div>
                    <p>{step.kind}</p>
                    <h3>{EVENT_LABELS[step.event] ?? step.event}</h3>
                  </div>
                  <span>#{String(step.seq + 1).padStart(2, "0")}</span>
                </div>
                <p className="trace-explanation">{step.detail}</p>
                {checkpoint ? (
                  <div className="checkpoint">
                    <div>
                      <span>Checkpoint</span>
                      <strong>{String(checkpoint.type ?? "state")}</strong>
                    </div>
                    <div>
                      <span>Notes</span>
                      <strong>{String(checkpoint.note_count ?? "—")}</strong>
                    </div>
                    <div>
                      <span>Replay</span>
                      <strong>{checkpoint.complete === true ? "Complete" : "Digest only"}</strong>
                    </div>
                  </div>
                ) : null}
                <details className="raw-evidence">
                  <summary>Typed evidence</summary>
                  <pre>{JSON.stringify(step.data, null, 2)}</pre>
                </details>
              </>
            ) : null}
          </article>
        </div>
      )}
    </section>
  );
}

function Arrangement({
  result,
  onReset,
  headingRef,
}: {
  result: ArrangementResponse;
  onReset: () => void;
  headingRef: React.RefObject<HTMLHeadingElement | null>;
}) {
  const fidelityStatus = result.faithfulness
    ? `available fidelity ${result.faithfulness.passed ? "passed" : "needs review"} (${result.faithfulness.evaluated_dimensions.length}/3)`
    : "fidelity unavailable";
  const runLabel =
    result.status === "no_fingering_within_budget"
      ? "Arrangement evidence / bounded search ended"
      : result.playability?.verdict !== "GREEN"
        ? "Arrangement evidence / playability needs review"
        : `Arrangement evidence / playability passed · ${fidelityStatus}`;
  return (
    <main className="result-shell">
      <section className="result-intro">
        <div className="result-title-block">
          <p className="eyebrow">{runLabel}</p>
          <h1 ref={headingRef} tabIndex={-1}>{result.score.title || "Untitled score"}</h1>
          <div className="score-facts">
            <span>{result.score.key}</span>
            <span>
              {result.score.time_signature.numerator}/{result.score.time_signature.denominator}
            </span>
            <span>{result.options.effective_tempo_bpm} BPM</span>
            <span>{result.score.note_count} source notes</span>
          </div>
        </div>
        <button className="reset-button" onClick={onReset} type="button">
          <span>Arrange another</span>
          <ArrowIcon />
        </button>
      </section>

      <section className="proof-grid">
        <article className="tab-card">
          <div className="tab-card-head">
            <div>
              <p className="eyebrow">Selected output</p>
              <h2>Fingerstyle tablature</h2>
            </div>
            <span className="model-chip">{result.model.model_id}</span>
          </div>
          {result.ascii ? (
            <div className="tab-scroll" tabIndex={0}>
              <pre>{result.ascii}</pre>
            </div>
          ) : (
            <div className="no-tab">
              <strong>No fingering within the bounded search.</strong>
              <span>The result does not claim that no solution exists.</span>
            </div>
          )}
          <footer className="source-strip">
            <div>
              <span>Input</span>
              <strong>{result.source.filename}</strong>
            </div>
            <div>
              <span>SHA-256</span>
              <strong title={result.source.raw_sha256}>{shortHash(result.source.raw_sha256)}</strong>
            </div>
            <div>
              <span>Importer</span>
              <strong>{result.source.importer_version}</strong>
            </div>
          </footer>
          {result.source.warnings.length > 0 ? (
            <section className="source-warnings" aria-label="Source import warnings">
              <strong>
                {result.source.warnings.length} source warning
                {result.source.warnings.length === 1 ? "" : "s"}
              </strong>
              <ul>
                {result.source.warnings.map((warning, index) => (
                  <li key={`${warning.code}-${index}`}>
                    <span>{warning.code}</span> — {warning.message}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </article>
        <EvidenceCard result={result} />
      </section>

      <TraceViewer trace={result.trace} />

      <section className="provenance-band">
        <div>
          <p className="eyebrow">Bound evidence</p>
          <h2>Every verdict carries its version.</h2>
        </div>
        <dl>
          {Object.entries(result.stamps).map(([key, value]) => (
            <div key={key}>
              <dt>{key.replaceAll("_", " ")}</dt>
              <dd title={value}>{key.includes("fingerprint") ? shortHash(value) : value}</dd>
            </div>
          ))}
        </dl>
      </section>
    </main>
  );
}

export default function App(): React.JSX.Element {
  const [capabilities, setCapabilities] = useState<CapabilitiesResponse | null>(null);
  const [capabilityError, setCapabilityError] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [controls, setControls] = useState<ArrangeControls>(FALLBACK_CONTROLS);
  const [dragging, setDragging] = useState(false);
  const [problem, setProblem] = useState<APIProblem | null>(null);
  const [result, setResult] = useState<ArrangementResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [loadingExample, setLoadingExample] = useState(false);
  const [capabilityAttempt, setCapabilityAttempt] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const resultHeadingRef = useRef<HTMLHeadingElement>(null);
  const requestRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setCapabilityError(null);
    getCapabilities(controller.signal)
      .then((value) => {
        setCapabilities(value);
        setControls(initialControls(value));
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setCapabilityError(error instanceof Error ? error.message : "Capabilities are unavailable.");
      });
    return () => controller.abort();
  }, [capabilityAttempt]);

  useEffect(() => () => requestRef.current?.abort(), []);

  useEffect(() => {
    if (result) resultHeadingRef.current?.focus({ preventScroll: true });
  }, [result]);

  const supportedSuffixes = capabilities?.inputs.score_suffixes ?? [
    ".musicxml",
    ".xml",
    ".mxl",
    ".mid",
    ".midi",
  ];
  const proxyEngine = capabilities?.engines.find((engine) => engine.id === "proxy");
  const proxyAvailable = proxyEngine?.available === true;
  const activeEngine = capabilities?.engines.find((engine) => engine.id === controls.engine);
  const fileError = useMemo(() => {
    if (!file) return null;
    const lower = file.name.toLowerCase();
    return supportedSuffixes.some((suffix) => lower.endsWith(suffix))
      ? null
      : `Choose ${supportedSuffixes.join(", ")}.`;
  }, [file, supportedSuffixes]);

  function chooseFile(next: File | null): void {
    setProblem(null);
    setFile(next);
  }

  async function submit(event: React.FormEvent): Promise<void> {
    event.preventDefault();
    if (!file || fileError || !capabilities || running) return;
    setProblem(null);
    setRunning(true);
    const controller = new AbortController();
    requestRef.current = controller;
    try {
      setResult(await arrangeScore(file, controls, controller.signal));
      window.scrollTo({ top: 0 });
    } catch (error: unknown) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      if (error instanceof FretsureAPIError) {
        setProblem(error.problem);
      } else {
        setProblem({
          type: "about:blank",
          api_version: capabilities.api_version,
          status: 0,
          code: "CONNECTION_FAILED",
          title: "Could not reach the arranger",
          detail: error instanceof Error ? error.message : "The request did not complete.",
        });
      }
    } finally {
      requestRef.current = null;
      setRunning(false);
    }
  }

  async function loadExample(): Promise<void> {
    if (!capabilities || running || loadingExample) return;
    setProblem(null);
    setLoadingExample(true);
    const controller = new AbortController();
    requestRef.current = controller;
    try {
      const response = await fetch("/examples/fretsure-etude.musicxml", {
        headers: { accept: "application/vnd.recordare.musicxml+xml" },
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`Example score returned HTTP ${response.status}.`);
      const example = new File([await response.arrayBuffer()], "fretsure-etude.musicxml", {
        type: "application/vnd.recordare.musicxml+xml",
      });
      chooseFile(example);
    } catch (error: unknown) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setProblem({
        type: "about:blank",
        api_version: capabilities.api_version,
        status: 0,
        code: "EXAMPLE_UNAVAILABLE",
        title: "Could not load the example score",
        detail: error instanceof Error ? error.message : "The example request did not complete.",
      });
    } finally {
      requestRef.current = null;
      setLoadingExample(false);
    }
  }

  if (result) {
    return (
      <div className="app result-app">
        <Header
          capabilities={capabilities}
          capabilityError={capabilityError}
          modelLabel={`${result.model.engine} · ${result.model.model_id}`}
        />
        <Arrangement
          headingRef={resultHeadingRef}
          result={result}
          onReset={() => {
            setResult(null);
            setProblem(null);
          }}
        />
      </div>
    );
  }

  return (
    <div className="app landing-app">
      <Header
        capabilities={capabilities}
        capabilityError={capabilityError}
        modelLabel={activeEngine ? `${activeEngine.id} · ${activeEngine.model_id}` : null}
      />
      <main>
        <section className="hero">
          <div className="hero-copy">
            <p className="eyebrow reveal reveal-1">Oracle-guided guitar arrangement</p>
            <h1 className="reveal reveal-2">
              Don’t just generate it.
              <em>Make sure hands can reach it.</em>
            </h1>
            <p className="hero-deck reveal reveal-3">
              Fretsure turns supported symbolic scores—MusicXML, MXL, and MIDI—into fingerstyle
              tablature, then makes every candidate answer to a deterministic physical model.
            </p>
            <div className="hero-proof reveal reveal-4">
              <span className="proof-line" />
              <p>
                <strong>Proof, not a promise.</strong>
                GREEN is versioned model evidence—not yet a blanket claim about every player.
              </p>
            </div>
          </div>
          <div className="hero-instrument" aria-hidden="true">
            <div className="string string-1" />
            <div className="string string-2" />
            <div className="string string-3" />
            <div className="string string-4" />
            <div className="string string-5" />
            <div className="string string-6" />
            <div className="fret fret-1" />
            <div className="fret fret-2" />
            <div className="fret fret-3" />
            <div className="fret fret-4" />
            <div className="finger finger-a">1</div>
            <div className="finger finger-b">3</div>
            <div className="finger finger-c">2</div>
            <div className="instrument-label">model / median hand</div>
          </div>
        </section>

        <section className="workbench" id="arrange">
          <div className="workbench-heading">
            <div>
              <p className="eyebrow">Start with a real score</p>
              <h2>Bring the notes. We’ll test the geometry.</h2>
            </div>
            <div className="format-list" aria-label="Supported formats">
              {supportedSuffixes.map((suffix) => (
                <span key={suffix}>{suffix.replace(".", "")}</span>
              ))}
            </div>
          </div>

          {capabilityError ? (
            <section className="service-warning" role="alert">
              <span>Service offline</span>
              <p>{capabilityError}</p>
              <button
                className="quiet-button"
                onClick={() => setCapabilityAttempt((value) => value + 1)}
                type="button"
              >
                Retry connection
              </button>
            </section>
          ) : null}
          {problem ? <ProblemPanel problem={problem} onDismiss={() => setProblem(null)} /> : null}

          <form
            aria-busy={running || loadingExample}
            className="arrange-form"
            onSubmit={(event) => void submit(event)}
          >
            <div
              className={`drop-zone ${dragging ? "is-dragging" : ""} ${file ? "has-file" : ""}`}
              onDragEnter={(event) => {
                event.preventDefault();
                setDragging(true);
              }}
              onDragLeave={(event) => {
                event.preventDefault();
                if (event.currentTarget === event.target) setDragging(false);
              }}
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                chooseFile(event.dataTransfer.files.item(0));
              }}
            >
              <input
                accept={supportedSuffixes.join(",")}
                aria-hidden="true"
                aria-label="Choose a supported symbolic score"
                onChange={(event) => chooseFile(event.target.files?.item(0) ?? null)}
                ref={inputRef}
                tabIndex={-1}
                type="file"
              />
              <button
                aria-describedby={fileError ? "file-error" : undefined}
                className="drop-trigger"
                onClick={() => inputRef.current?.click()}
                type="button"
              >
                <span className="upload-icon">
                  <UploadIcon />
                </span>
                {file ? (
                  <span className="file-selection">
                    <strong>{file.name}</strong>
                    <small>{displayBytes(file.size)} · ready in memory</small>
                  </span>
                ) : (
                  <span>
                    <strong>Drop a symbolic score here</strong>
                    <small>or choose a MusicXML / MXL / MIDI file</small>
                  </span>
                )}
                <span className="browse-label">{file ? "Change" : "Browse"}</span>
              </button>
              {!file ? (
                <button
                  className="example-trigger"
                  disabled={!capabilities || running || loadingExample}
                  onClick={() => void loadExample()}
                  type="button"
                >
                  {loadingExample ? "Loading example…" : "Or load the CC0 example"}
                </button>
              ) : null}
              {fileError ? (
                <p className="field-error" id="file-error" role="alert">
                  {fileError}
                </p>
              ) : null}
            </div>

            <fieldset className="controls" disabled={!capabilities || running}>
              <legend>Arrangement controls</legend>
              <label>
                <span>Engine</span>
                <select
                  onChange={(event) =>
                    setControls((current) => ({
                      ...current,
                      engine: event.target.value as ArrangeControls["engine"],
                    }))
                  }
                  value={controls.engine}
                >
                  <option value="offline">Offline · deterministic</option>
                  <option disabled={!proxyAvailable} value="proxy">
                    {proxyEngine?.model_id ?? "Proxy model"}
                    {proxyAvailable ? "" : " · server disabled"}
                  </option>
                </select>
              </label>
              <label>
                <span>Candidate breadth</span>
                <div className="range-control">
                  <input
                    aria-label="Candidate breadth"
                    max={capabilities?.controls.arrange.n.max ?? 8}
                    min={capabilities?.controls.arrange.n.min ?? 1}
                    onChange={(event) =>
                      setControls((current) => ({ ...current, n: Number(event.target.value) }))
                    }
                    type="range"
                    value={controls.n}
                  />
                  <output>{controls.n}</output>
                </div>
              </label>
              <label>
                <span>Repair passes</span>
                <div className="range-control">
                  <input
                    aria-label="Repair passes"
                    max={capabilities?.controls.arrange.max_iters.max ?? 16}
                    min={capabilities?.controls.arrange.max_iters.min ?? 0}
                    onChange={(event) =>
                      setControls((current) => ({
                        ...current,
                        maxIters: Number(event.target.value),
                      }))
                    }
                    type="range"
                    value={controls.maxIters}
                  />
                  <output>{controls.maxIters}</output>
                </div>
              </label>
              <label>
                <span>Tempo override</span>
                <div className="tempo-control">
                  <input
                    inputMode="decimal"
                    max={capabilities?.controls.arrange.tempo_bpm.max ?? 1000}
                    min={capabilities?.controls.arrange.tempo_bpm.min ?? 1}
                    onChange={(event) =>
                      setControls((current) => ({
                        ...current,
                        tempoBpm: event.target.value === "" ? null : Number(event.target.value),
                      }))
                    }
                    placeholder="From score"
                    type="number"
                    value={controls.tempoBpm ?? ""}
                  />
                  <span>BPM</span>
                </div>
              </label>
              <label className="switch-row">
                <span>
                  Taste critic
                  <small>Observed, not a correctness gate</small>
                </span>
                <input
                  checked={controls.useCritic}
                  onChange={(event) =>
                    setControls((current) => ({ ...current, useCritic: event.target.checked }))
                  }
                  role="switch"
                  type="checkbox"
                />
              </label>
            </fieldset>

            <button
              className="primary-action"
              disabled={!file || Boolean(fileError) || !capabilities || running}
              type="submit"
            >
              <span>{running ? "Arranging & checking…" : "Arrange and verify"}</span>
              {running ? <span className="spinner" /> : <ArrowIcon />}
            </button>
          </form>
        </section>

        <section className="method-strip" aria-label="How Fretsure works">
          <article>
            <span>01</span>
            <h3>Propose</h3>
            <p>The policy sketches a musical target—not a verdict.</p>
          </article>
          <article>
            <span>02</span>
            <h3>Check</h3>
            <p>A deterministic oracle localizes physical conflicts.</p>
          </article>
          <article>
            <span>03</span>
            <h3>Repair</h3>
            <p>Typed edits change only the diagnosed target, then re-check.</p>
          </article>
          <article>
            <span>04</span>
            <h3>Bind evidence</h3>
            <p>Every result carries model, checker, profile and input hashes.</p>
          </article>
        </section>
      </main>
      <Footer />
    </div>
  );
}

function Header({
  capabilities,
  capabilityError,
  modelLabel,
}: {
  capabilities: CapabilitiesResponse | null;
  capabilityError: string | null;
  modelLabel: string | null;
}) {
  const status = capabilities ? "Oracle ready" : capabilityError ? "Service unavailable" : "Connecting";
  return (
    <header className="site-header">
      <a className="brand" href="/" aria-label="Fretsure home">
        <Mark />
        <span>Fret<em>sure</em></span>
      </a>
      <div className="header-status">
        <span
          aria-hidden="true"
          className={`status-light ${capabilities ? "is-online" : capabilityError ? "is-error" : ""}`}
        />
        <span>{status}</span>
        <span className="header-divider" />
        <span>{modelLabel ?? (capabilityError ? "capabilities unavailable" : "model-relative evidence")}</span>
      </div>
    </header>
  );
}

function Footer() {
  return (
    <footer className="site-footer">
      <div className="brand footer-brand">
        <Mark />
        <span>Fret<em>sure</em></span>
      </div>
      <p>Versioned evidence for guitar arrangement.</p>
      <p>Plan 6A · replay-first interface</p>
    </footer>
  );
}
