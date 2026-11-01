import type { CompanyGroup, StatsData } from '../types';

const GROUP_COLORS: Partial<Record<CompanyGroup, string>> = {
  BAT: '#2f6df6',
  TMD: '#11b386',
  大外企: '#8b5cf6',
  国企: '#f5577a',
  其他互联网: '#94a3b8',
};

/** 公司集团分布。 */
export function GroupBars({ data }: { data: StatsData }) {
  const entries = Object.entries(data.byGroup).sort((a, b) => b[1] - a[1]);
  const max = Math.max(...entries.map((e) => e[1]), 1);
  return (
    <div className="card">
      <h2>公司集团分布</h2>
      <p className="hint">本周新增 JD 按公司集团</p>
      {entries.map(([g, n]) => {
        const group = g as CompanyGroup;
        return (
          <div className="bar" key={g}>
            <span className="lab">{g}</span>
            <div className="track">
              <div
                className="fill"
                style={{
                  width: `${(n / max) * 100}%`,
                  background: GROUP_COLORS[group] ?? '#94a3b8',
                }}
              />
            </div>
            <span className="val">{n}</span>
          </div>
        );
      })}
    </div>
  );
}
