import { useEffect, useMemo, useState } from 'react';
import type {
  AgentRunsData,
  DashboardState,
  Job,
  JobFilters,
  MatchScore,
  SortKey,
  StatsData,
} from './types';
import { loadAgentRuns, loadScores, loadStats } from './api/data';
import { JobGalaxy } from './components/JobGalaxy';
import { KpiCards } from './components/KpiCards';
import { TypeTrend } from './components/TypeTrend';
import { GroupBars } from './components/GroupBars';
import { JobList } from './components/JobList';
import { JobDrawer } from './components/JobDrawer';
import { TopPicks } from './components/TopPicks';
import { AgentPanel } from './components/AgentPanel';

const DEFAULT_FILTERS: JobFilters = {
  type: '全部',
  group: '全部',
  city: '全部',
  keyword: '',
  onlyAnalyzed: false,
};

export default function App() {
  const [data, setData] = useState<StatsData | null>(null);
  const [scores, setScores] = useState<Map<string, MatchScore>>(new Map());
  const [filters, setFilters] = useState<JobFilters>(DEFAULT_FILTERS);
  const [sortKey, setSortKey] = useState<SortKey>('postedDate');
  const [selected, setSelected] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [agentRuns, setAgentRuns] = useState<AgentRunsData | null>(null);

  useEffect(() => {
    loadStats()
      .then((d) => setData(d))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
    loadScores().then(setScores).catch(() => setScores(new Map()));
    loadAgentRuns().then(setAgentRuns).catch(() => setAgentRuns(null));
  }, []);

  const llmCount = useMemo(
    () => [...scores.values()].filter((s) => !s.model.startsWith('heuristic')).length,
    [scores],
  );

  const filteredJobs = useMemo(() => {
    if (!data) return [] as Job[];
    const kw = filters.keyword.trim().toLowerCase();
    let list = data.jobs.filter((j) => {
      if (filters.type !== '全部' && j.type !== filters.type) return false;
      if (filters.group !== '全部' && j.group !== filters.group) return false;
      if (filters.city !== '全部' && !j.city.includes(filters.city)) return false;
      if (filters.onlyAnalyzed && !scores.has(j.url)) return false;
      if (kw) {
        const hay = `${j.title} ${j.company} ${j.city} ${j.req.join(' ')}`.toLowerCase();
        if (!hay.includes(kw)) return false;
      }
      return true;
    });
    list = [...list].sort((a, b) => {
      if (sortKey === 'matchScore') {
        const sa = scores.get(a.url)?.matchScore ?? -1;
        const sb = scores.get(b.url)?.matchScore ?? -1;
        if (sa !== sb) return sb - sa;
      }
      return b.postedDate.localeCompare(a.postedDate);
    });
    return list;
  }, [data, filters, scores, sortKey]);

  if (error) {
    return <div className="page-error">数据加载失败：{error}</div>;
  }
  if (!data) {
    return <div className="page-loading">加载中…</div>;
  }

  const meta = data.meta;
  const period = `${meta.period_start} ~ ${meta.period_end}`;

  return (
    <div className="wrap">
      <header className="top">
        <div className="title">
          <h1>JobRadar · 岗位情报与匹配度看板</h1>
          <p>
            {meta.scenario} · 统计周期 {period} · 数据生成于 {meta.generated_at}
            （共 {data.jobs.length} 条，近 {meta.recentFilter.window_days} 天）
          </p>
        </div>
        <div className="badges">
          <span className="badge">源: {meta.source}</span>
          <span className="badge g">
            LLM 已分析 {llmCount} 条{llmCount < scores.size ? ` · 规则 ${scores.size - llmCount}` : ''}
          </span>
          <span
            className="badge y"
            role="button"
            tabIndex={0}
            onClick={() => window.location.reload()}
            onKeyDown={(e) => e.key === 'Enter' && window.location.reload()}
          >
            刷新最新数据
          </span>
        </div>
      </header>

      <JobGalaxy jobs={data.jobs} scores={scores} onSelect={setSelected} />

      <KpiCards data={data} />
      <TopPicks jobs={data.jobs} scores={scores} onSelect={setSelected} />
      <AgentPanel data={agentRuns} />
      <div className="grid">
        <TypeTrend data={data} />
        <GroupBars data={data} />
      </div>

      <JobList
        jobs={filteredJobs}
        scores={scores}
        filters={filters}
        sortKey={sortKey}
        total={data.jobs.length}
        onFiltersChange={setFilters}
        onSortChange={setSortKey}
        onSelect={setSelected}
      />

      {selected && (
        <JobDrawer
          job={selected}
          score={scores.get(selected.url) ?? null}
          onClose={() => setSelected(null)}
        />
      )}

      <footer className="footer">
        数据由采集管线自动更新 · React + TypeScript + Vite · 演示数据已脱敏
      </footer>
    </div>
  );
}

export type { DashboardState };
