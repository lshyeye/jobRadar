# pipeline/ · 采集管线

采集器已经收进本仓库（v1.1），**可以离线跑通**：

```bash
python -m pipeline --offline --out data/_offline      # 读 fixtures/，不联网
python -m pipeline --offline --out data/_offline --sync  # 顺带覆盖看板数据
```

913 条岗位跑完整条链约 100–170 ms。`--offline` 时打分走规则通道；
配了 `LLM_API_KEY` 且不加 `--offline` 就走 LLM 通道，失败自动降级（见 `llm_match.py`）。

---

## 链路

```
config/sources.json ──► collect ──► 归一化 ──► 近期过滤(92d) ──► classify
                                                                   │
                                              jd_store（正文按 URL 累积）◄──┘
                                                                   │
                                    llm_match / heuristic_match ◄──┘
                                                                   │
                                              stats.json + match_scores.json
```

| 阶段 | 模块 | 职责 | 已知局限 |
|------|------|------|---------|
| `collect` | `adapters.py` | 按 `sources.json` 拉列表，`fieldMap` 映射成 `Job`。两种适配器：`fixture`（本地快照）与 `json_list`（分页 HTTP） | 单源失败只记 `failedSources`，不中断整批 |
| `normalize` | `normalize.py` | 城市/薪资/日期/技能/要求句，**全是纯函数** | 未知城市原样返回，不猜 |
| `recent_filter` | `run.py` | 92 天窗口 | 无日期的岗位**保留**（宁可多一条） |
| `classify` | `classify.py` | 前端 / 全栈 / Agent 应用开发 / 其他 | **只认标题证据**，判不出来归「其他」；与 LLM 打标分布差异见下 |
| `jd_store` | `jd_store.py` | 正文按 URL 主键累积合并 | 增量幂等（重复 merge 新增为 0） |
| `llm_match` | `llm_match.py` | 逐条读 JD 原文 → 硬性要求/加分项/缺口/建议 | 输出必须过 schema 校验，不合格重试 2 次后降级 |
| `heuristic_match` | `heuristic_match.py` | 技能覆盖率 + 方向 + 城市 + 年限，乘角色惩罚 | 不懂语义；分数与 LLM 通道**不可比** |
| `aggregate` | `aggregate.py` | 统计、环比、摘要 | `byType` 是整窗口，`lastWeek` 是上周 7 天 |

---

## 三个刻意的设计决定

### 1. 分类只认标题，不认「技术词」

第一版把「AI Agent 产品经理」判成 Agent 开发岗、给到 87 分，因为它满篇都是技术词。
第二版把 `大模型/llm/agent` 放在匹配链最前，结果 913 条里 **417 条**被吞进 Agent 应用开发——
现在几乎每份 JD 都提一句大模型，这个词已经没有区分度。

所以现在的顺序是「先排除、再分类」：

1. 标题是非研发岗（运营/设计/销售/美术…）→ 其他
2. 标题显式写全栈 → 全栈
3. **AI 词紧邻岗位主名词**（`算法工程师`、`agent 开发`）→ Agent 应用开发
4. 标题显式写前端类（前端/客户端/移动端/小程序）→ 前端
5. 其余 AI 词 → Agent 应用开发
6. 技术岗但非目标（测试/运维/DBA）→ 其他
7. 泛研发词（`软件开发`）→ 按技能标签推断技术栈
8. 其余 → 其他

第 3 步的「紧邻」是为了解决一个真实歧义：
`计算机视觉算法实习生（移动端AIGC方向）` 应算 AI 岗（算法是主名词），
`前端研发工程师（视频生成 AI）` 应算前端岗（前端是主名词）。固定优先级必然错一个。

### 2. 规则分刻意不给满分，也不给「证据不足的高分」

覆盖率做了一次贝叶斯加性平滑：`(命中权重 + K·先验) / (标签数 + K)`，K=2、先验 0.2。

不做平滑时，JD 只写 1 个技能、命中 1 个就是 100% 覆盖，于是
「前端开发实习生」拿到 95 分——读起来像完美匹配，实际上是**证据量不足**。
平滑让证据少的岗位回归先验，证据多的才允许逼近真实覆盖率。

上限另外压在 95：规则通道不看团队、业务与隐性要求，给不出满分结论。

### 3. 两条打分通道同一契约，靠 `model` 前缀区分

`model` 以 `heuristic-` 开头 = 规则通道；否则是 LLM。降级时写成
`heuristic-v1 (llm-fallback: <原因>)`，前端据此把两者分开展示，绝不混排。

---

## 校准现状（如实记录）

同一批 913 条岗位，两条通道的产出：

| 指标 | LLM 通道（参考） | 规则通道 |
|---|---|---|
| 分数中位数 / 均值 | 20 / 23.2 | 16 / 24.3 |
| ≥55 分 | 33 条（3.6%） | 67 条（7.3%） |
| ≥70 分 | 12 条 | 2 条 |
| `byType` 前端/全栈/Agent/其他 | 78 / 182 / 447 / 207 | 34 / 14 / 239 / 626 |

**分数分布接近，`byType` 差得很远。** 这不是规则分类器的问题，是参考标签的问题：

- 标成「全栈」的 182 条里，**55% 的标题不含任何技术线索**：
  `海外广告投放 | Go`、`国际版-资深游戏视觉设计师 | Go`、
  `Channel Sales Manager | Go`、`商家运营岗 | Python` 都被标成了全栈。
- 标成「前端」的 78 条里有 28 条是运营 / 设计 / 数据分析岗。

也就是说，原来的 LLM 打标是「看到任何语言标签就当全栈」。
规则分类器**不去追这个分布**——追它等于拟合噪声。判断标准换成了可解释性：
每条 `type` 都能在标题里指出证据，指不出来就归「其他」。

`stats.json` 的 `meta.classifier = heuristic-title-v1` 用来标明类型来源，
避免和 LLM 打标的结果混在一起看。

---

## 接入自己的数据源

1. 复制 `../config/sources.example.json` → `sources.json`，填 `listUrl` 与 `fieldMap`；
2. `python -m pipeline --sources config/sources.json --out data/_live`；
3. 产物按 [`docs/DATA_CONTRACT.md`](../docs/DATA_CONTRACT.md) 落盘，`--sync` 会自动拷到
   `frontend/public/data/`。

换一个招聘站只改 `fieldMap`，不改代码——适配器只负责拿原始记录，
归一化/分类/打分都在后面的固定阶段里。

## 需要的凭据

统一走环境变量，不要写进代码或配置提交：

| 变量 | 用途 |
|------|------|
| `LLM_API_KEY` | OpenAI 兼容端点的 key |
| `LLM_BASE_URL` | 端点地址（默认按厂商填） |
| `LLM_MODEL` | 模型名 |
| `SRC_*_COOKIE` | 需要登录态的数据源凭据（可选） |

## fixtures 是怎么来的

`fixtures/` 里的 914 条原始记录由 `scripts/build_fixtures.py` 从 `data/stats.json`
反向生成：字段、标签、城市、薪资都来自真实采集（已脱敏）。

**但 JD 正文是按标签合成的占位文本**，明确标注「演示用 JD，非真实正文」——
原始正文含公司业务信息，不可公开。合成时刻意不写岗位类型，
否则分类器等于直接读到答案，离线跑出来的分布就没有验证意义了。
