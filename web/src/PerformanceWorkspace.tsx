import type { AlphaTabApi } from "@coderline/alphatab";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  canonicalTabJSON,
  checkDifficulty,
  editLeftFinger,
  exportAudio,
  exportGuitarPro,
  exportGuitarPro7,
  exportMidi,
  exportMusicXMLTab,
  exportPdfTab,
  exportTabText,
  regenerateSection,
  type DownloadAsset,
} from "./api";
import {
  appendFeedback,
  clearFeedback,
  feedbackDownload,
  readFeedback,
  type FeedbackDocument,
} from "./feedback";
import {
  readLocalLibrary,
  removeLocalResult,
  saveLocalResult,
  type LocalLibraryEntry,
} from "./library";
import {
  activeNotesAtBeat,
  beatToMeasure,
  fractionBeats,
  millisecondsToBeat,
  positionTab,
  type PositionedTabNote,
} from "./performance";
import {
  MAX_SECTION_MEASURES,
  measureAtPoint,
  measureRangeFromDrag,
  normalizeMeasureRange,
  type MeasureBounds,
  type MeasureRange,
} from "./measureSelection";
import {
  computeLiveRunScorecard,
  fairAlternativePair,
  selectedCandidateIndex,
} from "./scorecard";
import { incrementalTrials, summarizeTrace } from "./traceEvidence";
import type {
  ArrangementResponse,
  CanonicalTab,
  CapabilitiesResponse,
  DifficultyCheckResponse,
  DifficultyTierName,
  FingeringEditResponse,
  SectionRegenerationResponse,
  VoiceRole,
  VerifiedAlternative,
} from "./types";
import type { IncrementalTrialEvidence } from "./traceEvidence";
import "./performance-workspace.css";

type WorkspaceView = "workspace" | "lab" | "library";
type ExportKind =
  | "musicxml"
  | "gp7"
  | "gp5"
  | "pdf"
  | "midi"
  | "audio"
  | "tab-text"
  | "json";

const TIER_LABELS: Record<DifficultyTierName, string> = {
  beginner: "Beginner",
  intermediate: "Intermediate",
  advanced: "Advanced",
};

function Mark(): React.JSX.Element {
  return (
    <svg aria-hidden="true" className="p6-mark" viewBox="0 0 42 42">
      <path d="M8 7.5h26M8 17h26M8 26.5h26M8 36h26" />
      <path d="M14 4v35M28 4v35" />
      <circle cx="14" cy="17" r="3.3" />
      <circle cx="28" cy="26.5" r="3.3" />
    </svg>
  );
}

function PlayIcon({ playing }: { playing: boolean }): React.JSX.Element {
  return playing ? (
    <svg aria-hidden="true" viewBox="0 0 24 24">
      <path d="M8 6v12M16 6v12" />
    </svg>
  ) : (
    <svg aria-hidden="true" viewBox="0 0 24 24">
      <path d="m9 6 9 6-9 6Z" />
    </svg>
  );
}

