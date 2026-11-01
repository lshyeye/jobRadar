import type { StatsData } from '../types';

const TYPE_ORDER = ['前端', '全栈', 'Agent应用开发'] as const;

/** 三类核心岗位的本周 vs 上周对比条形。 */
export function TypeTrend({ data }: { data: StatsData }) {
  const max = Math.max(
    ...TYPE_ORDER.map((t) => Math.max(data.trend[t]?.this ?? 0, data.trend[t]?.last ?? 0)),
    1,
  );
  return (
    <div className="card">
      <h2>职位类型 · 本周 vs 上周</h2>
      <p className="hint">蓝为本周，灰为上周</p>
      {TYPE_ORDER.map((t) => {
        const item = data.trend[t];
        if (!item) return null;
        return (
          <div className="bar" key={t}>
            <span className="lab">{t}</span>
            <div className="track">
              <div
                className="fill"
                style={{ width: `${(item.this / max) * 100}%` }}
              />
              <div
                className="fill last"
                style={{ width: `${(item.last / max) * 100}%` }}
              />
            </div>
            <span className="val">
              {item.this}
              <em className={item.pct >= 0 ? 'up' : 'down'}>
                ({item.pct >= 0 ? '+' : ''}
                {item.pct}%)
              </em>
            </span>
          </div>
        );
      })}
    </div>
  );
}
