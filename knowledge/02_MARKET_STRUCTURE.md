# 02 市场结构 Market Structure

所有规则：Source = AI 助手的 Al Brooks 体系通用知识（无页码，未对照原书核实）· Provenance = general_knowledge

---
Rule ID: PA-CTX-001
Concept: Always In Long / Always In Short（AIL / AIS）
Definition: 如果此刻必须持有多单或空单之一，哪一方更合理，就是 Always In 方向。它是判断"顺势"的基准。
Required Context: 任何图表。
Detection Logic:
1. 找到最近一次**强突破**：连续多根同向趋势 K 线（实体大、影线小、收在极端附近），并且有跟进。
2. 突破后价格守在突破点之外 → Always In 方向翻转为突破方向。
3. 如果近期多空都没有强突破、K 线重叠多 → 方向不明确，按交易区间处理。
Supporting Evidence: 价格持续在 20 EMA 同一侧；回调浅且短；出现与 EMA 之间的跳空（gap bars）。
Invalidation: 反方向出现更强的突破并有跟进；或者价格进入横盘、来回穿越 EMA。
Trade Considerations: 优先做 Always In 方向的回调入场；逆 Always In 方向交易需要更强的理由（见 TD-001）。
Failure Scenarios: 把交易区间里的一根大 K 线误判成方向翻转；把高潮末端的大 K 线当成新趋势起点。
Confidence: 高

---
Rule ID: PA-TRD-001
Concept: 强趋势 Strong Trend（多 / 空）
Definition: 价格持续创新高（多头）或新低（空头），回调浅、短，买卖压力明显偏向一方。
Required Context: 一段持续单向运动。
Detection Logic: 满足多项越强：连续趋势 K 线；收盘接近 K 线极端；回调只有 1–3 根 K 线；很少触及 EMA；更高的高点与更高的低点（多）或相反（空）；趋势方向的跳空。
Supporting Evidence: 逆势信号 K 线弱、很快失败；突破后有跟进。
Invalidation: 回调变深、变长，多次触及并穿越 EMA；K 线开始重叠 → 正在转入通道或区间。
Trade Considerations: 强趋势中顺势交易，H1/L1 甚至市价追随都可以接受；逆势交易通常是低胜率。
Failure Scenarios: 趋势末端出现高潮（PA-CLX-001），此时追单风险最大。
Confidence: 高

---
Rule ID: PA-TR-001
Concept: 交易区间 Trading Range（TR）
Definition: 价格在大致水平的上下边界之间来回运动，多空力量接近平衡。
Required Context: 一段 K 线大量重叠、多次穿越 EMA 的横向走势。
Detection Logic: 两次以上触及上下沿并折返；上下影线多；突破尝试很快被拉回。
Supporting Evidence: 区间中部 K 线小、十字星多。
Invalidation: 一方出现强突破并有跟进，且价格守在区间之外。
Trade Considerations: 区间里"高抛低吸"（buy low, sell high）；在区间下沿找买点、上沿找卖点；区间中部不交易。大多数突破区间的尝试会失败（Brooks 常强调这一点，具体比例需对照原书核实）。
Failure Scenarios: 把区间里的突破当成新趋势追单；在区间中部入场导致风险回报很差。
Confidence: 高

---
Rule ID: PA-TTR-001
Concept: 窄幅区间 Tight Trading Range（TTR / barbwire 铁丝网）
Definition: 很窄的横盘，K 线高度重叠，十字星多（十字星本身就是 1 根 K 线的交易区间）。
Required Context: 任何位置，常见于趋势中继或高潮之后。
Detection Logic: 连续多根小实体、长影线 K 线，几乎完全重叠，常围绕 EMA。
Supporting Evidence: 突破尝试马上被吞没。
Invalidation: 出现强突破并有跟进。
Trade Considerations: 通常不交易，等待突破及其跟进；突破方向往往与此前的趋势方向一致，但不确定。
Failure Scenarios: 在 TTR 内部用停损单入场，被反复扫损。
Confidence: 高

---
Rule ID: PA-BO-001
Concept: 突破与跟进 Breakout and Follow-through
Definition: 价格以强趋势 K 线越过重要价位（区间边界、趋势线、前高前低），随后 K 线继续同向。
Required Context: 存在一个明确的被突破价位。
Detection Logic: 1) 突破 K 线实体大、收在极端附近；2) 下一根或几根 K 线继续同向（跟进）；3) 回调不回到突破点之内。
Supporting Evidence: 连续多根趋势 K 线；与突破点之间留有缺口（gap）。
Invalidation: 跟进 K 线弱（反向 K 线、十字星、长影线），价格回到突破点之内 → 可能是失败突破（PA-FBO-001）。
Trade Considerations: 强突破后可以顺势入场（收盘入场或回调入场）；止损通常放在突破 K 线或尖峰起点之外，风险较大需减仓。
Failure Scenarios: 区间内部的突破、出现在趋势末端的突破（可能是高潮）。
Confidence: 高

