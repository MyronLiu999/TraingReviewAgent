# 03 价格行为形态 Price Action Patterns

所有规则：Source = AI 助手的 Al Brooks 体系通用知识（无页码，未对照原书核实）· Provenance = general_knowledge

---
Rule ID: PA-H-001
Concept: H1 / H2 / H3 计数 Bar Counting（多头回调）
Definition: 多头趋势或区间中的回调里，**某根 K 线的高点高于前一根 K 线的高点**，就是一次向上尝试。
回调中第一次 = High 1（H1），回调继续走低后的第二次 = High 2（H2），第三次 = H3。
Required Context: 一段上涨后的回调（或交易区间里的下跌段）。
Detection Logic:
1. 找到回调起点（最近的摆动高点）。
2. 从回调开始，逐根比较：当前 K 线高点 > 前一根 K 线高点 → 计一次（H1）。
3. 价格再次下跌（至少一根 K 线高点不再上破），之后又有一根 K 线高点 > 前一根 → H2。同理计 H3。
4. **重新计数**：价格向上突破回调起点（创新高）→ 回调结束，计数清零；如果回调演变成强空头趋势（强空头突破并有跟进），多头计数失去意义，改用空头视角。
Supporting Evidence: H2 出现在 EMA 附近或略下方；回调是两段式（PA-2LEG-001）；信号 K 线是收在高位的阳线。
Invalidation: 在强空头 Always In 中，H1/H2 只是空头回调中的小反弹，不是多头买点。
Trade Considerations: 见 PA-H-002。
Failure Scenarios / 常见误判: 把每一根高点高于前一根的 K 线都机械地计数而忽略背景；在区间顶部数 H2；把 inside bar 的轻微突破当作有效 H1。
Confidence: 中高（具体计数细节和边界情况需对照原书核实）

---
Rule ID: PA-H-002
Concept: High 2 买点 High 2 Buy Setup
Definition: 多头背景下两段式回调结束时的买入形态（第二次向上尝试）。
Required Context: Always In Long（PA-CTX-001），或交易区间的下沿附近。
Detection Logic: 按 PA-H-001 识别 H2 → 检查信号 K 线（PA-BAR-001）→ 在信号 K 线高点上方挂停损买单。
Supporting Evidence: 回调到 EMA；回调 K 线变小、重叠；之前的趋势强劲；H2 同时也是双底或楔形底的一部分。
Invalidation: 信号 K 线是大阴线或长上影线；H2 出现在交易区间顶部；回调已经破坏多头结构（强空头突破）。
Trade Considerations: 入场：信号 K 线高点之上 1 tick 的停损单。止损：信号 K 线低点下方，或回调低点下方。目标：先看前高（测试趋势高点），强趋势中可以持有波段。
Failure Scenarios: H2 失败后常出现空头的第二段下跌（失败的 H2 可能成为 L2 卖点）；强多头里 H1 就已启动，等 H2 可能错过。
Confidence: 中高

---
Rule ID: PA-L-001
Concept: L1 / L2 / L3 计数与 Low 2 卖点（空头回调）
Definition: 空头趋势或区间中的反弹里，**某根 K 线的低点低于前一根 K 线的低点**为一次向下尝试：第一次 L1，第二次 L2，第三次 L3。
Required Context: 一段下跌后的反弹（或交易区间里的上涨段）。
Detection Logic: 与 PA-H-001 镜像：从反弹起点（摆动低点）开始逐根比较低点；价格跌破反弹起点（创新低）时重新计数；若反弹演变成强多头趋势，空头计数失效。
Supporting Evidence: L2 出现在 EMA 附近或略上方；两段式反弹；信号 K 线是收在低位的阴线。
Invalidation: 在 Always In Long 中，L1/L2 只是多头回调，不是卖点；L2 出现在区间底部。
Trade Considerations: 在信号 K 线低点下方挂停损卖单；止损在信号 K 线高点或反弹高点上方；目标为前低。
Failure Scenarios: 失败的 L2 常带来多头的第二段上涨；强空头里 L1 已足够，等 L2 可能错过。
Confidence: 中高

---
Rule ID: PA-2LEG-001
Concept: 两段式回调 Two-Legged Pullback（ABC）
Definition: 回调通常由两段组成：第一段（A）→ 小幅反弹（B）→ 第二段（C）。第二段结束处常是顺势入场点（对应 H2 / L2）。
Required Context: 趋势中的回调，或趋势反转后的第一次测试。
Detection Logic: 回调中出现一次明确的反向小反弹，之后价格再次朝回调方向运动并越过（或接近）第一段的极值。
Supporting Evidence: 第二段接近 EMA、前一摆动点或量度目标（第二段 ≈ 第一段）。
Invalidation: 第二段以强趋势 K 线大幅越过第一段并有跟进 → 可能不只是回调，而是反转。
Trade Considerations: 在第二段结束时的顺势信号 K 线上入场。
Failure Scenarios: 把第一段就当作回调结束；在窄通道里等不到两段。
Confidence: 中高

