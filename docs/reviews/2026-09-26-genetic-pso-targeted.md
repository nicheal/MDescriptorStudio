# 2026-09-26 — GA/PSO + 目标区域:实测结论(负/弱,门控维持)

问题(用户提出):GA/PSO 用于目标区域定向优化效率低吗?约束:GA/PSO 的
全部机制保留(基因组/掩码/轮盘/记忆/移民一个不裁),定向作为额外信号叠加。
本记录给出 20-seed × 10000 evals 的实测答案。

## 实现(门控已放开,锚点现支持 random/genetic/pso)

* **GA targeted**(`genetic.py`):轮盘权重 = 排名票数 Σk² ×
  `exp(-(d/r)²)`;种子池以基础票数 1 加入加权池(引导:锚点种子 d=0,
  区域填充可从第一轮启动);基因组/掩码/移民份额全部不动。
  `pso_weight_anchor` 之外无新参数。
* **PSO targeted**(`pso.py`):第四拉力质量
  `P_a = U·pso_weight_anchor·d(pos, 锚点)`(默认 1.5),赢时围绕锚点最近
  结构提案;pbest/gbest/mut 拉力与记忆系统全部不动。
* parse_request:锚点现接受 random/genetic/pso(GA/PSO+锚点此前是
  已识别扩展位,本次实现);与 `reuse_accepted_seeds` 互斥不变。
* 测试:`tests/test_generation_search_target.py` 门控更新;GA/PSO 各自
  38/18 个测试全过;全量 580 passed + 1 skipped;golden 基线逐位不变。

## 测度(20 seeds × 10000 evals,`benchmark/results/20260926T024832Z/`)

参照(同锚点、同预算,G5-2 sweep):random-target 接近度中位 **25.50**、
radius 内 21.2%、discovery 23.82;random 全局 47.70 / 25.31。

| 优化器(定向) | 接近度中位数 | 比 random 更近 | radius 内占比 | unique/100 |
| --- | --- | --- | --- | --- |
| genetic-target | 33.84 ± 5.45 | 20/20 | 0.0% | 20.68 ± 6.55 |
| pso-target | **98.04 ± 45.00** | **1/20** | 0.4% | 12.31 ± 7.98 |

## 结论

1. **GA targeted:有效但弱于 random-target**(33.84 vs 25.50,20/20 更近
   但改善幅度只有后者的一半)。机制:rank 压力(novelty 排名)与距离
   权重相乘后,两个信号竞争——区域内结构随填充 novelty 衰减,rank 把
   部分预算持续分给远端新颖结构。保留为可选组合(random-target 仍是
   定向场景的推荐)。
2. **PSO targeted:明确有害**——接近度比不做定向还差一倍(98.04 vs
   47.70,1/20),discovery 全场最差。三重机制冲突:
   (a) 锚点拉力 `P_a ∝ d(pos, anchor)` 是距离缩放的动态质量,粒子进入
   锚点壳层后拉力自动衰减,提案在锚点壳层与远端记忆间震荡;
   (b) pbest/gbest 记忆按 novelty z 分数锚定在远端高分结构,拉力方向
   与区域目标相反;
   (c) 验收端 novelty 目标惩罚锚点壳层结构(靠近已知锚点行),50/50 的
   提案分布下 FPS 把 far 提案优先收下——三重作用系统性把 accepted
   集合推离区域。
3. **最终排序(目标区域场景)**:random-target(25.50)≫ GA-target
   (33.84)≫ random(47.70)≫ PSO-target(98.04)。门控维持开放
   (机制已实现、已测度、已记录),但 UI 提示与默认建议不变:
   定向补采样用 random + 目标区域。
4. 对比 G4 的教训一脉相承:学习型优化器的自适应信号(novelty 排名、
   pbest 记忆)在"固定目标填充"场景下是**干扰源**——目标区域场景的
   正确形态是静态距离加权 + 新颖性验收,两个职责分离。

## 验证

后端 580 passed + 1 skipped;前端 tsc/eslint/229 passed;sweep 全程
checkpoint(`results/20260926T024832Z/`);random-target 参照值取自
G5-2 sweep(`20260925T151421Z`,同锚点同预算,random 在两次 sweep 中
逐位一致)。
