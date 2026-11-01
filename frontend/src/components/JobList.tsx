import { useState } from 'react';
import type {
  CompanyGroup,
  Job,
  JobFilters,
  JobType,
  MatchScore,
  SortKey,
  TypeFilter,
} from '../types';

const TYPE_OPTIONS: TypeFilter[] = ['全部', '前端', '全栈', 'Agent应用开发', '其他'];
const GROUP_OPTIONS: Array<CompanyGroup | '全部'> = [
  '全部',
  'BAT',
  'TMD',
  '大外企',
  '国企',
  '其他互联网',
];
const PAGE_SIZE = 60;

const TYPE_CLASS: Record<JobType, string> = {
  前端: 't-front',
  全栈: 't-full',
  Agent应用开发: 't-agent',
  其他: 't-other',
};

interface Props {
  jobs: Job[];
  scores: Map<string, MatchScore>;
  filters: JobFilters;
  sortKey: SortKey;
  total: number;
  onFiltersChange: (f: JobFilters) => void;
  onSortChange: (s: SortKey) => void;
  onSelect: (j: Job) => void;
}

/** 岗位列表：筛选 + 搜索 + 排序 + 分页渲染。 */
export function JobList({
  jobs,
  scores,
  filters,
  sortKey,
  total,
  onFiltersChange,
  onSortChange,
  onSelect,
}: Props) {
  const [limit, setLimit] = useState(PAGE_SIZE);
  const cities = buildCities(jobs);

  const set = <K extends keyof JobFilters>(key: K, val: JobFilters[K]) =>
    onFiltersChange({ ...filters, [key]: val });

  return (
    <div className="card full">
      <h2>岗位列表（筛选后 {jobs.length} / {total}）</h2>
      <div className="toolbar">
        <select
          value={filters.type}
          onChange={(e) => set('type', e.target.value as TypeFilter)}
        >
          {TYPE_OPTIONS.map((t) => (
            <option key={t} value={t}>
              类型：{t}
            </option>
          ))}
        </select>
        <select
          value={filters.group}
          onChange={(e) => set('group', e.target.value as CompanyGroup | '全部')}
        >
          {GROUP_OPTIONS.map((g) => (
            <option key={g} value={g}>
              集团：{g}
            </option>
          ))}
        </select>
        <select value={filters.city} onChange={(e) => set('city', e.target.value)}>
          <option value="全部">城市：全部</option>
          {cities.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <input
          className="search"
          placeholder="搜索标题 / 公司 / 技能…"
          value={filters.keyword}
          onChange={(e) => set('keyword', e.target.value)}
        />
        <select value={sortKey} onChange={(e) => onSortChange(e.target.value as SortKey)}>
          <option value="postedDate">按发布日期</option>
          <option value="matchScore">按匹配度</option>
        </select>
        <label className="check">
          <input
            type="checkbox"
            checked={filters.onlyAnalyzed}
            onChange={(e) => set('onlyAnalyzed', e.target.checked)}
          />
          仅看已分析
        </label>
      </div>

      <div className="jobs">
        {jobs.slice(0, limit).map((j) => {
          const sc = scores.get(j.url);
          return (
            <button
              type="button"
              className={`job ${TYPE_CLASS[j.type]}`}
              key={j.url}
              onClick={() => onSelect(j)}
            >
              <div className="row1">
                <span className={`chip ${TYPE_CLASS[j.type]}`}>{j.type}</span>
                {sc && <span className="score">{sc.matchScore}</span>}
                <span className="date">{j.postedDate}</span>
              </div>
              <div className="row2">{j.title}</div>
              <div className="row3">
                <span className="co">{j.company}</span>
                <span className="city">{j.city || '—'}</span>
                <span className="salary">
                  {j.salaryMax > 0 ? `${j.salaryMin}-${j.salaryMax}K` : '面议'}
                </span>
              </div>
              {j.req.length > 0 && (
                <div className="row4">
                  {j.req.slice(0, 6).map((r) => (
                    <span className="tag" key={r}>
                      {r}
                    </span>
                  ))}
                </div>
              )}
            </button>
          );
        })}
      </div>

      {jobs.length > limit && (
        <button type="button" className="btn" onClick={() => setLimit(limit + PAGE_SIZE)}>
          加载更多（剩 {jobs.length - limit} 条）
        </button>
      )}
      {jobs.length === 0 && <p className="empty">当前筛选无匹配岗位</p>}
    </div>
  );
}

function buildCities(jobs: Job[]): string[] {
  const set = new Set<string>();
  for (const j of jobs) {
    if (j.city) set.add(j.city);
  }
  return [...set].sort();
}