---
Rule ID: PA-BO-002
Concept: 突破回调 Breakout Pullback / Breakout Test
Definition: 突破后价格回到突破点附近测试，然后恢复突破方向。
Required Context: 刚发生过一次有效突破（PA-BO-001）。
Detection Logic: 突破 → 回调接近原突破点或 EMA → 出现顺势信号 K 线（常是 H1/H2 或 L1/L2）。
Supporting Evidence: 回调 K 线小、重叠；回调未完全填补缺口。
Invalidation: 回调深入原区间并有强反向 K 线 → 突破失败。
Trade Considerations: 在顺势信号 K 线之外挂停损单入场；止损在回调极端之外；目标为突破的量度目标（PA-MM-001）或前一极端。
Failure Scenarios: 原突破本身是高潮；回调是 TTR 而不是明确的回调。
Confidence: 中高

---
Rule ID: PA-SC-001
Concept: 尖峰与通道 Spike and Channel
Definition: 先出现强劲急速的尖峰（spike），之后进入斜率较缓、K 线更重叠的同向通道（channel）。
Required Context: 一次强突破之后。
Detection Logic: 识别尖峰（连续大趋势 K 线）→ 回调 → 通道（较小 K 线，逐步推进，回调更多）。
Supporting Evidence: 通道中出现多次推动、动能减弱。
Invalidation: 尖峰后立即强烈反转（V 形）。
Trade Considerations: 通道最终常演变为交易区间；常见的目标是回到通道起点附近测试（需对照原书核实其具体表述）。通道后期顺势追单风险增加。
Failure Scenarios: 把通道后段当作新的尖峰追单。
Confidence: 中

---
Rule ID: PA-CH-001
Concept: 窄通道 vs 宽通道 Tight Channel / Broad Channel
Definition: 窄通道：回调很小、几乎没有反向 K 线越过前一根 K 线，强度接近强趋势。宽通道：回调明显、常触及 EMA，强度介于趋势与区间之间。
Required Context: 一段倾斜的单向运动。
Detection Logic: 数回调深度与持续时间；看反向 K 线的大小与数量。
Supporting Evidence: 窄通道里逆势信号几乎都失败。
Invalidation: 回调突破通道线并有跟进 → 通道可能结束。
Trade Considerations: 窄通道只做顺势；宽通道可在回调处顺势入场，也可以在通道极端附近做小量逆势（需强信号）。
Failure Scenarios: 在窄通道里做"第一次逆势"。
Confidence: 中高

---
Rule ID: PA-CLX-001
Concept: 高潮 Climax（Buy Climax / Sell Climax）
Definition: 趋势后段出现异常大、连续的趋势 K 线，代表最后一批追单者入场，之后常出现交易区间或反转。
Required Context: 已经持续一段时间的趋势。
Detection Logic: 1) 趋势已运行多段；2) 出现一根或多根远大于平均的趋势 K 线，常伴随跳空；3) 随后出现反向 K 线或横盘。
Supporting Evidence: 连续第二次、第三次高潮（consecutive climaxes）；到达量度目标或重要价位；高潮后跟进很弱。
Invalidation: 大 K 线之后有强跟进、继续创新极值并守住 → 那是突破而不是高潮。
Trade Considerations: 高潮后通常先进入至少两段的横盘或回调；顺势者应获利了结，逆势入场仍需等待反转信号（如 PA-MTR-001、双顶/双底）。
Failure Scenarios: 把趋势初期的突破当作高潮去逆势。
Confidence: 中高

---
Rule ID: PA-MTR-001
Concept: 主要趋势反转 Major Trend Reversal（MTR）
Definition: 趋势转向的典型过程：趋势线被突破 → 回测趋势极值（更高低点 HL / 更低高点 LH / 双底 / 双顶）→ 出现强反转。
Required Context: 一段明确的趋势之后。
Detection Logic:
1. 先有一次反向的强运动突破趋势线或 EMA（显示反方向已有力量）。
2. 价格回测原趋势的极值：形成 HL（空转多）或 LH（多转空），或者小幅越过后失败（更低低点 / 更高高点后反转）。
3. 回测处出现强反转信号 K 线，之后反向突破并有跟进。
Supporting Evidence: 回测时出现楔形、双底/双顶；之前有高潮。
Invalidation: 回测时以强趋势 K 线越过原极值并有跟进 → 原趋势恢复。
Trade Considerations: 在回测处的信号 K 线之外入场，止损在回测极值之外；目标至少为一次量度运动或前一个主要摆动点（需对照原书核实）。
Failure Scenarios: 没有先发生趋势线突破就直接逆势（只是普通回调）；回测太快、反转 K 线弱。
Confidence: 中高

---
Rule ID: PA-MM-001
Concept: 量度运动 Measured Move（MM）
Definition: 价格常运行与此前某段距离相等的幅度：例如尖峰的高度、交易区间的高度，或第一段（leg 1）等于第二段（leg 2）。
Required Context: 有一个可以量度的基准（尖峰、区间、第一段）。
Detection Logic: 从基准的起点量到终点，把同样的距离从突破点或第二段起点投射出去。
Supporting Evidence: 投射位置附近有前高前低或整数关口。
Invalidation: 不是目标保证，只是常见的"磁吸位"。
Trade Considerations: 用作获利目标和观察反转的区域。
Failure Scenarios: 在没有明确基准时乱量。
Confidence: 中
