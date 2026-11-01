import type { StatsData, TrendItem } from '../types';

/** KPI 卡片区：周新增 JD 总数 + 三类核心岗位数量与环比。 */
export function KpiCards({ data }: { data: StatsData }) {
  const total = data.jobs.length;
  const trend = data.trend;
  const cards: Array<{ k: string; v: number; t: TrendItem | null }> = [
    { k: '本周新增 JD 总数', v: total, t: null },
    {
      k: '前端',
      v: data.byType['前端'] ?? 0,
      t: trend['前端'] ?? null,
    },
    {
      k: '全栈',
      v: data.byType['全栈'] ?? 0,
      t: trend['全栈'] ?? null,
    },
    {
      k: 'Agent 应用开发',
      v: data.byType['Agent应用开发'] ?? 0,
      t: trend['Agent应用开发'] ?? null,
    },
  ];

  return (
    <section className="kpis">
      {cards.map((c) => (
        <div className="kpi" key={c.k}>
          <div className="k">{c.k}</div>
          <div className="v">{c.v}</div>
          {c.t ? (
            <div className={`d ${c.t.pct >= 0 ? 'up' : 'down'}`}>
              环比 {c.t.pct >= 0 ? '+' : ''}
              {c.t.pct}%（上周 {c.t.last}）
            </div>
          ) : (
            <div className="d">近 {data.meta.recentFilter.window_days} 天累计</div>
          )}
        </div>
      ))}
    </section>
  );
}
