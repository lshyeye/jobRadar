/**
 * JobRadar 类型定义 — 全局唯一收口
 * 所有跨模块共享的类型/接口集中定义于此，禁止在函数内内联定义。
 */

/* ---------------- pipeline 产出（stats.json） ---------------- */

export type JobType = '前端' | '全栈' | 'Agent应用开发' | '其他';
export type CompanyGroup = 'BAT' | 'TMD' | '大外企' | '国企' | '其他互联网';

export interface Job {
  company: string;
  group: CompanyGroup;
  title: string;
  type: JobType;
  city: string;
  salaryMin: number;
  salaryMax: number;
  posted: string;
  postedDate: string;
  req: string[];
  url: string;
  source: string;
}

export interface RecentFilter {
  window_days: number;
  drop_undated: boolean;
  kept: number;
  dropped_old: number;
  dropped_undated: number;
}

export interface StatsMeta {
  period_start: string;
  period_end: string;
  last_week_start: string;
  last_week_end: string;
  generated_at: string;
  source: string;
  scenario: string;
  recentFilter: RecentFilter;
}

export interface TrendItem {
  last: number;
  this: number;
  delta: number;
  pct: number;
}

export interface StatsData {
  meta: StatsMeta;
  jobs: Job[];
  byType: Record<JobType, number>;
  byGroup: Partial<Record<CompanyGroup, number>>;
  trend: Record<JobType, TrendItem>;
  lastWeek?: Record<JobType, number>;
}

/* ---------------- LLM 分析产出（match_scores.json，v1.1 接入） ---------------- */

export interface MatchScore {
  jobId: string;
  matchScore: number;
  hardReqs: string[];
  bonusReqs: string[];
  matchedSkills: string[];
  missingSkills: string[];
  advice: string;
  analyzedAt: string;
  model: string;
}

/* ---------------- 看板 UI 层 ---------------- */

export type TypeFilter = JobType | '全部';
export type GroupFilter = CompanyGroup | '全部';
export type CityFilter = string;

export interface JobFilters {
  type: TypeFilter;
  group: GroupFilter;
  city: CityFilter;
  keyword: string;
  onlyAnalyzed: boolean;
}

export type SortKey = 'matchScore' | 'postedDate';

/* ---------------- 3D 岗位星系（JobGalaxy） ---------------- */

export type GalaxyTypeClass = JobType;

export interface GalaxyPoint {
  job: Job;
  position: readonly [number, number, number];
  color: readonly [number, number, number];
  size: number;
}

export interface GalaxyHoverInfo {
  job: Job;
  x: number;
  y: number;
}

export interface DashboardState {
  data: StatsData | null;
  scores: Map<string, MatchScore>;
  filters: JobFilters;
  sortKey: SortKey;
  selectedJob: Job | null;
  loading: boolean;
  error: string | null;
}

/* ---------------- Agent 运行轨迹（agent_runs.json，agent/trace.py 产出） ---------------- */

export type AgentStepKind = 'tool' | 'final';
export type AgentStepStatus = 'ok' | 'error';
export type AgentRunStatus = 'ok' | 'degraded' | 'max_steps';
export type AgentPlanner = 'offline' | 'llm';

export interface AgentStep {
  index: number;
  kind: AgentStepKind;
  name: string;
  args: Record<string, unknown>;
  status: AgentStepStatus;
  latency_ms: number;
  summary: string;
  result_count: number;
  error: string | null;
}

export interface AgentRunRecord {
  run_id: string;
  question: string;
  started_at: string;
  duration_ms: number;
  status: AgentRunStatus;
  planner: AgentPlanner;
  answer: string;
  steps: AgentStep[];
  usage: Record<string, number>;
}

export interface AgentToolStat {
  name: string;
  calls: number;
  errors: number;
  avgLatencyMs: number;
}

/** 聚合指标由后端算好，前端只展示，不在浏览器里重算统计。 */
export interface AgentAggregate {
  totalRuns: number;
  okRuns: number;
  degradedRuns: number;
  toolCalls: number;
  toolErrors: number;
  toolSuccessRate: number;
  avgDurationMs: number;
  p50StepLatencyMs: number;
  p95StepLatencyMs: number;
  totalTokens: number;
  byTool: AgentToolStat[];
}

export interface AgentRunsMeta {
  generatedAt: string;
  maxRuns: number;
  schemaVersion: number;
}

export interface AgentRunsData {
  meta: AgentRunsMeta;
  runs: AgentRunRecord[];
  aggregate: AgentAggregate;
}