---
Rule ID: PA-WDG-001
Concept: 楔形 Wedge（三次推动）
Definition: 三次推动（three pushes）构成的收敛运动，通常每次推进的幅度或动能递减。楔形可以是反转形态（楔形顶/底）或旗形（楔形牛旗/熊旗，即 H3/L3）。
Required Context: 趋势后段（反转楔形），或趋势中的回调（楔形旗形）。
Detection Logic: 找到三个依次推进的极值点（第 1、2、3 推）；第三推常略微越过第二推后失败；三推可以连成收敛的趋势线与通道线。
Supporting Evidence: 第三推动能弱、出现反向信号 K 线；楔形之后的突破有跟进。
Invalidation: 第三推之后以强趋势 K 线继续推进并有跟进 → 楔形失败，可能出现量度运动。
Trade Considerations: 反转楔形：在第三推后的反向信号 K 线之外入场，止损在第三推极值之外；目标常为楔形起点附近（需对照原书核实）。楔形反转常先出现两段式运动。
Failure Scenarios: 强趋势中的楔形反转常只带来回调而不是趋势反转。
Confidence: 中

---
Rule ID: PA-DT-001
Concept: 双顶 / 双底 Double Top / Double Bottom（含双底牛旗、双顶熊旗）
Definition: 价格两次在接近的价位遇阻（双顶）或获得支撑（双底）。出现在趋势中的回调里时称为双底牛旗 / 双顶熊旗。
Required Context: 顶部 / 底部反转（需配合 PA-MTR-001 的背景），或趋势中的回调（旗形）。
Detection Logic: 找到两个高度接近的摆动高点（或低点），中间有一次明显的回调；第二次测试时出现反向信号 K 线。
Supporting Evidence: 第二个顶低于第一个（LH）或第二个底高于第一个（HL）通常更强；第二次测试时 K 线弱、影线长。
Invalidation: 第二次测试时以强趋势 K 线突破并有跟进 → 形态失败，可能出现量度运动。
Trade Considerations: 在第二个顶/底的信号 K 线之外入场，止损在该极值之外；目标为颈线（中间的回调点），突破颈线后可看量度运动。
Failure Scenarios: 强趋势中逆势做双顶/双底；在区间中部画出的"双顶"。
Confidence: 中高

---
Rule ID: PA-DT-002
Concept: 微型双顶 / 双底 Micro Double Top / Bottom
Definition: 相邻或相距 1–3 根 K 线的高点（或低点）几乎相同，是很小级别的支撑阻力。
Required Context: 任何位置，常见于回调末端（作为信号）或突破前。
Detection Logic: 相邻 K 线的高点（或低点）几乎相同。
Supporting Evidence: 出现在 EMA、前高前低或回调末端。
Invalidation: 下一根 K 线强势突破该价位并有跟进。
Trade Considerations: 可作为顺势入场的细节信号，止损通常较窄。
Failure Scenarios: 在 TTR 中意义不大。
Confidence: 中

---
Rule ID: PA-FF-001
Concept: 末端旗形 Final Flag
Definition: 趋势后段的一个旗形（常是横盘的小区间），突破后很快失败并反转，标志趋势的最后一推。
Required Context: 已经持续较久、出现过高潮迹象的趋势。
Detection Logic: 趋势后段出现横盘旗形（常是 TTR）→ 顺势突破 → 突破只持续 1–几根 K 线 → 反向回到旗形之内并继续反向。
Supporting Evidence: 突破 K 线弱或跟进差；之前已有高潮或楔形。
Invalidation: 突破后有强跟进并守住。
Trade Considerations: 在突破失败后的反向信号 K 线之外入场；目标常为旗形另一侧或更远。
Failure Scenarios: 在强趋势早期把普通旗形当作末端旗形逆势。
Confidence: 中

---
Rule ID: PA-FBO-001
Concept: 失败突破 Failed Breakout（含区间假突破）
Definition: 价格越过重要价位后无法跟进，迅速回到原区域，困住了追突破的交易者。
Required Context: 区间边界、前高前低、趋势线等明确价位；交易区间中最常见。
Detection Logic: 突破 K 线后出现反向 K 线或长影线；价格在 1–几根 K 线内回到突破点之内；反方向出现信号 K 线。
Supporting Evidence: 背景是交易区间；突破 K 线本身弱；突破方向与更大级别的 Always In 方向相反。
Invalidation: 回到突破点内后又以强 K 线再次突破并有跟进（失败的失败 = 突破成功，第二次突破常更可靠）。
Trade Considerations: 在反向信号 K 线之外入场；止损在突破极值之外；目标为区间另一侧或中部。被困者止损离场会推动价格。
Failure Scenarios: 在强趋势中"逆势做假突破"往往只是趋势中的停顿。
Confidence: 中高

