import type { Job, MatchScore } from '../types';

interface Props {
  jobs: Job[];
  scores: Map<string, MatchScore>;
  onSelect: (j: Job) => void;
}

const PICK_LIMIT = 8;
const PICK_MIN_SCORE = 55;
/** LLM 结果达到这个数量后，清单只采信 LLM 分数（两套刻度不可混排）。 */
const LLM_AUTHORITATIVE_MIN = 5;

interface PickRow {
  job: Job;
  score: MatchScore;
}

/**
 * 优先投递清单：把岗位流压缩成「今天该投哪几个」。
 *
 * 分数来源策略：规则分（heuristic-v1）与 LLM 分是两套不可比的刻度——规则分普遍虚高
 * （关键词命中即高分），LLM 分更严格且带语义判断。一旦 LLM 产出足够样本，清单只采信
 * LLM 分数，避免规则分的高分岗位挤占真正值得投的位置。
 */
export function TopPicks({ jobs, scores, onSelect }: Props) {
  const llmRows: PickRow[] = [];
  const ruleRows: PickRow[] = [];
  for (const j of jobs) {
    const s = scores.get(j.url);
    if (!s) continue;
    const row = { job: j, score: s };
    if (s.model.startsWith('heuristic')) ruleRows.push(row);
    else llmRows.push(row);
  }

  const useLlm = llmRows.length >= LLM_AUTHORITATIVE_MIN;
  const picks = (useLlm ? llmRows : ruleRows)
    .filter((p) => p.score.matchScore >= PICK_MIN_SCORE)
    .sort((a, b) => b.score.matchScore - a.score.matchScore);
  const top = picks.slice(0, PICK_LIMIT);

  if (top.length === 0) return null;

  return (
    <div className="card full picks">
      <h2>
        优先投递清单
        <span className="hint">
          Top {top.length} · 阈值 {PICK_MIN_SCORE} 分 ·{' '}
          {useLlm
            ? `LLM 深度分析（已覆盖 ${llmRows.length} 条）`
            : '规则匹配（离线降级，LLM 就绪后自动替换）'}
        </span>
      </h2>
      {useLlm && ruleRows.length > 0 && (
        <p className="rule-note">
          另有 {ruleRows.length} 条仅有规则分，未参与本次排序——规则分与 LLM 分刻度不同，
          混排会让关键词命中的岗位虚高挤占位置。LLM 分析逐步覆盖后会自动纳入。
        </p>
      )}
      <ol className="pick-list">
        {top.map(({ job, score }, i) => (
          <li key={job.url}>
            <button type="button" className="pick" onClick={() => onSelect(job)}>
              <span className="pick-rank">{i + 1}</span>
              <span className="pick-body">
                <span className="pick-title">{job.title}</span>
                <span className="pick-meta">
                  {job.company} · {job.city || '—'} ·{' '}
                  {job.salaryMax > 0 ? `${job.salaryMin}-${job.salaryMax}K` : '面议'} ·{' '}
                  {job.postedDate}
                </span>
                <span className="pick-advice">{score.advice}</span>
              </span>
              <span className={`pick-score s${Math.floor(score.matchScore / 10)}`}>
                {score.matchScore}
              </span>
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}
