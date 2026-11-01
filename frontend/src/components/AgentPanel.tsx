import { useMemo, useState } from 'react';
import type { AgentRunRecord, AgentRunsData, AgentStep } from '../types';

interface Props {
  data: AgentRunsData | null;
}

/**
 * Agent 运行监控面板。
 *
 * 为什么一个「求职工具」要有监控面板：Agent 的输出不可复现、不可解释，
 * 唯一能做的是把**每一次运行的每一步**摊开——调了哪个工具、花了多久、成功没有。
 * 没有这层观测，Agent 在面试和在生产里都是黑盒。
 *
 * 数据全部来自后端算好的 agent_runs.json，前端不做二次统计。
 */
export function AgentPanel({ data }: Props) {
  const [openId, setOpenId] = useState<string | null>(null);

  const maxDuration = useMemo(
    () => Math.max(1, ...(data?.runs.map((r) => r.duration_ms) ?? [0])),
    [data],
  );
  const maxToolCalls = useMemo(
    () => Math.max(1, ...(data?.aggregate.byTool.map((t) => t.calls) ?? [0])),
    [data],
  );

  if (!data || data.runs.length === 0) return null;
  const { aggregate: agg, runs, meta } = data;

  return (
    <div className="card full agent-panel">
      <h2>
        Agent 运行监控
        <span className="hint">
          最近 {runs.length} 次运行 · 每次运行的每一步工具调用都可追溯 · 更新于 {meta.generatedAt}
        </span>
      </h2>

      <div className="agent-kpis">
        <Kpi label="运行次数" value={String(agg.totalRuns)} />
        <Kpi
          label="工具成功率"
          value={`${(agg.toolSuccessRate * 100).toFixed(0)}%`}
          sub={`${agg.toolCalls} 次调用 / ${agg.toolErrors} 次失败`}
        />
        <Kpi label="平均时长" value={`${agg.avgDurationMs.toFixed(1)} ms`} />
        <Kpi
          label="步耗时 P50 / P95"
          value={`${agg.p50StepLatencyMs.toFixed(1)} / ${agg.p95StepLatencyMs.toFixed(1)} ms`}
        />
        <Kpi
          label="Token 消耗"
          value={agg.totalTokens > 0 ? String(agg.totalTokens) : '—'}
          sub={agg.totalTokens > 0 ? '模型通道' : '离线规划器不消耗 token'}
        />
      </div>

      <section>
        <h5>工具调用分布</h5>
        <div className="agent-tools">
          {agg.byTool.map((tool) => (
            <div className="agent-tool" key={tool.name}>
              <span className="agent-tool-name">{tool.name}</span>
              <span className="agent-tool-bar">
                <i style={{ width: `${(tool.calls / maxToolCalls) * 100}%` }} />
              </span>
              <span className="agent-tool-num">
                {tool.calls} 次 · {tool.avgLatencyMs.toFixed(1)} ms
                {tool.errors > 0 ? ` · ${tool.errors} 失败` : ''}
              </span>
            </div>
          ))}
        </div>
      </section>

      <section>
        <h5>运行记录（点开看每一步）</h5>
        <ol className="agent-runs">
          {runs.map((run) => {
            const open = openId === run.run_id;
            return (
              <li key={run.run_id}>
                <button
                  type="button"
                  className={`agent-run ${open ? 'open' : ''}`}
                  onClick={() => setOpenId(open ? null : run.run_id)}
                  aria-expanded={open}
                >
                  <span className={`agent-dot ${run.status === 'ok' ? 'ok' : 'warn'}`} aria-hidden="true" />
                  <span className="agent-q">{run.question}</span>
                  <span className={`agent-plan ${run.planner}`}>{run.planner === 'llm' ? 'LLM' : '离线'}</span>
                  <span className="agent-dur">
                    {run.duration_ms.toFixed(1)} ms
                    <i style={{ width: `${(run.duration_ms / maxDuration) * 100}%` }} />
                  </span>
                  <span className="agent-steps-count">{run.steps.length} 步</span>
                </button>
                {open && <RunDetail run={run} />}
              </li>
            );
          })}
        </ol>
      </section>
    </div>
  );
}

function Kpi({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="agent-kpi">
      <div className="agent-kpi-label">{label}</div>
      <div className="agent-kpi-value">{value}</div>
      {sub && <div className="agent-kpi-sub">{sub}</div>}
    </div>
  );
}

function RunDetail({ run }: { run: AgentRunRecord }) {
  const maxLatency = Math.max(1, ...run.steps.map((s) => s.latency_ms));
  return (
    <div className="agent-detail">
      <p className="agent-answer">{run.answer}</p>
      <table className="agent-steps">
        <thead>
          <tr>
            <th>#</th>
            <th>步骤</th>
            <th>结果</th>
            <th>耗时（瀑布）</th>
          </tr>
        </thead>
        <tbody>
          {run.steps.map((step) => (
            <StepRow key={`${run.run_id}-${step.index}`} step={step} maxLatency={maxLatency} />
          ))}
        </tbody>
      </table>
      <p className="agent-meta">
        {run.started_at} · {run.run_id} · 状态 {run.status}
        {run.usage?.totalTokens ? ` · ${run.usage.totalTokens} tokens` : ''}
      </p>
    </div>
  );
}

function StepRow({ step, maxLatency }: { step: AgentStep; maxLatency: number }) {
  const args = Object.entries(step.args ?? {})
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => `${k}=${String(v)}`)
    .join(' ');
  return (
    <tr className={step.status === 'ok' ? '' : 'bad'}>
      <td>{step.index}</td>
      <td>
        <code>{step.name}</code>
        {args && <span className="agent-args">{args}</span>}
      </td>
      <td>
        {step.error ? <span className="miss">{step.error}</span> : step.summary}
      </td>
      <td>
        <span className="agent-waterfall">
          <i style={{ width: `${(step.latency_ms / maxLatency) * 100}%` }} />
        </span>
        <span className="agent-lat">{step.latency_ms.toFixed(1)} ms</span>
      </td>
    </tr>
  );
}
