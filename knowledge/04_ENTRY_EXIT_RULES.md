# 04 交易决策、出入场与市场心理 Trading Decisions & Psychology

所有规则：Source = AI 助手的 Al Brooks 体系通用知识（无页码，未对照原书核实）· Provenance = general_knowledge

---
Rule ID: TD-001
Concept: 顺势与逆势 With-trend vs Countertrend
Definition: 顺势 = 与 Always In 方向一致的交易；逆势 = 相反方向。
Required Context: 先由 PA-CTX-001 确定方向。
Detection Logic: 对每个机会标注它是顺势还是逆势。
Supporting Evidence: 逆势要合格，至少需要：趋势已显疲态（高潮、楔形、趋势线被突破）、强反转信号 K 线、最好是第二次尝试（second entry）。
Invalidation: 强趋势中的第一次逆势信号——通常不交易。
Trade Considerations: 顺势：可用较宽松的信号、可持有波段；逆势：仓位小、以短线为主，或者等 MTR 结构完整。
Failure Scenarios: 因为"涨太多了"就逆势做空。
Confidence: 高

---
Rule ID: TD-002
Concept: 交易者方程 Trader's Equation
Definition: 一笔交易只有在"成功概率 × 盈利空间 > 失败概率 × 风险"时才合理。概率高的交易常常报酬小；报酬大的交易常常概率低。
Required Context: 任何入场决策。
Detection Logic: 估计风险（入场到止损的距离）、合理目标的距离，以及背景给出的概率倾向（只能定性：偏高 / 50-50 / 偏低）。
Supporting Evidence: 顺势 + 强信号 + 好位置 → 概率偏高。
Invalidation: 止损太远而目标太近；区间中部入场。
Trade Considerations: 复盘时说明每个机会的风险回报是否合理，不编造具体胜率数字。
Failure Scenarios: 只看形态不算风险回报。
Confidence: 高

---
Rule ID: TD-003
Concept: 止损设置 Stop Placement
Definition: 止损放在"形态失效"的价位：信号 K 线另一端之外（较窄），或者形态 / 摆动点极值之外（较宽）。
Required Context: 已识别的交易机会。
Detection Logic: 问："价格到哪里，原来的理由就不成立了？"那里就是止损。
Supporting Evidence: 止损外有明确的结构（前低前高、区间边界）。
Invalidation: 止损放在随意的金额而不是结构位置。
Trade Considerations: 风险太大时减仓，而不是把止损挪近到无意义的位置。
Failure Scenarios: 止损放在显眼的整数位或 K 线极端正下方，容易被扫。
Confidence: 中高

---
Rule ID: TD-004
Concept: 获利目标与退出 Profit Taking
Definition: 常见目标：前一摆动高低点、区间另一侧、量度目标（PA-MM-001）。短线（scalp）目标较小、要求高概率；波段（swing）目标较大、可接受较低概率。
Required Context: 已识别的交易。
Detection Logic: 找入场方向上最近的"磁吸位"（前高前低、区间边界、量度目标、整数关口）。
Supporting Evidence: 强趋势中可以持有部分仓位做波段。
Invalidation: 目标比止损还近（违反 TD-002）。
Trade Considerations: 在目标附近或出现反向信号 / 高潮时分批离场。
Failure Scenarios: 区间里期待趋势级别的目标。
Confidence: 中高

---
Rule ID: TD-005
Concept: 不该交易的情形 When Not to Trade
Definition: 一些看起来像形态的位置，实际风险回报差或概率低。
Required Context: 任何位置。
Detection Logic: 满足以下任一项就应提示"不交易 / 谨慎"：
1. 位于 TTR 内部或区间中部（PA-TTR-001、PA-TR-001）。
2. 强趋势中的第一次逆势信号（TD-001）。
3. 信号 K 线弱，或者太大导致止损过远（PA-BAR-001）。
4. 在高潮末端追单（PA-CLX-001）。
5. 区间顶部买入 H2 / 区间底部卖出 L2。
Supporting Evidence: 多个条件同时出现时更应避免。
Invalidation: —
Trade Considerations: 复盘中要专门指出这些位置，帮助交易者避免。
Failure Scenarios: "错过行情"后的报复性入场。
Confidence: 高

---
Rule ID: TD-006
Concept: 区间中的限价单逆向交易 Fading in a Trading Range
Definition: 在交易区间里，于上沿附近卖出、下沿附近买入（常用限价单），预期突破失败。
Required Context: 已确认的交易区间（PA-TR-001），且没有强突破迹象。
Detection Logic: 价格接近区间边界，K 线开始变弱（影线、反向实体）。
Supporting Evidence: 之前已多次在边界折返；突破 K 线弱。
Invalidation: 出现强突破并有跟进 → 立刻停止逆向交易。
Trade Considerations: 止损在边界之外；目标为区间中部或另一侧。
Failure Scenarios: 区间最终会被突破，此时逆向交易者被困。
Confidence: 中

---
Rule ID: PSY-001
Concept: 被困交易者 Trapped Traders
Definition: 在错误位置入场的一方（例如追突破的人遇到失败突破），他们的止损离场会推动价格朝相反方向运动。
Required Context: 失败突破、失败反转、高潮之后。
Detection Logic: 找出"在哪里入场的人现在被套"，以及他们的止损大致在哪。
Supporting Evidence: 被困一方的止损被触发时出现大 K 线。
Invalidation: —
Trade Considerations: 顺着让被困者离场的方向交易。
Failure Scenarios: 这是行为推断，无法确认真实订单流，复盘中应写成"可能"。
Confidence: 中

---
Rule ID: PSY-002
Concept: 获利了结与磁吸位 Profit Taking and Magnets
Definition: 价格接近量度目标、前高前低、整数关口时，顺势者获利了结，逆势者开始入场，常导致停顿或回调。
Required Context: 价格接近明显目标位。
Detection Logic: 标出附近的磁吸位，观察到达后 K 线是否变小、出现反向 K 线。
Supporting Evidence: 到达后出现高潮式 K 线或横盘。
Invalidation: 强趋势中价格直接穿越目标并有跟进。
Trade Considerations: 顺势者在目标附近减仓；不在目标位追单。
Failure Scenarios: 把"可能出现的获利了结"当成反转信号。
Confidence: 中
