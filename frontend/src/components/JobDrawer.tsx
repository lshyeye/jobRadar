import type { Job, MatchScore } from '../types';

interface Props {
  job: Job;
  score: MatchScore | null;
  onClose: () => void;
}

/** 演示数据的占位域名：脱敏时原始链接被换成 jobs.example.com 下的哈希路径。 */
const DEMO_HOST = 'jobs.example.com';

/**
 * 判断是不是脱敏后的占位链接。
 * 占位链接点了会「静默失败」（域名不存在、浏览器连报错页都没有），
 * 所以必须显式降级成不可点状态，而不是留一个骗人的按钮。
 */
function isDemoLink(url: string): boolean {
  try {
    return new URL(url).hostname === DEMO_HOST;
  } catch {
    return true;
  }
}

/** 岗位详情抽屉：JD 摘要 + 匹配度分析（来源不同则文案不同）。 */
export function JobDrawer({ job, score, onClose }: Props) {
  const isRule = score ? score.model.startsWith('heuristic') : false;
  const demoLink = isDemoLink(job.url);
  return (
    <div className="drawer-mask" onClick={onClose} role="presentation">
      <aside className="drawer" onClick={(e) => e.stopPropagation()} role="presentation">
        <header className="drawer-head">
          <h3>{job.title}</h3>
          <button type="button" className="btn" onClick={onClose}>
            关闭
          </button>
        </header>

        <dl className="meta">
          <dt>公司</dt>
          <dd>
            {job.company}（{job.group}）
          </dd>
          <dt>城市</dt>
          <dd>{job.city || '—'}</dd>
          <dt>薪资</dt>
          <dd>{job.salaryMax > 0 ? `${job.salaryMin}-${job.salaryMax}K` : '面议'}</dd>
          <dt>发布</dt>
          <dd>{job.postedDate}</dd>
        </dl>

        {job.req.length > 0 && (
          <section>
            <h4>采集到的技能标签</h4>
            <div className="row4">
              {job.req.map((r) => (
                <span className="tag" key={r}>
                  {r}
                </span>
              ))}
            </div>
          </section>
        )}

        {score ? (
          <section className="analysis">
            <h4>{isRule ? '规则匹配分析（离线）' : 'LLM 匹配度分析'}</h4>
            <div className="score-big">{score.matchScore}</div>
            {isRule && (
              <p className="rule-note">
                由技能词表命中 + 标题角色校准产生，只判断「JD 要的技能你有没有」，
                不解析语义。相对排序可信，绝对值参考意义有限。
              </p>
            )}
            <h5>硬性要求</h5>
            <ul>
              {score.hardReqs.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
            <h5>加分项</h5>
            <ul>
              {score.bonusReqs.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
            <div className="match-grid">
              <div>
                <h5>已匹配</h5>
                <div className="row4">
                  {score.matchedSkills.map((s) => (
                    <span className="tag ok" key={s}>
                      {s}
                    </span>
                  ))}
                </div>
              </div>
              <div>
                <h5>缺口</h5>
                <div className="row4">
                  {score.missingSkills.map((s) => (
                    <span className="tag miss" key={s}>
                      {s}
                    </span>
                  ))}
                </div>
              </div>
            </div>
            <p className="advice">{score.advice}</p>
            <p className="model-note">
              分析模型 {score.model} · {score.analyzedAt}
            </p>
          </section>
        ) : (
          <p className="empty">该岗位暂未分析（非重点方向，或等待下一轮分析）</p>
        )}

        {demoLink ? (
          <div className="link-block">
            <span className="btn primary disabled" aria-disabled="true">
              原始 JD 链接已脱敏
            </span>
            <p className="link-note">
              仓库内是脱敏演示数据，真实岗位链接已替换为占位地址（jobs.example.com），
              点击无目标页。接自己的数据源后即可正常跳转。
            </p>
          </div>
        ) : (
          <a className="btn primary" href={job.url} target="_blank" rel="noreferrer">
            查看原始 JD ↗
          </a>
        )}
      </aside>
    </div>
  );
}