---
Rule ID: PA-FREV-001
Concept: 失败反转 Failed Reversal
Definition: 逆势的反转尝试（例如强多头里出现的卖出信号）入场后没有跟进，价格恢复原趋势，被困的逆势者推动趋势延续。
Required Context: 强趋势中的逆势信号。
Detection Logic: 逆势信号 K 线触发 → 下一根或几根 K 线未跟进 → 价格越过信号 K 线的另一端。
Supporting Evidence: 原趋势强（PA-TRD-001）；逆势信号 K 线弱。
Invalidation: 反转有强跟进并突破 EMA / 趋势线（可能是 MTR 的第一步）。
Trade Considerations: 在原趋势方向入场，止损在失败反转的极值之外。
Failure Scenarios: 趋势已经处于后期并出现多次高潮时，这个规则可靠性下降。
Confidence: 中

---
Rule ID: PA-BAR-001
Concept: 信号 K 线质量 Signal Bar Quality
Definition: 信号 K 线是触发入场之前的那根 K 线。好的多头信号 K 线（反转阳线）：阳线实体、收在高位、下影线较长、上影线小、与前几根 K 线重叠不多。空头信号 K 线镜像。
Required Context: 任何入场决策。
Detection Logic: 检查实体方向和大小、收盘位置、影线比例、K 线大小相对近期平均（太大则止损太远）。
Supporting Evidence: 信号 K 线出现在正确的背景位置（EMA、区间边缘、第二次尝试）。
Invalidation: 十字星、反向实体、太小的 K 线、在 TTR 中的 K 线 → 信号弱。
Trade Considerations: 弱信号 K 线在顺势背景下有时可接受；逆势交易必须要强信号 K 线。
Failure Scenarios: K 线很大但出现在趋势末端（可能是高潮或被困者的最后入场）。
Confidence: 高

---
Rule ID: PA-BAR-002
Concept: 入场 K 线与跟进 Entry Bar / Follow-through
Definition: 入场 K 线是触发入场的那根 K 线。强入场 K 线（同向趋势 K 线、收在极端）和随后的跟进 K 线，确认交易方向。
Required Context: 已经触发的交易。
Detection Logic: 入场后观察 1–3 根 K 线：同向实体并收在极端 → 确认；反向 K 线或十字星 → 预警。
Supporting Evidence: 入场 K 线与信号 K 线之间留下缺口。
Invalidation: 入场 K 线收成反向实体 → 交易可能失败，考虑提前离场。
Trade Considerations: 跟进差时减仓或收紧止损。
Failure Scenarios: 被一根大的反向 K 线洗出后价格又恢复（需要看背景）。
Confidence: 中高

---
Rule ID: PA-BAR-003
Concept: 内包线 / 外包线 Inside Bar / Outside Bar（含 ii、ioi）
Definition: 内包线：高低点都在前一根 K 线之内，代表犹豫、小区间。外包线：高点高于、低点低于前一根，代表双方争夺。ii = 连续两根内包线；ioi = 内-外-内。
Required Context: 形态本身中性，必须结合背景。
Detection Logic: 逐根比较高低点。
Supporting Evidence: 内包线出现在回调末端时可以作为顺势信号（突破方向入场）；外包线收盘方向更重要。
Invalidation: 在 TTR 中意义很弱。
Trade Considerations: 顺着背景方向在内包线的突破处入场，止损在内包线或母线另一端；外包线入场的止损常较远。
Failure Scenarios: 内包线的双向突破（先触发一边再反向）。
Confidence: 中

---
Rule ID: PA-BAR-004
Concept: 连续收盘 / 连续趋势 K 线 Consecutive Closes / Trend Bars
Definition: 多根连续同向收盘（尤其实体大、收在极端）的 K 线，代表一方持续主导。
Required Context: 突破或趋势中。
Detection Logic: 数连续同向实体 K 线的数量和大小；看收盘是否持续接近极端。
Supporting Evidence: 发生在突破处（PA-BO-001）→ 强；发生在趋势后段且远大于平均 → 可能是高潮（PA-CLX-001）。
Invalidation: 连续趋势 K 线之后立即出现大反向 K 线。
Trade Considerations: 早期顺势可以跟随；后期更要防高潮。
Failure Scenarios: 把高潮当作新趋势。
Confidence: 中高

---
Rule ID: PA-EMA-001
Concept: 均线回调与均线缺口 EMA Pullback / Gap Bars
Definition: 趋势中价格回调到 20 EMA 附近常出现顺势入场机会。价格长时间不触及 EMA（K 线与 EMA 之间有缺口）说明趋势强；趋势很久之后第一次回到 EMA 常有顺势反应。
Required Context: 趋势背景（PA-TRD-001 或 Always In 明确）。
Detection Logic: 回调触及或接近 EMA → 出现顺势信号 K 线（常是 H2 / L2）。
Supporting Evidence: 回调是两段式；回调 K 线小。
Invalidation: 价格强势穿越 EMA 并有跟进 → 背景可能改变。
Trade Considerations: 在信号 K 线之外入场；止损在回调极端之外。
Failure Scenarios: 在交易区间里把 EMA 当作支撑阻力（区间中 EMA 意义下降）。
Confidence: 中
