"use client";

import { useEffect, useMemo, useRef, useState } from "react";

const defaultPayload = {
  constraints: {
    type: "summarize",
    min_accuracy: 0.85,
    max_latency_ms: 3000,
    priority: "balanced",
    cost_limit: 0.02,
    carbon_budget_remaining_g: 10,
    delay_tolerant: false,
  },
  steps: [
    { name: "Ingest request", type: "summarize", input: "Summarize the latest carbon report." },
    { name: "Analyze emissions", type: "analyze", input: "Find emission drivers and hotspots." },
    { name: "Generate response", type: "generate", input: "Create a concise recommendation." },
  ],
};

type WorkflowState = {
  workflow_id: string;
  status: string;
  steps: Array<any>;
  constraints?: any;
  carbon_budget?: any;
};

export default function Home() {
  const [health, setHealth] = useState<any>(null);
  const [runtime, setRuntime] = useState<any>(null);
  const [workflow, setWorkflow] = useState<WorkflowState | null>(null);
  const [currentStep, setCurrentStep] = useState<any>(null);
  const [decision, setDecision] = useState<any>(null);
  const [passport, setPassport] = useState<any>(null);
  const [comparison, setComparison] = useState<any>(null);
  const [whatIf, setWhatIf] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [priority, setPriority] = useState("balanced");
  const [carbonBudget, setCarbonBudget] = useState(10);
  const [maxLatency, setMaxLatency] = useState(3000);
  const [delayTolerant, setDelayTolerant] = useState(false);
  const initializedRef = useRef(false);

  const api = async (path: string, options: RequestInit = {}) => {
    const response = await fetch(path, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload?.detail || payload?.error || "Request failed");
    }
    return payload;
  };

  const fetchRuntime = async () => {
    try {
      const data = await api("/api/runtime/config");
      setRuntime(data);
    } catch {
      setRuntime({ mock_mode: true, live_mode_ready: false, deployment_url: "local-demo" });
    }
  };

  const fetchHealth = async () => {
    try {
      const data = await api("/api/health");
      setHealth(data);
    } catch {
      setHealth({ status: "booting", service: "hackdays-greenpilot-backend" });
    }
  };

  const reloadDerived = async (workflowId: string) => {
    const [passportData, comparisonData, whatIfData] = await Promise.all([
      api(`/api/workflows/${workflowId}/passport`).catch(() => null),
      api(`/api/workflows/${workflowId}/dashboard/static-vs-dynamic`).catch(() => null),
      api(`/api/workflows/${workflowId}/what-if`).catch(() => null),
    ]);
    setPassport(passportData);
    setComparison(comparisonData);
    setWhatIf(whatIfData);
  };

  const buildWorkflow = async () => {
    setLoading(true);
    setError(null);
    try {
      const payload = await api("/api/workflows", {
        method: "POST",
        body: JSON.stringify({
          constraints: {
            type: "summarize",
            min_accuracy: 0.85,
            max_latency_ms: maxLatency,
            priority,
            cost_limit: 0.02,
            carbon_budget_remaining_g: carbonBudget,
            delay_tolerant: delayTolerant,
          },
          steps: defaultPayload.steps,
        }),
      });
      setWorkflow(payload.workflow);
      setCurrentStep(payload.current_step);
      setDecision(payload.decision);
      await reloadDerived(payload.workflow.workflow_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  };

  const executeNext = async () => {
    if (!workflow) return;
    const data = await api(`/api/workflows/${workflow.workflow_id}/execute-next`, { method: "POST" });
    setWorkflow(data.workflow);
    setCurrentStep(data.next_step || null);
    setDecision(data.next_decision || data.run || null);
    await reloadDerived(workflow.workflow_id);
  };

  const executeAll = async () => {
    if (!workflow) return;
    const data = await api(`/api/workflows/${workflow.workflow_id}/execute-all`, { method: "POST" });
    setWorkflow(data.workflow);
    setCurrentStep(null);
    await reloadDerived(workflow.workflow_id);
  };

  const triggerBudgetDrop = async () => {
    if (!workflow) return;
    await api(`/api/workflows/${workflow.workflow_id}/runtime/budget`, {
      method: "POST",
      body: JSON.stringify({ remaining_carbon_g: 0.5 }),
    });
    await reloadDerived(workflow.workflow_id);
  };

  const triggerEnvironment = async () => {
    if (!workflow) return;
    const data = await api(`/api/workflows/${workflow.workflow_id}/runtime/environment`, { method: "POST", body: JSON.stringify({ carbon_multiplier: 2, reason: "dashboard_spike" }) });
    setWorkflow(data.workflow);
    setDecision(data.decision);
    await reloadDerived(workflow.workflow_id);
  };

  const temporalPlan = async () => {
    if (!workflow) return;
    const data = await api(`/api/workflows/${workflow.workflow_id}/temporal-plan`);
    setPassport((prev: any) => ({ ...prev, temporal_plan: data.plan }));
  };

  const bootstrapDashboard = async (attempt = 0) => {
    await fetchHealth();
    await fetchRuntime();

    if (initializedRef.current) {
      return;
    }

    try {
      await buildWorkflow();
      initializedRef.current = true;
    } catch {
      if (attempt < 6) {
        setTimeout(() => {
          void bootstrapDashboard(attempt + 1);
        }, 1000 * (attempt + 1));
        return;
      }

      initializedRef.current = true;
      setError("Backend did not become ready in time. The dashboard is in demo-safe mode.");
    }
  };

  useEffect(() => {
    void bootstrapDashboard();
  }, []);

  const summary = useMemo(() => {
    const budget = workflow?.carbon_budget;
    const remaining = budget?.remaining_carbon_g ?? 0;
    const score = decision?.score ?? 0;
    return {
      workflowStatus: workflow?.status || "not started",
      remainingCarbon: remaining,
      decisionScore: score,
      replanCount: (workflow?.steps || []).filter((step: any) => step.status === "done").length,
    };
  }, [workflow, decision]);

  const candidateRows = decision?.feasible_candidates || [];
  const rejectedRows = decision?.rejected_candidates || [];

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">GP</div>
          <div>
            <h1>GreenPilot</h1>
            <p>Runtime scheduler</p>
          </div>
        </div>

        <nav className="nav-list" aria-label="Dashboard sections">
          <a href="#workflow" className="nav-link active">Workflow</a>
          <a href="#decision" className="nav-link">Decision</a>
          <a href="#candidates" className="nav-link">Candidates</a>
          <a href="#what-if" className="nav-link">What-if</a>
          <a href="#passport" className="nav-link">Passport</a>
          <a href="#comparison" className="nav-link">Static vs Dynamic</a>
        </nav>

        <section className="runtime-strip" aria-label="Runtime configuration">
          <span className={`status-dot ${runtime?.live_mode_ready ? "live" : ""}`} />
          <div>
            <strong>{runtime?.mock_mode ? "Mock Mode" : "Live Mode"}</strong>
            <span>{runtime?.live_mode_ready ? "Live-ready" : "Demo-safe"}</span>
          </div>
        </section>
      </aside>

      <div className="main">
        <section className="toolbar" aria-label="Workflow controls">
          <div className="field-grid">
            <label>
              Priority
              <select value={priority} onChange={(e) => setPriority(e.target.value)}>
                <option value="balanced">Balanced</option>
                <option value="fast">Fast</option>
                <option value="green">Green</option>
                <option value="quality">Quality</option>
              </select>
            </label>
            <label>
              Carbon budget
              <input type="number" min="0" step="0.1" value={carbonBudget} onChange={(e) => setCarbonBudget(Number(e.target.value))} />
            </label>
            <label>
              Max latency
              <input type="number" min="1" step="100" value={maxLatency} onChange={(e) => setMaxLatency(Number(e.target.value))} />
            </label>
            <label className="toggle-row">
              <input type="checkbox" checked={delayTolerant} onChange={(e) => setDelayTolerant(e.target.checked)} />
              Delay tolerant
            </label>
          </div>

          <div className="actions">
            <button className="button primary" onClick={buildWorkflow} disabled={loading}>Create</button>
            <button className="button" onClick={executeNext}>Run step</button>
            <button className="button" onClick={executeAll}>Run all</button>
            <button className="button danger" onClick={triggerBudgetDrop}>Budget drop</button>
            <button className="button" onClick={triggerEnvironment}>Carbon spike</button>
          </div>
        </section>

        <section className="kpi-grid" aria-label="Summary metrics">
          <article className="metric-panel">
            <span>Workflow</span>
            <strong>{summary.workflowStatus}</strong>
          </article>
          <article className="metric-panel">
            <span>Remaining carbon</span>
            <strong>{summary.remainingCarbon.toFixed(2)} g</strong>
          </article>
          <article className="metric-panel">
            <span>Decision score</span>
            <strong>{summary.decisionScore.toFixed(4)}</strong>
          </article>
          <article className="metric-panel">
            <span>Replans</span>
            <strong>{summary.replanCount}</strong>
          </article>
        </section>

        <section id="workflow" className="section-band">
          <div className="section-heading">
            <h2>Workflow</h2>
            <span>{workflow?.workflow_id || "No workflow"}</span>
          </div>
          <div className="step-track">
            {(workflow?.steps || []).map((step: any) => (
              <article key={step.step_id} className={`step-card ${step.status}`}>
                <strong>{step.name}</strong>
                <span>{step.step_id} / {step.type}</span>
                <span>{step.status}</span>
              </article>
            ))}
          </div>
        </section>

        <section id="decision" className="section-band decision-layout">
          <div>
            <div className="section-heading">
              <h2>Decision</h2>
              <span>{decision?.reason || "Waiting for workflow"}</span>
            </div>
            <div className="decision-summary">
              <div>
                <span>Model</span>
                <strong>{decision?.selected_candidate?.model || "-"}</strong>
              </div>
              <div>
                <span>Location</span>
                <strong>{decision?.selected_candidate?.location || "-"}</strong>
              </div>
              <div>
                <span>Time</span>
                <strong>{decision?.selected_candidate?.time_window || "-"}</strong>
              </div>
            </div>
          </div>

          <div className="balance-box">
            <div className="balance-meter"><div className="balance-fill" style={{ width: `${Math.min(100, (decision?.constraint_results?.balance_metrics?.balance_evenness_score || 0) * 100)}%` }} /></div>
            <dl>
              <div>
                <dt>Dominant</dt>
                <dd>{decision?.constraint_results?.balance_metrics?.dominant_criterion || "-"}</dd>
              </div>
              <div>
                <dt>Compromise</dt>
                <dd>{decision?.constraint_results?.balance_metrics?.largest_compromise || "-"}</dd>
              </div>
              <div>
                <dt>Evenness</dt>
                <dd>{decision?.constraint_results?.balance_metrics?.balance_evenness_score?.toFixed(3) || "-"}</dd>
              </div>
            </dl>
          </div>
        </section>

        <section id="candidates" className="section-band">
          <div className="section-heading">
            <h2>Candidates</h2>
            <span>{candidateRows.length} feasible</span>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Plan</th>
                  <th>Location</th>
                  <th>Time</th>
                  <th>Latency</th>
                  <th>Quality</th>
                  <th>Cost</th>
                  <th>Energy</th>
                  <th>Carbon</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {candidateRows.length > 0 ? candidateRows.map((candidate: any) => (
                  <tr key={candidate.candidate_id}>
                    <td>{candidate.model}</td>
                    <td>{candidate.location}</td>
                    <td>{candidate.time_window}</td>
                    <td>{candidate.latency_ms} ms</td>
                    <td>{candidate.accuracy.toFixed(2)}</td>
                    <td>${candidate.cost_usd.toFixed(4)}</td>
                    <td>{candidate.energy_wh.toFixed(4)} Wh</td>
                    <td>{candidate.carbon_g.toFixed(4)} g</td>
                    <td><span className="tag ok">feasible</span></td>
                  </tr>
                )) : <tr><td colSpan={9}>No candidates yet</td></tr>}
                {rejectedRows.map((row: any) => (
                  <tr key={row.candidate.candidate_id}>
                    <td>{row.candidate.model}</td>
                    <td>{row.candidate.location}</td>
                    <td>{row.candidate.time_window}</td>
                    <td>{row.candidate.latency_ms} ms</td>
                    <td>{row.candidate.accuracy.toFixed(2)}</td>
                    <td>${row.candidate.cost_usd.toFixed(4)}</td>
                    <td>{row.candidate.energy_wh.toFixed(4)} Wh</td>
                    <td>{row.candidate.carbon_g.toFixed(4)} g</td>
                    <td><span className="tag bad">{row.reasons[0]}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section id="what-if" className="section-band">
          <div className="section-heading">
            <h2>What-if</h2>
            <button className="button compact" onClick={() => workflow && reloadDerived(workflow.workflow_id)}>Refresh</button>
          </div>
          <div className="profile-grid">
            {whatIf?.profiles ? Object.entries(whatIf.profiles).map(([key, value]: any) => (
              <article key={key} className="profile-card">
                <strong>{key}</strong>
                <dl>
                  <div><dt>Score</dt><dd>{value.score?.toFixed(4) || '-'}</dd></div>
                  <div><dt>Latency</dt><dd>{value.predicted_metrics?.latency_ms || '-'}</dd></div>
                  <div><dt>Cost</dt><dd>${value.predicted_metrics?.cost_usd?.toFixed(4) || '-'}</dd></div>
                  <div><dt>Energy</dt><dd>{value.predicted_metrics?.energy_wh ? `${value.predicted_metrics.energy_wh.toFixed(4)} Wh` : '-'}</dd></div>
                  <div><dt>Carbon</dt><dd>{value.predicted_metrics?.carbon_g ? `${value.predicted_metrics.carbon_g.toFixed(4)} g` : '-'}</dd></div>
                </dl>
              </article>
            )) : <div className="muted">No what-if data available.</div>}
          </div>
        </section>

        <section id="passport" className="section-band">
          <div className="section-heading">
            <h2>Decision Passport</h2>
            <button className="button compact" onClick={temporalPlan}>Cleaner plan</button>
          </div>
          <div className="passport-grid">
            <div><span>Estimated saving</span><strong>{passport?.estimated_saving?.carbon_g ? `${passport.estimated_saving.carbon_g.toFixed(4)} g` : '-'}</strong></div>
            <div><span>Rejected alternatives</span><strong>{passport?.alternatives?.rejected_count ?? '-'}</strong></div>
            <div><span>Runtime events</span><strong>{passport?.runtime_events?.length ?? 0}</strong></div>
            <div><span>Green window</span><strong>{passport?.temporal_plan ? `${passport.temporal_plan.mode} / ${passport.temporal_plan.wait_minutes} min` : '-'}</strong></div>
          </div>
        </section>

        <section id="comparison" className="section-band">
          <div className="section-heading">
            <h2>Static vs Dynamic</h2>
            <span>{comparison?.quality_check ? `quality: ${comparison.quality_check.dynamic_quality} / ${comparison.quality_check.baseline_quality}` : '-'}</span>
          </div>
          <div className="comparison-grid">
            {comparison ? Object.entries(comparison.absolute_difference || {}).map(([key, value]: any) => (
              <article key={key} className="comparison-item">
                <strong>{key}</strong>
                <span><b>Baseline</b><em>{comparison.baseline_totals?.[key]?.toFixed(4) ?? '-'}</em></span>
                <span><b>Dynamic</b><em>{comparison.dynamic_totals?.[key]?.toFixed(4) ?? '-'}</em></span>
                <div className="bar-track"><div className="bar-fill" style={{ width: `${Math.min(100, Math.abs(Number(value || 0)) * 20)}%` }} /></div>
              </article>
            )) : <div className="muted">No comparison data yet.</div>}
          </div>
        </section>

        {error && <div className="toast show">{error}</div>}
      </div>
    </main>
  );
}