function formatTime(milliseconds: number): string {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function scoreBeatsPerMeasure(result: ArrangementResponse): number {
  const signature = result.score.time_signature;
  return Math.max(1, Math.round((signature.numerator * 4) / signature.denominator));
}

function saveDownload(asset: DownloadAsset): void {
  const url = URL.createObjectURL(asset.blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = asset.filename;
  anchor.hidden = true;
  document.body.append(anchor);
  try {
    anchor.click();
  } finally {
    anchor.remove();
    URL.revokeObjectURL(url);
  }
}

function ExportMenu({ result }: { result: ArrangementResponse }): React.JSX.Element | null {
  const [exporting, setExporting] = useState<ExportKind | null>(null);
  const [error, setError] = useState<string | null>(null);
  const selectedTab = result.tab;
  if (!selectedTab) return null;
  const tab: CanonicalTab = selectedTab;

  async function run(kind: ExportKind): Promise<void> {
    setError(null);
    setExporting(kind);
    try {
      let asset: DownloadAsset;
      switch (kind) {
        case "musicxml":
          asset = await exportMusicXMLTab(tab, result.options.effective_tempo_bpm);
          break;
        case "gp7":
          asset = await exportGuitarPro7(tab, result.options.effective_tempo_bpm);
          break;
        case "gp5":
          asset = await exportGuitarPro(tab, result.options.effective_tempo_bpm);
          break;
        case "pdf":
          asset = await exportPdfTab(tab, result.options.effective_tempo_bpm);
          break;
        case "midi":
          asset = await exportMidi(tab, result.options.effective_tempo_bpm);
          break;
        case "audio":
          asset = await exportAudio(tab, result.options.effective_tempo_bpm);
          break;
        case "tab-text":
          asset = await exportTabText(tab);
          break;
        case "json":
          asset = {
            blob: new Blob([canonicalTabJSON(tab)], { type: "application/json" }),
            filename: "fretsure-arrangement.tab.json",
          };
          break;
      }
      saveDownload(asset);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The export did not complete.");
    } finally {
      setExporting(null);
    }
  }

  return (
    <div className="p6-export-wrap">
      <details className="p6-export-menu">
        <summary>Export <span>⌄</span></summary>
        <div>
          {(
            [
              ["musicxml", "MusicXML"],
              ["gp7", "Guitar Pro 7+"],
              ["gp5", "Guitar Pro 5"],
              ["pdf", "Printable PDF"],
              ["midi", "MIDI"],
              ["audio", "WAV preview"],
              ["tab-text", "ASCII TAB"],
              ["json", "Tab JSON"],
            ] as const
          ).map(([kind, label]) => (
            <button
              aria-label={`Download ${label}`}
              disabled={exporting !== null}
              key={kind}
              onClick={() => void run(kind)}
              type="button"
            >
              {exporting === kind ? "Preparing…" : label}
            </button>
          ))}
        </div>
      </details>
      {error ? <p className="p6-export-error" role="alert">{error}</p> : null}
    </div>
  );
}

function AlphaTabScore({
  result,
  apiRef,
  measureCount,
  measureSelection,
  measureSelectionEnabled,
  onMeasureSelectionChange,
  onPlaying,
  onPosition,
  onReady,
  scale,
}: {
  result: ArrangementResponse;
  apiRef: React.RefObject<AlphaTabApi | null>;
  measureCount: number;
  measureSelection: MeasureRange;
  measureSelectionEnabled: boolean;
  onMeasureSelectionChange: (selection: MeasureRange) => void;
  onPlaying: (playing: boolean) => void;
  onPosition: (current: number, end: number) => void;
  onReady: (ready: boolean) => void;
  scale: number;
}): React.JSX.Element {
  const hostRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ anchor: number; pointerId: number } | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [measureBounds, setMeasureBounds] = useState<MeasureBounds[]>([]);
  const [viewport, setViewport] = useState({
    height: 0,
    scrollLeft: 0,
    scrollTop: 0,
    width: 0,
  });

  useEffect(() => {
    if (!result.tab || !hostRef.current) {
      setStatus("error");
      return;
    }
    const controller = new AbortController();
    let disposed = false;
    let api: AlphaTabApi | null = null;
    const host = hostRef.current;
    setStatus("loading");
    setMeasureBounds([]);
    onReady(false);

    const syncMeasureBounds = (): void => {
      if (disposed || !host) return;
      setViewport({
        height: host.clientHeight,
        scrollLeft: host.scrollLeft,
        scrollTop: host.scrollTop,
        width: host.clientWidth,
      });
      const lookup = api?.renderer.boundsLookup;
      if (!lookup) return;
      const next = new Map<number, MeasureBounds>();
      for (const system of lookup.staffSystems) {
        for (const bar of system.bars) {
          if (bar.index < 0 || bar.index >= measureCount) continue;
          const bounds = bar.realBounds;
          if (bounds.w <= 0 || bounds.h <= 0) continue;
          next.set(bar.index, {
            measure: bar.index + 1,
            x: bounds.x,
            y: bounds.y,
            width: bounds.w,
            height: bounds.h,
          });
        }
      }
      setMeasureBounds([...next.values()].sort((left, right) => left.measure - right.measure));
    };

    host.addEventListener("scroll", syncMeasureBounds, { passive: true });
    const resizeObserver = new ResizeObserver(syncMeasureBounds);
    resizeObserver.observe(host);
    syncMeasureBounds();

    void Promise.all([
      exportMusicXMLTab(result.tab, result.options.effective_tempo_bpm, controller.signal),
      import("@coderline/alphatab"),
    ])
      .then(async ([asset, alphaTab]) => {
        if (disposed || !hostRef.current) return;
        const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
        api = new alphaTab.AlphaTabApi(hostRef.current, {
          core: {
            engine: "svg",
            fontDirectory: "/font/",
            enableLazyLoading: false,
          },
          display: {
            layoutMode: alphaTab.LayoutMode.Page,
            staveProfile: alphaTab.StaveProfile.ScoreTab,
            scale,
            stretchForce: 0.75,
            barsPerRow: 3,
          },
          player: {
            enablePlayer: true,
            soundFont: "/soundfont/sonivox.sf3",
            enableCursor: true,
            enableAnimatedBeatCursor: !reducedMotion,
            enableElementHighlighting: true,
            enableUserInteraction: true,
            scrollElement: hostRef.current,
            nativeBrowserSmoothScroll: false,
          },
        });
        apiRef.current = api;
        api.error.on(() => {
          if (!disposed) {
            setStatus("error");
            onReady(false);
          }
        });
        api.renderFinished.on(() => {
          if (!disposed) {
            setStatus("ready");
            syncMeasureBounds();
          }
        });
        api.postRenderFinished.on(syncMeasureBounds);
        api.playerReady.on(() => {
          if (!disposed) onReady(true);
        });
        api.playerStateChanged.on((event) => {
          if (!disposed) onPlaying(event.state === 1);
        });
        api.playerPositionChanged.on((event) => {
          if (!disposed) onPosition(event.currentTime, event.endTime);
        });
        api.midiLoaded.on((event) => {
          if (!disposed) onPosition(event.currentTime, event.endTime);
        });
        const bytes = new Uint8Array(await asset.blob.arrayBuffer());
        if (!api.load(bytes)) {
          setStatus("error");
          onReady(false);
        }
      })
      .catch((caught: unknown) => {
        if (
          !disposed &&
          !(caught instanceof DOMException && caught.name === "AbortError")
        ) {
          setStatus("error");
          onReady(false);
        }
      });

    return () => {
      disposed = true;
      controller.abort();
      dragRef.current = null;
      host.removeEventListener("scroll", syncMeasureBounds);
      resizeObserver.disconnect();
      onPlaying(false);
      onReady(false);
      apiRef.current = null;
      api?.destroy();
    };
  }, [apiRef, measureCount, onPlaying, onPosition, onReady, result]);

  function beginMeasureDrag(
    event: React.PointerEvent<SVGRectElement>,
    measure: number,
  ): void {
    if (!measureSelectionEnabled || !event.isPrimary || event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = { anchor: measure, pointerId: event.pointerId };
    onMeasureSelectionChange(
      measureRangeFromDrag(measure, measure, measureCount),
    );
  }

  function continueMeasureDrag(event: React.PointerEvent<SVGSVGElement>): void {
    const drag = dragRef.current;
    const host = hostRef.current;
    if (!drag || drag.pointerId !== event.pointerId || !host) return;
    const hostRect = host.getBoundingClientRect();
    if (
      event.clientX < hostRect.left ||
      event.clientX > hostRect.right ||
      event.clientY < hostRect.top ||
      event.clientY > hostRect.bottom
    ) {
      return;
    }
    event.preventDefault();
    const focus = measureAtPoint(
      measureBounds,
      event.clientX - hostRect.left + host.scrollLeft,
      event.clientY - hostRect.top + host.scrollTop,
    );
    if (focus === null) return;
    onMeasureSelectionChange(
      measureRangeFromDrag(drag.anchor, focus, measureCount),
    );
  }

  function finishMeasureDrag(event: React.PointerEvent<SVGSVGElement>): void {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    dragRef.current = null;
  }

  return (
    <div className="p6-alphatab-shell">
      {status === "loading" ? <div className="p6-notation-status">Preparing notation and player…</div> : null}
      {status === "error" ? (
        <div className="p6-notation-status is-error">
          <strong>Notation preview unavailable.</strong>
          <span>The verified Tab and exports remain available.</span>
        </div>
      ) : null}
      <div
        aria-label="Interactive notation and tablature"
        className="p6-alphatab-host"
        ref={hostRef}
      />
      {status === "ready" && measureSelectionEnabled && measureBounds.length > 0 ? (
        <svg
          aria-hidden="true"
          className="p7-measure-selection-layer"
          onPointerCancel={finishMeasureDrag}
          onPointerMove={continueMeasureDrag}
          onPointerUp={finishMeasureDrag}
          viewBox={`0 0 ${Math.max(1, viewport.width)} ${Math.max(1, viewport.height)}`}
        >
          {measureBounds.map((bounds) => {
            const selected =
              bounds.measure >= measureSelection.start &&
              bounds.measure <= measureSelection.end;
            return (
              <g className={selected ? "is-selected" : undefined} key={bounds.measure}>
                <rect
                  className="p7-measure-target"
                  data-measure={bounds.measure}
                  height={bounds.height}
                  onPointerDown={(event) => beginMeasureDrag(event, bounds.measure)}
                  width={bounds.width}
                  x={bounds.x - viewport.scrollLeft}
                  y={bounds.y - viewport.scrollTop}
                />
              </g>
            );
          })}
        </svg>
      ) : null}
    </div>
  );
}

type TrialAddition = { onset: string; pitch: number };

function trialAdditions(trial: IncrementalTrialEvidence | null): TrialAddition[] {
  const additions = trial?.step.data.additions;
  if (!Array.isArray(additions)) return [];
  return additions.flatMap((addition) =>
    typeof addition === "object" &&
    addition !== null &&
    typeof (addition as Record<string, unknown>).onset === "string" &&
    typeof (addition as Record<string, unknown>).pitch === "number"
      ? [
          {
            onset: (addition as Record<string, unknown>).onset as string,
            pitch: (addition as Record<string, unknown>).pitch as number,
          },
        ]
      : [],
  );
}

function Fretboard({
  tab,
  activeNotes,
  trial,
}: {
  tab: CanonicalTab | null;
  activeNotes: PositionedTabNote[];
  trial: IncrementalTrialEvidence | null;
}): React.JSX.Element {
  const additions = trialAdditions(trial);
  const maxActiveFret = activeNotes.reduce((value, note) => Math.max(value, note.fret), 0);
  const visibleFrets = Math.max(9, Math.min(19, maxActiveFret + 2));
  const left = 26;
  const right = 504;
  const width = right - left;
  const fretX = (fret: number) => left + (width * fret) / visibleFrets;
  const noteX = (fret: number) =>
    fret === 0 ? left - 12 : (fretX(fret - 1) + fretX(fret)) / 2;
  const noteY = (string: number) => 164 - string * 24;

  function isAddition(note: PositionedTabNote): boolean {
    if (!tab) return false;
    const pitch = tab.tuning[note.string] + tab.capo + note.fret;
    return additions.some((addition) => addition.onset === note.onset && addition.pitch === pitch);
  }

  return (
    <svg
      aria-label={trial ? "Trial fingering at the playback position" : "Verified fingering at the playback position"}
      className="p6-fretboard-svg"
      role="img"
      viewBox="0 0 530 215"
    >
      <defs>
        <linearGradient id="fretsure-fretboard-wood" x1="0" x2="1">
          <stop offset="0" stopColor="#181814" />
          <stop offset=".5" stopColor="#24231d" />
          <stop offset="1" stopColor="#151611" />
        </linearGradient>
      </defs>
      <rect fill="url(#fretsure-fretboard-wood)" height="160" rx="3" width={width} x={left} y="21" />
      {[44, 68, 92, 116, 140, 164].map((y, index) => (
        <line
          className="p6-string-line"
          key={`string-${y}`}
          strokeWidth={0.7 + index * 0.22}
          x1={left}
          x2={right}
          y1={y}
          y2={y}
        />
      ))}
      {Array.from({ length: visibleFrets + 1 }, (_, fret) => (
        <line
          className={fret === 0 ? "p6-nut-line" : "p6-fret-line"}
          key={`fret-${fret}`}
          x1={fretX(fret)}
          x2={fretX(fret)}
          y1="21"
          y2="181"
        />
      ))}
      {Array.from({ length: visibleFrets }, (_, index) => index + 1)
        .filter((fret) => fret === 1 || fret % 2 === 1)
        .map((fret) => (
          <text className="p6-fret-number" key={`label-${fret}`} textAnchor="middle" x={noteX(fret)} y="205">{fret}</text>
        ))}
      {activeNotes.map((note, index) => {
        const rejectedAddition = trial?.accepted === false && isAddition(note);
        const label = note.fret === 0 ? "○" : String(note.left_finger || "•");
        return (
          <g
            className={rejectedAddition ? "p6-finger-rejected" : "p6-finger-kept"}
            key={`${note.onset}-${note.string}-${note.fret}-${index}`}
          >
            <circle cx={noteX(note.fret)} cy={noteY(note.string)} r="13" />
            <text x={noteX(note.fret)} y={noteY(note.string) + 4}>{label}</text>
            {rejectedAddition ? (
              <path
                d={`M${noteX(note.fret) - 12} ${noteY(note.string) - 12} L${noteX(note.fret) + 12} ${noteY(note.string) + 12} M${noteX(note.fret) + 12} ${noteY(note.string) - 12} L${noteX(note.fret) - 12} ${noteY(note.string) + 12}`}
              />
            ) : null}
          </g>
        );
      })}
      {activeNotes.length === 0 ? (
        <text className="p6-fret-empty" textAnchor="middle" x="265" y="106">No note attack at this position</text>
      ) : null}
    </svg>
  );
}

function EvidenceCard({
  result,
  selectedTrial,
  difficulty,
}: {
  result: ArrangementResponse;
  selectedTrial: IncrementalTrialEvidence | null;
  difficulty: DifficultyCheckResponse | null;
}): React.JSX.Element {
  const finalVerdict = result.playability?.verdict ?? null;
  const rejected = selectedTrial !== null && !selectedTrial.accepted;
  const verdict = selectedTrial?.verdict ?? finalVerdict;
  const green = !rejected && verdict === "GREEN";
  const firstDiagnostic = selectedTrial?.diagnostics[0] ?? result.playability?.diagnostics[0];
  const additions = trialAdditions(selectedTrial);
  const label = rejected ? "REJECTED" : verdict ?? "N/A";
  const heading = selectedTrial
    ? selectedTrial.accepted
      ? "Trial accepted into the GREEN checkpoint"
      : "Trial rejected; checkpoint preserved"
    : green
      ? "Selected score is inside the hand model"
      : "Selected score needs human review";
  const detail = selectedTrial
    ? selectedTrial.step.detail
    : finalVerdict
      ? `The selected score returned ${finalVerdict} under the versioned ${result.options.profile.name} profile.`
      : "No tablature was produced, so no playability claim is available.";
  const faithfulness = result.faithfulness;

  function metric(label: string, value: number | null): React.JSX.Element {
    if (value === null) {
      return (
        <div aria-label={`${label}: N/A`} className="p6-metric">
          <span>{label}</span>
          <strong>N/A</strong>
        </div>
      );
    }
    const percent = Math.round(value * 100);
    return (
      <div className="p6-metric">
        <span>{label}</span>
        <strong>{percent}%</strong>
        <progress aria-label={`${label}: ${percent}%`} max="100" value={percent} />
      </div>
    );
  }

  return (
    <article className={`p6-oracle-card ${rejected ? "is-rejected" : green ? "is-green" : "is-amber"}`}>
      <div className="p6-card-label">
        <span>{selectedTrial ? "Trial verdict" : "Oracle verdict"}</span>
        <strong>{label}</strong>
      </div>
      <h3>{heading}</h3>
      <p>{detail}</p>
      <dl>
        <div>
          <dt>{selectedTrial ? "Proposed" : "Diagnostics"}</dt>
          <dd>{selectedTrial ? `+${additions.length} notes` : String(result.playability?.diagnostics.length ?? 0)}</dd>
        </div>
        <div>
          <dt>Location</dt>
          <dd>{firstDiagnostic ? `m. ${firstDiagnostic.measure} · ${firstDiagnostic.beat}` : "—"}</dd>
        </div>
        <div>
          <dt>Profile</dt>
          <dd>{result.options.profile.version}</dd>
        </div>
      </dl>
      <div className="p6-difficulty-result">
        <span>Difficulty gate</span>
        <strong>{difficulty ? `${TIER_LABELS[difficulty.options.tier]} · ${difficulty.difficulty.meets ? "PASS" : "REVIEW"}` : "Checking…"}</strong>
      </div>
      <div className="p6-difficulty-result">
        <span>Published grade estimate</span>
        <strong>
          {difficulty
            ? `Grade ${difficulty.published_grade.estimated_grade} (${difficulty.published_grade.likely_interval.lower}–${difficulty.published_grade.likely_interval.upper}) · low confidence`
            : "Checking…"}
        </strong>
      </div>
      {firstDiagnostic ? (
        <ol aria-label="Playability diagnostics" className="p6-diagnostics">
          {(selectedTrial?.diagnostics ?? result.playability?.diagnostics ?? []).map(
            (diagnostic, index) => (
              <li key={`${diagnostic.measure}-${diagnostic.beat}-${index}`}>
                <strong>{diagnostic.violation_type.replaceAll("_", " ")}</strong>
                <span>
                  Measure {diagnostic.measure} · beat {diagnostic.beat} · overage {diagnostic.overage}
                </span>
                {diagnostic.suggested_relaxations.length > 0 ? (
                  <small>{diagnostic.suggested_relaxations.join(" · ")}</small>
                ) : null}
              </li>
            ),
          )}
        </ol>
      ) : null}
      <section className="p6-fidelity" aria-label="Source fidelity evidence">
        <div>
          <span>Source fidelity</span>
          <strong>
            {faithfulness
              ? `${faithfulness.passed ? "PASS" : "REVIEW"} · ${faithfulness.evaluated_dimensions.length}/3 available`
              : "N/A"}
          </strong>
        </div>
        {faithfulness ? (
          <div className="p6-metrics">
            {metric("Melody", faithfulness.melody_f1)}
            {metric("Bass root", faithfulness.bass_root_accuracy)}
            {metric("Harmony", faithfulness.harmony_jaccard)}
          </div>
        ) : null}
      </section>
    </article>
  );
}

const TRACE_LABELS: Record<string, string> = {
  PIPELINE_CONFIGURED: "Plan",
  CANDIDATE_PROPOSED: "Propose",
  PROPOSE: "Propose",
  SOLVER_RETURNED_TAB: "Solve",
  SOLVER_RETURNED_NO_TAB: "No fingering",
  SOLVE: "Solve",
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

function TracePanel({
  result,
  trials,
  selectedTrial,
  onSelect,
}: {
  result: ArrangementResponse;
  trials: IncrementalTrialEvidence[];
  selectedTrial: IncrementalTrialEvidence | null;
  onSelect: (trial: IncrementalTrialEvidence | null) => void;
}): React.JSX.Element {
  const summary = summarizeTrace(result.trace);
  const additions = trialAdditions(selectedTrial);
  const selectedData = selectedTrial?.step.data;
  const firstOracle = result.trace.steps.findIndex((step) => step.event === "PLAYABILITY_CHECKED");
  const [publicStepIndex, setPublicStepIndex] = useState(
    firstOracle >= 0 ? firstOracle : Math.max(0, result.trace.steps.length - 1),
  );
  const publicStep = result.trace.steps[publicStepIndex];
  return (
    <section className="p6-trace" aria-labelledby="p6-trace-title">
      <header>
        <div>
          <p className="p6-eyebrow">Replay, not chain-of-thought</p>
          <h2 id="p6-trace-title">What changed, and why</h2>
        </div>
        <span>{summary.acceptedTrials} accepted · {summary.rejectedTrials} rejected · solver {summary.solverTrials}×</span>
      </header>
      <div className="p6-trace-grid">
        <ol>
          <li>
            <button type="button">
              <i className="is-green" />
              <span><strong>Verified seed</strong><small>Source melody checkpoint</small></span>
              <b>00</b>
            </button>
          </li>
          {trials.map((trial, index) => (
            <li key={`${trial.step.seq}-${trial.step.iteration}`}>
              <button
                aria-current={selectedTrial?.step.seq === trial.step.seq ? "step" : undefined}
                className={selectedTrial?.step.seq === trial.step.seq ? "is-active" : ""}
                onClick={() => onSelect(trial)}
                type="button"
              >
                <i className={trial.accepted ? "is-green" : "is-red"} />
                <span>
                  <strong>Trial {String(index + 1).padStart(2, "0")} {trial.accepted ? "accepted" : "rejected"}</strong>
                  <small>+{trialAdditions(trial).length} notes · {trial.reasonCode ?? trial.verdict ?? "checked"}</small>
                </span>
                <b>{String(index + 1).padStart(2, "0")}</b>
              </button>
            </li>
          ))}
          <li>
            <button
              aria-current={selectedTrial === null ? "step" : undefined}
              className={selectedTrial === null ? "is-active" : ""}
              onClick={() => onSelect(null)}
              type="button"
            >
              <i className={result.playability?.verdict === "GREEN" ? "is-green" : "is-amber"} />
              <span><strong>Selected checkpoint</strong><small>{result.status === "tab_produced" ? `${result.playability?.verdict ?? "N/A"} · ready to export` : "No fingering within budget"}</small></span>
              <b>{String(trials.length + 1).padStart(2, "0")}</b>
            </button>
          </li>
        </ol>
        <article className="p6-trace-detail">
          <div>
            <span>{selectedTrial ? "ADDITIVE_TRIAL" : "SELECTED_CHECKPOINT"}</span>
            <strong>#{String(selectedTrial?.step.seq ?? result.trace.steps.length).padStart(2, "0")}</strong>
          </div>
          <h3>{selectedTrial ? selectedTrial.step.detail : "Playback, notation and exports share this checkpoint."}</h3>
          <p>
            {selectedTrial
              ? selectedTrial.accepted
                ? "The full score stayed GREEN, so this additive batch entered the retained score."
                : "The full score did not satisfy every gate. The batch was removed and the prior GREEN checkpoint remained authoritative."
              : result.tab
                ? "This complete canonical Tab is the single source for the score, fretboard, audio and every exported file."
                : "The bounded search ended without a Tab. This is not an unsatisfiability proof."}
          </p>
          <dl>
            <div><dt>Proposed</dt><dd>{selectedTrial ? `+${additions.length}` : String(summary.proposedAdditions)}</dd></div>
            <div><dt>Accepted</dt><dd>{selectedTrial ? (selectedTrial.accepted ? `+${additions.length}` : "0") : String(summary.acceptedAdditions)}</dd></div>
            <div><dt>Solver</dt><dd>{selectedTrial?.step.data.solver_called === false ? "not called" : "called"}</dd></div>
            <div><dt>Checkpoint</dt><dd>{selectedTrial?.attemptedTab || result.tab ? "complete" : "digest only"}</dd></div>
          </dl>
          {selectedData ? (
            <details className="p6-typed-evidence">
              <summary>Typed evidence</summary>
              <pre>{JSON.stringify(selectedData, null, 2)}</pre>
            </details>
          ) : null}
        </article>
      </div>
      <details className="p6-full-trace" open={trials.length === 0}>
        <summary>Full public trace · {result.trace.steps.length} steps · {result.trace.schema_version}</summary>
        {result.trace.steps.length === 0 ? (
          <p>No public trace steps were recorded.</p>
        ) : (
          <div className="p6-full-trace-grid">
            <ol aria-label="Arrangement trace steps">
              {result.trace.steps.map((step, index) => (
                <li key={`${step.seq}-${step.event}`}>
                  <button
                    aria-current={index === publicStepIndex ? "step" : undefined}
                    className={index === publicStepIndex ? "is-active" : ""}
                    onClick={() => setPublicStepIndex(index)}
                    type="button"
                  >
                    <i className={String(step.data.verdict ?? "") === "GREEN" ? "is-green" : "is-amber"} />
                    <span>
                      <strong>{TRACE_LABELS[step.event] ?? step.event.replaceAll("_", " ")}</strong>
                      <small>{step.candidate_index === null ? "Pipeline" : `Candidate ${step.candidate_index}`}</small>
                    </span>
                    <b>{String(step.seq + 1).padStart(2, "0")}</b>
                  </button>
                </li>
              ))}
            </ol>
            <article className="p6-trace-detail" aria-live="polite">
              {publicStep ? (
                <>
                  <div><span>{publicStep.kind}</span><strong>#{String(publicStep.seq + 1).padStart(2, "0")}</strong></div>
                  <h3>{TRACE_LABELS[publicStep.event] ?? publicStep.event.replaceAll("_", " ")}</h3>
                  <p>{publicStep.detail}</p>
                  <details className="p6-typed-evidence">
                    <summary>Typed evidence</summary>
                    <pre>{JSON.stringify(publicStep.data, null, 2)}</pre>
                  </details>
                </>
              ) : null}
            </article>
          </div>
        )}
      </details>
    </section>
  );
}

function ResultProvenance({ result }: { result: ArrangementResponse }): React.JSX.Element | null {
  if (!result.tab) return null;
  const selection = result.trace.steps.find((step) => step.event === "CANDIDATE_SELECTED");
  const selectedIndex = selection?.candidate_index ?? null;
  const agentSelected = result.model.engine === "proxy" && selectedIndex !== null;
  const title = agentSelected
    ? `Agent candidate ${selectedIndex + 1} selected`
    : selectedIndex === null
      ? "No Agent candidate was used"
      : `Offline candidate ${selectedIndex + 1} selected`;
  const detail = agentSelected
    ? `${result.model.model_id} proposed this candidate; it survived the displayed deterministic gates.`
    : selectedIndex === null
      ? "Agent candidates were rejected or unavailable. This selected score comes entirely from the deterministic baseline."
      : "This score was produced by the offline candidate path; no remote Agent contributed notes.";
  return (
    <section className="p6-provenance" aria-label="Selected output provenance">
      <span>{agentSelected ? "Agent contribution" : "Deterministic provenance"}</span>
      <strong>{title}</strong>
      <p>{detail}</p>
    </section>
  );
}

function SourceEvidence({ result }: { result: ArrangementResponse }): React.JSX.Element {
  return (
    <section className="p6-source-evidence" aria-label="Source and version evidence">
      <div>
        <span>Input</span>
        <strong>{result.source.filename}</strong>
        <strong>{result.source.importer_version}</strong>
        <small>
          {result.stamps.fingering_solver_version} · {result.stamps.score_solver_version} ·{" "}
          {result.stamps.left_hand_model_version}
        </small>
        <small>
          Published-score fingering · {result.stamps.published_fingering_ranker_version} ·{" "}
          {result.stamps.published_fingering_model_sha256?.slice(0, 12)}
        </small>
        <small>SHA-256 {result.source.raw_sha256}</small>
      </div>
      {result.source.warnings.length > 0 ? (
        <ul aria-label="Source import warnings">
          {result.source.warnings.map((warning, index) => (
            <li key={`${warning.code}-${index}`}>
              <strong>{warning.code}</strong>
              <span>{warning.message}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p>No source import warnings.</p>
      )}
    </section>
  );
}

function AlternativePanel({
  result,
  inspectedCandidate,
  onInspect,
}: {
  result: ArrangementResponse;
  inspectedCandidate: number | null;
  onInspect: (candidateIndex: number | null) => void;
}): React.JSX.Element | null {
  if (result.alternatives.length === 0) return null;
  const winner = selectedCandidateIndex(result);
  const totalCalls = result.alternatives.reduce(
    (total, alternative) => total + alternative.work.model_calls,
    0,
  );
  return (
    <section className="p6-alternatives" aria-labelledby="p6-alternatives-title">
      <header>
        <div>
          <p className="p6-eyebrow">Opt-in breadth / actual work</p>
          <h2 id="p6-alternatives-title">Verified candidate pool</h2>
          <p>
            Every checkpoint below passed the same oracle and fidelity checker. Breadth used {totalCalls} logical model call{totalCalls === 1 ? "" : "s"}.
          </p>
        </div>
        {inspectedCandidate !== null ? (
          <button onClick={() => onInspect(null)} type="button">Return to selected checkpoint</button>
        ) : null}
      </header>
      <div className="p6-alternative-grid">
        {result.alternatives.map((alternative) => {
          const inspected = inspectedCandidate === alternative.candidate_index;
          const selected = winner === alternative.candidate_index;
          const critic = alternative.observed_critic;
          return (
            <article className={inspected ? "is-inspected" : ""} key={alternative.candidate_index}>
              <div>
                <span>Candidate {alternative.candidate_index + 1}</span>
                <strong>{selected ? "Selected by ranking" : "Verified alternative"}</strong>
              </div>
              <dl>
                <div><dt>Oracle</dt><dd>{alternative.playability.verdict}</dd></div>
                <div><dt>Fidelity</dt><dd>{alternative.faithfulness.passed ? "PASS" : "REVIEW"}</dd></div>
                <div><dt>Model work</dt><dd>{alternative.work.model_calls} call{alternative.work.model_calls === 1 ? "" : "s"}</dd></div>
                <div><dt>Solver trials</dt><dd>{alternative.work.trial_solver_calls}</dd></div>
                <div><dt>Additions kept</dt><dd>{alternative.work.accepted_additions} / {alternative.work.proposed_additions}</dd></div>
                <div>
                  <dt>Critic metadata</dt>
                  <dd>{critic.status && critic.overall !== null ? `${Math.round(critic.overall * 100)}% observed` : "not run"}</dd>
                </div>
              </dl>
              <small>{alternative.proposal_status.replaceAll("_", " ").toLowerCase()} · critic is not human taste evidence</small>
              <button
                aria-pressed={inspected}
                disabled={inspected}
                onClick={() => onInspect(alternative.candidate_index)}
                type="button"
              >
                {inspected ? "Inspecting checkpoint" : `Inspect candidate ${alternative.candidate_index + 1}`}
              </button>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function resultAtAlternative(
  result: ArrangementResponse,
  alternative: VerifiedAlternative | null,
): ArrangementResponse {
  if (!alternative) return result;
  return {
    ...result,
    tab: alternative.tab,
    ascii: alternative.ascii,
    playability: alternative.playability,
    faithfulness: alternative.faithfulness,
  };
}

function LibraryView({
  entries,
  notice,
  onClose,
  onOpen,
  onRemove,
}: {
  entries: LocalLibraryEntry[];
  notice: string | null;
  onClose: () => void;
  onOpen: (entry: LocalLibraryEntry) => void;
  onRemove: (id: string) => void;
}): React.JSX.Element {
  return (
    <section className="p6-library" aria-labelledby="p6-library-title">
      <div className="p6-lab-head">
        <div>
          <p className="p6-eyebrow">This browser / canonical artifacts</p>
          <h2 id="p6-library-title">Personal library</h2>
          <p>Stores result contracts and provenance locally. It does not store source bytes, sync to a cloud account, or claim learned retrieval.</p>
        </div>
        <button className="p6-close" onClick={onClose} type="button">Back to score</button>
      </div>
      {notice ? <p className="p6-library-notice" role="status">{notice}</p> : null}
      {entries.length === 0 ? (
        <div className="p6-lab-empty">
          <strong>No saved canonical results yet.</strong>
          <p>Return to a score and save its checked result. The original upload is not copied into this library.</p>
        </div>
      ) : (
        <div className="p6-library-grid">
          {entries.map((entry) => {
            const saved = entry.saved_at.replace("T", " ").slice(0, 16);
            return (
              <article key={entry.id}>
                <div>
                  <span>{saved} UTC</span>
                  <strong>{entry.result.playability?.verdict ?? "NO TAB"}</strong>
                </div>
                <h3>{entry.result.score.title || "Untitled score"}</h3>
                <p>{entry.result.source.filename}</p>
                <dl>
                  <div><dt>Model</dt><dd>{entry.result.model.model_id}</dd></div>
                  <div><dt>Fidelity</dt><dd>{entry.result.faithfulness ? `${entry.result.faithfulness.evaluated_dimensions.length}/3` : "N/A"}</dd></div>
                  <div><dt>Alternatives</dt><dd>{entry.result.alternatives.length}</dd></div>
                  <div><dt>Source SHA</dt><dd>{entry.result.source.raw_sha256.slice(0, 12)}…</dd></div>
                </dl>
                <div className="p6-library-actions">
                  <button onClick={() => onOpen(entry)} type="button">Open evidence</button>
                  <button onClick={() => onRemove(entry.id)} type="button">Remove</button>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}

function AudioPreview({
  tab,
  label,
  tempoBpm,
}: {
  tab: CanonicalTab;
  label: "A" | "B";
  tempoBpm: number;
}): React.JSX.Element {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    let created: string | null = null;
    setUrl(null);
    setError(false);
    void exportAudio(tab, tempoBpm, controller.signal)
      .then((asset) => {
        created = URL.createObjectURL(asset.blob);
        setUrl(created);
      })
      .catch((caught: unknown) => {
        if (!(caught instanceof DOMException && caught.name === "AbortError")) setError(true);
      });
    return () => {
      controller.abort();
      if (created) URL.revokeObjectURL(created);
    };
  }, [tab, tempoBpm]);

  if (error) return <p className="p6-audio-status">The synthesized comparison is unavailable in this runtime.</p>;
  if (!url) return <p className="p6-audio-status">Rendering with the shared SoundFont…</p>;
  return <audio aria-label={`Comparison ${label}`} controls preload="metadata" src={url} />;
}

const VOICE_ROLES: readonly VoiceRole[] = ["melody", "bass", "harmony"];

function scoreMeasureCount(result: ArrangementResponse): number {
  const beatsPerMeasure = scoreBeatsPerMeasure(result);
  const duration = result.score.duration_beats
    ? fractionBeats(result.score.duration_beats)
    : Math.max(
        0,
        ...(result.tab?.notes.map(
          (note) => fractionBeats(note.onset) + fractionBeats(note.duration),
        ) ?? []),
      );
  return Math.max(1, Math.ceil(duration / beatsPerMeasure));
}

function RevisionPanel({
  result,
  sourceFile,
  onOutcome,
  maxMeasure,
  measureSelection,
  onMeasureSelectionChange,
}: {
  result: ArrangementResponse;
  sourceFile: File | null;
  onOutcome: (outcome: SectionRegenerationResponse) => void;
  maxMeasure: number;
  measureSelection: MeasureRange;
  onMeasureSelectionChange: (selection: MeasureRange) => void;
}): React.JSX.Element {
  const [lockedVoices, setLockedVoices] = useState<VoiceRole[]>(["melody"]);
  const [running, setRunning] = useState(false);
  const [outcome, setOutcome] = useState<SectionRegenerationResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setOutcome(null);
  }, [result.source.raw_sha256]);

  async function submit(event: React.FormEvent): Promise<void> {
    event.preventDefault();
    if (!sourceFile || running || !result.tab || !result.editable_target) return;
    setRunning(true);
    setError(null);
    setOutcome(null);
    try {
      const response = await regenerateSection(sourceFile, result, {
        startMeasure: measureSelection.start,
        endMeasure: measureSelection.end,
        lockedVoices,
      });
      setOutcome(response);
      onOutcome(response);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Section regeneration failed.");
    } finally {
      setRunning(false);
    }
  }

  return (
    <section className="p7-edit-card" aria-labelledby="p7-revision-title">
      <header>
        <div>
          <p className="p6-eyebrow">Measure-scoped revision</p>
          <h2 id="p7-revision-title">Regenerate a section</h2>
        </div>
        <span>{result.model.engine === "proxy" ? "at most 1 model call" : "0 model calls"}</span>
      </header>
      <p>
        Replace only unlocked voices inside the selected measures. Source melody attacks stay
        anchored, and any non-GREEN or unfaithful attempt preserves this checkpoint.
      </p>
      <form onSubmit={(event) => void submit(event)}>
        <label>
          <span>From measure</span>
          <input
            max={maxMeasure}
            min="1"
            onChange={(event) => {
              const next = Number(event.target.value);
              onMeasureSelectionChange(
                normalizeMeasureRange(
                  next,
                  Math.max(next, measureSelection.end),
                  maxMeasure,
                ),
              );
            }}
            type="number"
            value={measureSelection.start}
          />
        </label>
        <label>
          <span>To measure</span>
          <input
            max={Math.min(
              maxMeasure,
              measureSelection.start + MAX_SECTION_MEASURES - 1,
            )}
            min={measureSelection.start}
            onChange={(event) =>
              onMeasureSelectionChange(
                normalizeMeasureRange(
                  measureSelection.start,
                  Number(event.target.value),
                  maxMeasure,
                ),
              )
            }
            type="number"
            value={measureSelection.end}
          />
        </label>
        <fieldset>
          <legend>Lock voices</legend>
          {VOICE_ROLES.map((voice) => (
            <label key={voice}>
              <input
                checked={lockedVoices.includes(voice)}
                onChange={(event) =>
                  setLockedVoices((current) =>
                    VOICE_ROLES.filter((item) =>
                      item === voice ? event.target.checked : current.includes(item),
                    ),
                  )
                }
                type="checkbox"
              />
              <span>{voice}</span>
            </label>
          ))}
        </fieldset>
        <button disabled={!sourceFile || running || !result.tab} type="submit">
          {running ? "Checking revision…" : "Regenerate & verify"}
        </button>
      </form>
      {!sourceFile ? (
        <small role="status">Source bytes are unavailable for this library checkpoint.</small>
      ) : null}
      {outcome ? (
        <p className={`p7-operation-status is-${outcome.status}`} role="status">
          {outcome.status === "accepted"
            ? "Accepted: the revised section is now the selected GREEN checkpoint."
            : outcome.status === "unchanged"
              ? "No score change was needed; the selected checkpoint is unchanged."
              : `Preserved: ${outcome.revision.reason ?? "the revision did not pass every gate"}.`}
        </p>
      ) : null}
      {error ? <p className="p7-operation-error" role="alert">{error}</p> : null}
    </section>
  );
}

function FingeringEditor({
  result,
  onEdit,
}: {
  result: ArrangementResponse;
  onEdit: (noteIndex: number, leftFinger: number) => Promise<FingeringEditResponse>;
}): React.JSX.Element {
  const fretted = useMemo(
    () =>
      result.tab?.notes
        .map((note, noteIndex) => ({ note, noteIndex }))
        .filter(({ note }) => note.fret > 0) ?? [],
    [result.tab],
  );
  const [noteIndex, setNoteIndex] = useState(fretted[0]?.noteIndex ?? 0);
  const selected = fretted.find((item) => item.noteIndex === noteIndex) ?? fretted[0];
  const [finger, setFinger] = useState(selected?.note.left_finger ?? 1);
  const [running, setRunning] = useState(false);
  const [outcome, setOutcome] = useState<FingeringEditResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const first = fretted[0];
    setNoteIndex(first?.noteIndex ?? 0);
    setFinger(first?.note.left_finger ?? 1);
  }, [fretted]);

  useEffect(() => {
    setOutcome((current) => {
      if (!current || !result.tab) return current;
      return canonicalTabJSON(current.tab) === canonicalTabJSON(result.tab) ? current : null;
    });
  }, [result.tab]);

  useEffect(() => setOutcome(null), [result.source.raw_sha256]);

  async function submit(event: React.FormEvent): Promise<void> {
    event.preventDefault();
    if (!selected || running) return;
    setRunning(true);
    setError(null);
    try {
      const response = await onEdit(selected.noteIndex, finger);
      setOutcome(response);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Fingering edit failed.");
    } finally {
      setRunning(false);
    }
  }

  return (
    <section className="p7-edit-card" aria-labelledby="p7-fingering-title">
      <header>
        <div>
          <p className="p6-eyebrow">Left hand / manual correction</p>
          <h2 id="p7-fingering-title">Edit a finger number</h2>
        </div>
        <span>Oracle recheck</span>
      </header>
      <p>String and fret stay fixed. Only finger 1–4 changes, and RED/AMBER attempts roll back.</p>
      {selected ? (
        <form onSubmit={(event) => void submit(event)}>
          <label className="p7-note-picker">
            <span>Fretted note</span>
            <select
              onChange={(event) => {
                const next = Number(event.target.value);
                const item = fretted.find((candidate) => candidate.noteIndex === next);
                setNoteIndex(next);
                setFinger(item?.note.left_finger ?? 1);
              }}
              value={selected.noteIndex}
            >
              {fretted.map(({ note, noteIndex: index }) => (
                <option key={index} value={index}>
                  #{index + 1} · beat {note.onset} · string {6 - note.string} fret {note.fret} ·
                  finger {note.left_finger}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Left finger</span>
            <select onChange={(event) => setFinger(Number(event.target.value))} value={finger}>
              {[1, 2, 3, 4].map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
          </label>
          <button disabled={running || finger === selected.note.left_finger} type="submit">
            {running ? "Rechecking…" : "Apply finger & recheck"}
          </button>
        </form>
      ) : (
        <small>This checkpoint contains no fretted notes to edit.</small>
      )}
      {outcome ? (
        <p className={`p7-operation-status is-${outcome.status}`} role="status">
          {outcome.status === "applied"
            ? `Applied: note ${outcome.edit.note_index + 1} now uses finger ${outcome.edit.requested_finger}.`
            : outcome.status === "unchanged"
              ? "That finger was already selected."
              : `Rejected: the attempted full-score verdict was ${outcome.attempted_playability.verdict}.`}
        </p>
      ) : null}
      {error ? <p className="p7-operation-error" role="alert">{error}</p> : null}
    </section>
  );
}

const FEEDBACK_TAGS = ["natural", "easy to play", "style fit", "awkward", "too dense"];

function FeedbackPanel({
  feedback,
  onClear,
  onSave,
}: {
  feedback: FeedbackDocument;
  onClear: () => void;
  onSave: (rating: number, tags: string[], note: string) => void;
}): React.JSX.Element {
  const [rating, setRating] = useState(4);
  const [tags, setTags] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [notice, setNotice] = useState<string | null>(null);

  return (
    <section className="p7-feedback" aria-labelledby="p7-feedback-title">
      <header>
        <div>
          <p className="p6-eyebrow">Anonymous / this browser</p>
          <h2 id="p7-feedback-title">Preference evidence</h2>
        </div>
        <span>{feedback.entries.length} saved event{feedback.entries.length === 1 ? "" : "s"}</span>
      </header>
      <p>
        Ratings, blind A/B choices and accepted or rejected finger corrections stay local until
        export. They are evidence for later analysis; API calls do not retrain the model.
      </p>
      <div className="p7-feedback-grid">
        <label>
          <span>Overall result</span>
          <select onChange={(event) => setRating(Number(event.target.value))} value={rating}>
            {[1, 2, 3, 4, 5].map((value) => <option key={value} value={value}>{value} / 5</option>)}
          </select>
        </label>
        <fieldset>
          <legend>Reason tags</legend>
          {FEEDBACK_TAGS.map((tag) => (
            <label key={tag}>
              <input
                checked={tags.includes(tag)}
                onChange={(event) =>
                  setTags((current) =>
                    event.target.checked
                      ? [...current, tag]
                      : current.filter((item) => item !== tag),
                  )
                }
                type="checkbox"
              />
              <span>{tag}</span>
            </label>
          ))}
        </fieldset>
        <label className="p7-feedback-note">
          <span>Optional note</span>
          <textarea
            maxLength={1000}
            onChange={(event) => setNote(event.target.value)}
            placeholder="What felt natural or awkward?"
            value={note}
          />
        </label>
      </div>
      <div className="p7-feedback-actions">
        <button
          onClick={() => {
            onSave(rating, tags, note.trim());
            setNotice("Anonymous rating saved locally.");
            setNote("");
          }}
          type="button"
        >
          Save feedback
        </button>
        <button
          disabled={feedback.entries.length === 0}
          onClick={() => saveDownload(feedbackDownload(feedback))}
          type="button"
        >
          Export JSON
        </button>
        <button
          disabled={feedback.entries.length === 0}
          onClick={() => {
            onClear();
            setNotice("Local feedback cleared.");
          }}
          type="button"
        >
          Clear local data
        </button>
      </div>
      {notice ? <small role="status">{notice}</small> : null}
    </section>
  );
}

function DemoLab({
  result,
  difficulty,
  onClose,
  onPreference,
}: {
  result: ArrangementResponse;
  difficulty: DifficultyCheckResponse | null;
  onClose: () => void;
  onPreference: (candidates: [number, number], preferred: number) => void;
}): React.JSX.Element {
  const [choice, setChoice] = useState<"A" | "B" | "none" | null>(null);
  const pair = fairAlternativePair(result);
  const scorecard = computeLiveRunScorecard(result, difficulty);
  const firstIsA = Number.parseInt(result.source.raw_sha256.slice(-1), 16) % 2 === 0;
  const candidates = pair
    ? ({
        A: firstIsA ? pair[0] : pair[1],
        B: firstIsA ? pair[1] : pair[0],
      } as const)
    : null;
  const selectedIndex = selectedCandidateIndex(result);
  const selectedLabel = candidates
    ? candidates.A.candidate_index === selectedIndex
      ? "A"
      : candidates.B.candidate_index === selectedIndex
        ? "B"
        : null
    : null;
  const preferredSelected = selectedLabel !== null && choice === selectedLabel;

  function choose(next: "A" | "B" | "none"): void {
    setChoice(next);
    if (candidates && next !== "none") {
      onPreference(
        [candidates.A.candidate_index, candidates.B.candidate_index],
        candidates[next].candidate_index,
      );
    }
  }

  return (
    <section className="p6-lab" aria-labelledby="p6-lab-title">
      <div className="p6-lab-head">
        <div>
          <p className="p6-eyebrow">Secondary workspace / same run evidence</p>
          <h2 id="p6-lab-title">Blind A/B audition</h2>
          <p>Same source, model identity, tempo, SoundFont, model-call budget and deterministic checkers.</p>
        </div>
        <button className="p6-close" onClick={onClose} type="button">Back to score</button>
      </div>
      {candidates ? (
        <>
          <div className="p6-ab-grid">
            {(["A", "B"] as const).map((label) => {
              const alternative = candidates[label];
              return (
                <article key={label}>
                  <div className="p6-ab-label">
                    <span>{label}</span>
                    <small>{choice ? `candidate ${alternative.candidate_index + 1}${alternative.candidate_index === selectedIndex ? " · selected" : ""}` : "identity hidden"}</small>
                  </div>
                  <div className="p6-wave" aria-hidden="true">
                    {[18, 30, 14, 42, 25, 50, 34, 22, 44, 28, 38, 17, 47, 31, 19, 36, 26, 43].map((height, index) => (
                      <i key={`${label}-${index}`} style={{ height: alternative.candidate_index % 2 === 0 ? height : 58 - height / 2 }} />
                    ))}
                  </div>
                  <div className="p6-audio-slot">
                    <AudioPreview
                      label={label}
                      tab={alternative.tab}
                      tempoBpm={result.options.effective_tempo_bpm}
                    />
                  </div>
                </article>
              );
            })}
          </div>
          <div className="p6-lab-bottom">
            <article className="p6-choice-card">
              <span>Your call</span>
              <h3>Which one would you keep?</h3>
              <div>
                <button disabled={choice !== null} onClick={() => choose("A")} type="button">Choose A</button>
                <button disabled={choice !== null} onClick={() => choose("B")} type="button">Choose B</button>
                <button disabled={choice !== null} onClick={() => choose("none")} type="button">No preference</button>
              </div>
              <small>
                {choice
                  ? selectedLabel
                    ? `Revealed: ${selectedLabel} is the ranked selection; both outputs are independently GREEN.`
                    : "Revealed: neither candidate became the selected checkpoint; both remain verified alternatives."
                  : `Identity is revealed only after the choice · ${pair?.[0].work.model_calls ?? 0} model call${pair?.[0].work.model_calls === 1 ? "" : "s"} per output.`}
              </small>
            </article>
            <article className="p6-scorecard">
              <div><span>Live run scorecard</span><strong>current run</strong></div>
              <dl>
                <div><dt>Oracle</dt><dd>{scorecard.oracle}</dd></div>
                <div><dt>Fidelity</dt><dd>{scorecard.fidelity}</dd></div>
                <div><dt>Difficulty</dt><dd>{scorecard.difficulty}</dd></div>
                <div><dt>Verified alternatives</dt><dd>{scorecard.verifiedAlternatives}</dd></div>
                <div><dt>Actual model calls</dt><dd>{scorecard.actualModelCalls}</dd></div>
                <div><dt>Fair A/B</dt><dd>{scorecard.fairComparison}</dd></div>
                <div><dt>Product policy</dt><dd>{scorecard.productPolicy}</dd></div>
                <div><dt>Legacy repair evidence</dt><dd>{scorecard.repairAblation}</dd></div>
                <div><dt>Listener preferred retained</dt><dd>{choice === null || choice === "none" ? "—" : preferredSelected ? "1 / 1" : "0 / 1"}</dd></div>
              </dl>
              <small>Recomputed from this typed result · local choice only · no model learning claim</small>
            </article>
          </div>
        </>
      ) : (
        <div className="p6-lab-empty">
          <strong>A fair A/B is unavailable in this run.</strong>
          <p>Run the explicitly enabled proxy path with candidate breadth 2 or more. Both outputs must have the same actual model-call count and pass the same checkers; no synthetic alternative is invented.</p>
        </div>
      )}
    </section>
  );
}

export default function PerformanceWorkspace({
  capabilities,
  initialTier,
  result: incomingResult,
  sourceFile,
  onReset,
}: {
  capabilities: CapabilitiesResponse;
  initialTier: DifficultyTierName;
  result: ArrangementResponse;
  sourceFile: File;
  onReset: () => void;
}): React.JSX.Element {
  const [result, setResult] = useState(incomingResult);
  const [view, setView] = useState<WorkspaceView>("workspace");
  const [playing, setPlaying] = useState(false);
  const [playerReady, setPlayerReady] = useState(false);
  const [positionMs, setPositionMs] = useState(0);
  const [durationMs, setDurationMs] = useState(0);
  const [scale, setScale] = useState(() =>
    typeof window.matchMedia === "function" && window.matchMedia("(max-width: 700px)").matches
      ? 0.6
      : 0.8,
  );
  const [tier, setTier] = useState<DifficultyTierName>(initialTier);
  const [difficulty, setDifficulty] = useState<DifficultyCheckResponse | null>(null);
  const [inspectedCandidate, setInspectedCandidate] = useState<number | null>(null);
  const [libraryEntries, setLibraryEntries] = useState<LocalLibraryEntry[]>(() =>
    readLocalLibrary(),
  );
  const [libraryNotice, setLibraryNotice] = useState<string | null>(null);
  const [workspaceNotice, setWorkspaceNotice] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<FeedbackDocument>(() => readFeedback());
  const maxMeasure = scoreMeasureCount(result);
  const [revisionSelection, setRevisionSelection] = useState<MeasureRange>({
    start: 1,
    end: 1,
  });
  const headingRef = useRef<HTMLHeadingElement>(null);
  const apiRef = useRef<AlphaTabApi | null>(null);
  const trials = useMemo(() => incrementalTrials(result.trace), [result.trace]);
  const rejectedTrial = useMemo(
    () => [...trials].reverse().find((trial) => !trial.accepted && trial.attemptedTab) ?? null,
    [trials],
  );
  const [selectedSeq, setSelectedSeq] = useState<number | null>(null);
  const selectedTrial = useMemo(
    () => trials.find((trial) => trial.step.seq === selectedSeq) ?? null,
    [selectedSeq, trials],
  );
  const inspectedAlternative = useMemo(
    () =>
      result.alternatives.find(
        (alternative) => alternative.candidate_index === inspectedCandidate,
      ) ?? null,
    [inspectedCandidate, result.alternatives],
  );
  const displayedResult = useMemo(
    () => resultAtAlternative(result, inspectedAlternative),
    [inspectedAlternative, result],
  );
  const inspectionTab = selectedTrial?.attemptedTab ?? displayedResult.tab;
  const notationResult = useMemo(
    () =>
      inspectionTab && inspectionTab !== displayedResult.tab
        ? { ...displayedResult, tab: inspectionTab }
        : displayedResult,
    [displayedResult, inspectionTab],
  );
  const positionedNotes = useMemo(
    () => (inspectionTab ? positionTab(inspectionTab) : []),
    [inspectionTab],
  );
  const currentBeat = millisecondsToBeat(positionMs, result.options.effective_tempo_bpm);
  const activeNotes = activeNotesAtBeat(positionedNotes, currentBeat);
  const beatsPerMeasure = scoreBeatsPerMeasure(result);
  const currentMeasure = beatToMeasure(currentBeat, beatsPerMeasure);
  const availableSourceFile =
    result.source.raw_sha256 === incomingResult.source.raw_sha256 ? sourceFile : null;
  const measureSelectionEnabled =
    availableSourceFile !== null && selectedTrial === null && inspectedAlternative === null;
  const handlePlayerPosition = useCallback((current: number, end: number) => {
    setPositionMs(current);
    setDurationMs(end);
  }, []);

  useEffect(() => {
    setResult(incomingResult);
  }, [incomingResult]);

  useEffect(() => {
    setRevisionSelection({ start: 1, end: 1 });
  }, [result.source.raw_sha256]);

  useEffect(() => {
    setRevisionSelection((current) =>
      normalizeMeasureRange(current.start, current.end, maxMeasure),
    );
  }, [maxMeasure]);

  useEffect(() => {
    if (view === "workspace") headingRef.current?.focus({ preventScroll: true });
    setInspectedCandidate(null);
  }, [result, view]);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return undefined;
    const viewport = window.matchMedia("(max-width: 700px)");
    const resizeNotation = (event: MediaQueryListEvent): void => {
      const next = event.matches ? 0.6 : 0.8;
      setScale(next);
      if (apiRef.current) {
        apiRef.current.settings.display.scale = next;
        apiRef.current.updateSettings();
        apiRef.current.render();
      }
    };
    viewport.addEventListener("change", resizeNotation);
    return () => viewport.removeEventListener("change", resizeNotation);
  }, []);

  useEffect(() => {
    setSelectedSeq(null);
  }, [result.trace]);

  useEffect(() => {
    if (!displayedResult.tab) {
      setDifficulty(null);
      return;
    }
    const controller = new AbortController();
    setDifficulty(null);
    void checkDifficulty(
      displayedResult.tab,
      {
        tier,
        tempoBpm: result.options.effective_tempo_bpm,
        beatsPerBar: beatsPerMeasure,
      },
      controller.signal,
    )
      .then(setDifficulty)
      .catch((caught: unknown) => {
        if (!(caught instanceof DOMException && caught.name === "AbortError")) {
          setDifficulty(null);
        }
      });
    return () => controller.abort();
  }, [beatsPerMeasure, displayedResult.tab, result.options.effective_tempo_bpm, tier]);

  function selectTrial(trial: IncrementalTrialEvidence | null): void {
    setInspectedCandidate(null);
    setSelectedSeq(trial?.step.seq ?? null);
    const diagnostic = trial?.diagnostics[0];
    if (diagnostic) {
      const beat = (diagnostic.measure - 1) * beatsPerMeasure + fractionBeats(diagnostic.beat);
      const milliseconds = (beat * 60_000) / result.options.effective_tempo_bpm;
      setPositionMs(milliseconds);
      if (apiRef.current && playerReady) apiRef.current.timePosition = milliseconds;
    }
  }

  function inspectCandidate(candidateIndex: number | null): void {
    setSelectedSeq(null);
    setInspectedCandidate(candidateIndex);
    setPositionMs(0);
    if (apiRef.current && playerReady) apiRef.current.timePosition = 0;
  }

  function saveResult(): void {
    try {
      setLibraryEntries(saveLocalResult(result));
      setLibraryNotice("Canonical result and provenance saved in this browser.");
    } catch {
      setLibraryNotice("This browser could not save the local result.");
    }
  }

  function openLibraryEntry(entry: LocalLibraryEntry): void {
    setResult(entry.result);
    setView("workspace");
    setLibraryNotice("Opened a saved canonical result; source bytes were not restored.");
  }

  function removeLibraryEntry(id: string): void {
    try {
      setLibraryEntries(removeLocalResult(id));
      setLibraryNotice("Saved result removed from this browser.");
    } catch {
      setLibraryNotice("This browser could not update the local library.");
    }
  }

  function feedbackContext() {
    return {
      source_sha256: result.source.raw_sha256,
      model_id: result.model.model_id,
      player_profile: result.options.profile.name,
      style: result.options.style,
      difficulty_tier: result.options.difficulty_tier,
      technique_profile: result.options.technique_profile,
    } as const;
  }

  function handleRevision(outcome: SectionRegenerationResponse): void {
    if (outcome.status !== "accepted") {
      setWorkspaceNotice(
        outcome.status === "unchanged"
          ? "Section revision made no checkpoint change."
          : `Section revision rolled back (${outcome.revision.reason ?? "gate not passed"}).`,
      );
      return;
    }
    setResult((current) => ({
      ...current,
      options: {
        ...current.options,
        profile: outcome.options.profile,
        style: outcome.options.style,
        difficulty_tier: outcome.options.difficulty_tier,
        technique_profile: outcome.options.technique_profile,
      },
      model: outcome.model,
      editable_target: outcome.editable_target,
      tab: outcome.tab,
      ascii: outcome.ascii,
      playability: outcome.playability,
      faithfulness: outcome.faithfulness,
      alternatives: [],
      stamps: {
        ...current.stamps,
        ...outcome.stamps,
        local_checkpoint_origin: "section-regeneration@0.1.0",
      },
    }));
    setSelectedSeq(null);
    setInspectedCandidate(null);
    setPositionMs(0);
    setWorkspaceNotice("Section revision accepted as the selected GREEN checkpoint.");
  }

  async function handleFingerEdit(
    noteIndex: number,
    leftFinger: number,
  ): Promise<FingeringEditResponse> {
    if (!result.tab) throw new Error("The selected checkpoint has no Tab to edit.");
    const outcome = await editLeftFinger(
      result.tab,
      noteIndex,
      leftFinger,
      result.options.profile.name,
      result.options.effective_tempo_bpm,
      beatsPerMeasure,
    );
    setFeedback(
      appendFeedback({
        ...feedbackContext(),
        kind: "fingering_correction",
        note_index: outcome.edit.note_index,
        before_finger: outcome.edit.before_finger,
        requested_finger: outcome.edit.requested_finger,
        outcome: outcome.status,
      }),
    );
    if (outcome.status === "applied") {
      setResult((current) => ({
        ...current,
        tab: outcome.tab,
        ascii: outcome.ascii,
        playability: outcome.playability,
        alternatives: [],
        stamps: {
          ...current.stamps,
          ...outcome.stamps,
          local_checkpoint_origin: "left-hand-fingering-edit@0.1.0",
        },
      }));
      setSelectedSeq(null);
      setInspectedCandidate(null);
      setWorkspaceNotice("Manual finger correction accepted after a full Oracle recheck.");
    } else if (outcome.status === "rejected") {
      setWorkspaceNotice("Manual finger correction was rejected; the checkpoint was preserved.");
    }
    return outcome;
  }

  function recordABPreference(candidates: [number, number], preferred: number): void {
    setFeedback(
      appendFeedback({
        ...feedbackContext(),
        kind: "ab_preference",
        candidate_indices: candidates,
        preferred_candidate_index: preferred,
      }),
    );
  }

  function recordRating(rating: number, tags: string[], note: string): void {
    setFeedback(
      appendFeedback({
        ...feedbackContext(),
        kind: "rating",
        rating,
        tags,
        note,
      }),
    );
  }

  function changeScale(next: number): void {
    const normalized = Math.max(0.55, Math.min(1.15, next));
    setScale(normalized);
    if (apiRef.current) {
      apiRef.current.settings.display.scale = normalized;
      apiRef.current.updateSettings();
      apiRef.current.render();
    }
  }

  const fidelityStatus = result.faithfulness
    ? `available fidelity ${result.faithfulness.passed ? "passed" : "needs review"} (${result.faithfulness.evaluated_dimensions.length}/3)`
    : "fidelity unavailable";
  const runLabel = inspectedAlternative
    ? `Candidate ${inspectedAlternative.candidate_index + 1} under inspection / independently checked`
    : result.status === "no_fingering_within_budget"
      ? "Arrangement evidence / bounded search ended"
      : result.playability?.verdict !== "GREEN"
        ? "Arrangement evidence / playability needs review"
        : `Arrangement evidence / playability passed · ${fidelityStatus}`;

  return (
    <div className="p6-preview">
      <header className="p6-header">
        <a className="p6-brand" href="/" aria-label="Fretsure home">
          <Mark />
          <span>Fret<em>sure</em></span>
        </a>
        <div className="p6-preview-stamp"><i />{result.model.engine} · {result.model.model_id}</div>
        <nav aria-label="Result sections">
          <button className={view === "workspace" ? "is-active" : ""} onClick={() => setView("workspace")} type="button">Workspace</button>
          <button className={view === "lab" ? "is-active" : ""} onClick={() => setView("lab")} type="button">Demo Lab</button>
          <button className={view === "library" ? "is-active" : ""} onClick={() => setView("library")} type="button">Library</button>
          <button aria-label="Arrange another" onClick={onReset} type="button">New score</button>
        </nav>
      </header>

      <main className="p6-main">
        {view === "lab" ? (
          <DemoLab
            difficulty={difficulty}
            onClose={() => setView("workspace")}
            onPreference={recordABPreference}
            result={result}
          />
        ) : view === "library" ? (
          <LibraryView
            entries={libraryEntries}
            notice={libraryNotice}
            onClose={() => setView("workspace")}
            onOpen={openLibraryEntry}
            onRemove={removeLibraryEntry}
          />
        ) : (
          <>
            <section className="p6-title-row">
              <div>
                <p className="p6-eyebrow">{runLabel}</p>
                <h1 ref={headingRef} tabIndex={-1}>{result.score.title || "Untitled score"}</h1>
                <p className="p6-score-meta">
                  <span>{result.score.key}</span>
                  <span>{result.score.time_signature.numerator}/{result.score.time_signature.denominator}</span>
                  <span>{result.options.effective_tempo_bpm} BPM</span>
                  <span>{result.score.note_count} source notes</span>
                </p>
              </div>
              <div className="p6-run-status">
                {inspectedAlternative ? <span className="p6-green-status"><i />Candidate {inspectedAlternative.candidate_index + 1} · GREEN</span> : null}
                {result.playability?.verdict === "GREEN" ? <span className="p6-green-status"><i />Selected checkpoint · GREEN</span> : null}
                {result.playability && result.playability.verdict !== "GREEN" ? <span className="p6-red-status">Selected checkpoint · {result.playability.verdict}</span> : null}
                {rejectedTrial ? <span className="p6-red-status">Trial {rejectedTrial.step.iteration ?? "—"} rolled back</span> : null}
                <button className="p6-save-local" onClick={saveResult} type="button">Save to local library</button>
                {libraryNotice ? <small role="status">{libraryNotice}</small> : null}
                {workspaceNotice ? <small role="status">{workspaceNotice}</small> : null}
              </div>
            </section>

            <section className="p6-transport" aria-label="Playback controls">
              <button
                aria-label={playing ? "Pause score" : "Play score"}
                className="p6-play"
                disabled={!playerReady}
                onClick={() => apiRef.current?.playPause()}
                type="button"
              >
                <PlayIcon playing={playing} />
              </button>
              <div className="p6-time"><strong>{formatTime(positionMs)}</strong><span>/ {formatTime(durationMs)}</span></div>
              <input
                aria-label="Playback position"
                className="p6-progress-input"
                disabled={!playerReady || durationMs <= 0}
                max={Math.max(durationMs, 1)}
                min="0"
                onChange={(event) => {
                  const next = Number(event.target.value);
                  setPositionMs(next);
                  if (apiRef.current) apiRef.current.timePosition = next;
                }}
                step="10"
                type="range"
                value={Math.min(positionMs, Math.max(durationMs, 1))}
              />
              <div className="p6-locator"><span>Measure</span><strong>{String(currentMeasure).padStart(2, "0")} · beat {(currentBeat % beatsPerMeasure + 1).toFixed(1)}</strong></div>
              <div className="p6-tool"><span>Tempo</span><strong>{result.options.effective_tempo_bpm} BPM</strong></div>
              <label className="p6-tool p6-tier-control">
                <span>Difficulty</span>
                <select aria-label="Difficulty tier" onChange={(event) => setTier(event.target.value as DifficultyTierName)} value={tier}>
                  <option value="beginner">Beginner</option>
                  <option value="intermediate">Intermediate</option>
                  <option value="advanced">Advanced</option>
                </select>
              </label>
              <ExportMenu result={displayedResult} />
            </section>

            <section className="p6-stage-grid">
              <article className="p6-score-panel">
                <header>
                  <div><span>Notation + TAB</span><strong>AlphaTab 1.8.4 · {selectedTrial ? "trial checkpoint" : inspectedAlternative ? `candidate ${inspectedAlternative.candidate_index + 1}` : "selected checkpoint"}</strong></div>
                  <div className="p6-page-control">
                    <button aria-label="Zoom out" onClick={() => changeScale(scale - 0.1)} type="button">−</button>
                    <span>{Math.round(scale * 100)}%</span>
                    <button aria-label="Zoom in" onClick={() => changeScale(scale + 0.1)} type="button">+</button>
                  </div>
                </header>
                <div
                  aria-live="polite"
                  className={`p7-score-selection-bar${measureSelectionEnabled ? "" : " is-disabled"}`}
                >
                  <span>Section selection</span>
                  <strong>
                    {measureSelectionEnabled
                      ? `Drag across the score · measures ${revisionSelection.start}–${revisionSelection.end}`
                      : availableSourceFile
                        ? "Return to the selected checkpoint to edit its measures"
                        : "Source bytes are unavailable for this checkpoint"}
                  </strong>
                </div>
                <div className="p6-paper p6-real-paper">
                  {notationResult.tab ? (
                    <AlphaTabScore
                      apiRef={apiRef}
                      measureCount={maxMeasure}
                      measureSelection={revisionSelection}
                      measureSelectionEnabled={measureSelectionEnabled}
                      onMeasureSelectionChange={setRevisionSelection}
                      onPlaying={setPlaying}
                      onPosition={handlePlayerPosition}
                      onReady={setPlayerReady}
                      result={notationResult}
                      scale={scale}
                    />
                  ) : (
                    <div className="p6-empty-score"><strong>No fingering within the bounded search.</strong><span>This result does not claim that no solution exists.</span></div>
                  )}
                </div>
                <footer>
                  <span><i className="p6-source-dot" />Canonical Tab</span>
                  <span><i className="p6-agent-dot" />Playback cursor</span>
                  <strong>{selectedTrial ? "Trial replay is isolated from exports" : "Score, audio and exports share one checkpoint"}</strong>
                </footer>
              </article>

              <aside className="p6-side-rail">
                <article className="p6-fret-card">
                  <header>
                    <div><span>Live fretboard</span><strong>{selectedTrial ? `Trial ${selectedTrial.step.iteration ?? "—"}` : inspectedAlternative ? `Candidate ${inspectedAlternative.candidate_index + 1}` : "Selected checkpoint"}</strong></div>
                    <small>left hand · player view</small>
                  </header>
                  <Fretboard activeNotes={activeNotes} tab={inspectionTab} trial={selectedTrial} />
                  <div className="p6-fret-legend"><span><i />Pressed now</span>{selectedTrial && !selectedTrial.accepted ? <span><i />Rejected addition</span> : null}</div>
                </article>
                <EvidenceCard difficulty={difficulty} result={displayedResult} selectedTrial={selectedTrial} />
                <ResultProvenance result={result} />
              </aside>
            </section>

            <AlternativePanel
              inspectedCandidate={inspectedCandidate}
              onInspect={inspectCandidate}
              result={result}
            />
            <section className="p7-edit-grid" aria-label="Arrangement editing tools">
              <RevisionPanel
                maxMeasure={maxMeasure}
                measureSelection={revisionSelection}
                onMeasureSelectionChange={setRevisionSelection}
                onOutcome={handleRevision}
                result={result}
                sourceFile={availableSourceFile}
              />
              <FingeringEditor onEdit={handleFingerEdit} result={result} />
            </section>
            <FeedbackPanel
              feedback={feedback}
              onClear={() => setFeedback(clearFeedback())}
              onSave={recordRating}
            />
            <TracePanel onSelect={selectTrial} result={result} selectedTrial={selectedTrial} trials={trials} />
            <SourceEvidence result={result} />
          </>
        )}
      </main>

      <footer className="p6-footer">
        <span>{result.source.filename} · {result.source.raw_sha256.slice(0, 12)}…</span>
        <p>GREEN means “inside this versioned model,” not “guaranteed for every guitarist.”</p>
        <span>
          {result.stamps.oracle_checker_version} · {result.options.profile.version} ·{" "}
          {capabilities.profile_registry_version}
        </span>
      </footer>
    </div>
  );
}
