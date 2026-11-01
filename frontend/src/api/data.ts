/**
 * 数据获取层 — fetch pipeline 产出的 JSON。
 * 加时间戳参数防缓存，保证定时任务更新 data/*.json 后页面即时拉到最新。
 */
import type { AgentRunsData, MatchScore, StatsData } from '../types';

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`${path}?t=${Date.now()}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`加载 ${path} 失败: HTTP ${res.status}`);
  return res.json() as Promise<T>;
}

export function loadStats(): Promise<StatsData> {
  return fetchJson<StatsData>('./data/stats.json');
}

/**
 * 匹配度数据为可选（v1.1 才有产出），缺失时安静降级为空 Map。
 */
export async function loadScores(): Promise<Map<string, MatchScore>> {
  try {
    const list = await fetchJson<MatchScore[]>('./data/match_scores.json');
    return new Map(list.map((s) => [s.jobId, s]));
  } catch {
    return new Map();
  }
}

/**
 * Agent 运行轨迹为可选数据：没有跑过 Agent 时安静返回 null，
 * 面板整体不渲染，而不是显示一个空壳。
 */
export async function loadAgentRuns(): Promise<AgentRunsData | null> {
  try {
    return await fetchJson<AgentRunsData>('./data/agent_runs.json');
  } catch {
    return null;
  }
}
