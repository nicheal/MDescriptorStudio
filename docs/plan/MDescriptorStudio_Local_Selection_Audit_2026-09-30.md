# MDescriptorStudio：局域环境选择与严格去重深度审阅

审阅日期：2026-09-30（北京时间）。

最终审阅 main：`9f73f0b2895248958a09ec78e072ed1c8675154a`。
最初拉取 `bd186395d4647129876a70daac1e7277ebeb1cec`；复核远端后同步到上述最新提交。新增提交只增加 benchmark/resume_sweep.py，本次涉及的函数和测试没有变化。

本次只审阅、执行测试、构造复现，没有修改仓库实现，也没有提交修复。

## 1. 结论

上一轮修复在 raw 空间已解决候选内部重复计数、archive-near 行错误排斥真正 novel 行、strict > threshold 边界和发现率停机指标问题。然而，**严格计数与局域选择并没有真正共用同一距离空间**：计数器的 archive 判定使用缩放距离，候选内部/候选间判定使用 raw 距离；局域选择的三类判定全部使用局域 archive 的缩放空间。

**改变候选顺序可以改变选中身份、选择顺序，甚至严格计数；改变同一结构的原子行顺序也可以改变严格计数。** 固定输入顺序可以复现，不代表排列不变。这是目前阈值贪心代表集的数学语义，不应简单宣称“严格唯一环境数”具有排列不变性。

另有两个直接复现的边界缺口：budget > 128 时局域选择不能与 FPS 保持等接收数；所有被选结构的局域描述符行为空时，选择函数可返回，但引擎在局域 archive 更新时抛错，并已修改结构 archive。

## 2. 证据与测试范围

源码定位均使用上述提交的不可变路径：

- [engine.py](https://github.com/nicheal/MDescriptorStudio/blob/9f73f0b2895248958a09ec78e072ed1c8675154a/backend/mdescriptor_studio_backend/generation/engine.py)：共享过滤 71–99，严格计数 102–133，FPS 包装 208–232，局域选择 238–324，更新前计数 551–556，archive 更新 615–632，发现率消费指标 732–745。
- [archive.py](https://github.com/nicheal/MDescriptorStudio/blob/9f73f0b2895248958a09ec78e072ed1c8675154a/backend/mdescriptor_studio_backend/generation/archive.py)：非空 reference 契约 33–36，局域 add 159–164。
- [preprocessing.py](https://github.com/nicheal/MDescriptorStudio/blob/9f73f0b2895248958a09ec78e072ed1c8675154a/backend/mdescriptor_studio_backend/analysis/sampling/preprocessing.py)：固定缩放、raw/standardized/robust。
- [test_generation_engine.py](https://github.com/nicheal/MDescriptorStudio/blob/9f73f0b2895248958a09ec78e072ed1c8675154a/tests/test_generation_engine.py)：TestUniqueEnvironmentMetric。
- [test_generation_selection.py](https://github.com/nicheal/MDescriptorStudio/blob/9f73f0b2895248958a09ec78e072ed1c8675154a/tests/test_generation_selection.py)：策略、固定 benchmark、空行和集成测试。

执行结果：

| 检查 | 结果 | 含义 |
|---|---|---|
| 原有 selection、engine、objectives、fps_sampling 四个测试文件 | 99 passed | 现有回归全部通过 |
| 补充诊断案例 | 15 passed | 成功锁定本文描述的当前行为；其中包含对当前缺陷的断言，不代表缺陷已修复 |

运行环境：Python 3.12，NumPy 2.3.5，SciPy 1.17.0，pytest 9.1.1。使用临时测试环境；未修改仓库依赖。没有运行全仓库测试、真实描述符计算或大规模性能 benchmark；结论范围为上述源码路径及合成回归。

## 3. 逐项核查

| 用户要求 | 实现与现有测试 | 审阅判定 |
|---|---|---|
| archive 一侧 strict > threshold | nearest_per_row(raw) > threshold | 等于阈值不 novel；raw 等号及 nextafter 两侧验证通过 |
| 已计数集一侧 strict > threshold | sqrt(min_sqdist) > threshold | 比较符号正确；严格计数使用 raw 距离，缩放语义错误 |
| 候选内部去重 | _candidate_novel_rows 按行贪心，只保留通过判定的行 | 相同/近重复行不再重复计数；仍依赖行顺序及计数器缩放问题 |
| archive-near 不阻挡 novel | 首先 rows[archive_novel]，然后检查 memory 和候选内重复 | 同轮内部与跨候选均正确；raw 0.9→1.8 反例已修复 |
| 固定结构/原子行顺序 | selected 顺序→每结构 slice 原始行顺序 | 没有规范化排序；这里的“固定”指调用者提供的顺序 |
| 缩放一致性 | selector 使用 scaled_rows，counter 使用 raw rows | **未修复，高优先级** |
| 空候选 | finite.size==0 或 budget<=0 返回 []；计数 selected=[] 返回 0 | 直接接口正确；原测试仅覆盖全非有限 fitness，补充真零候选 |
| 空局域行块 | selector 可作为零增量填充；counter 跳过 | 单函数正确；全空被选批次引擎 update 崩溃 |
| 空 archive | accepted_matrix=None 正常；reference 必须非空 | “无新增接受项”支持；“无 reference 也无接受项”不支持，应明确区别 |
| 跨轮 archive 更新 | 更新前计数，每轮整批加入全部被接受结构的局域行 | 正常两轮流程正确；新增 spy 验证每轮一次，查询只看冻结版本 |
| 局域与结构 FPS tie-break | 局域稳定降序；FPS 稳定升序后反转 | fitness 相等时两者候选顺序相反；边界池成员也可能不同 |
| 同预算局域不输 FPS | 现有 5 个固定随机 seed + 1 个 engine seed | 只能证明这些数据；存在 raw、真实 mean pooling 反例 |

## 4. 已修复问题

这些修复来自已有提交，不是本次修改。

1. **候选内部重复行多计数**：现在逐行检查 kept_matrix，重复环境只保留一个。原有 `test_within_candidate_duplicates_count_once` 验证 `[3,3]` 计 1。
2. **非 novel 行污染同轮 memory**：只把实际通过判定的 block 放进 counted/memory。原有 `test_non_novel_rows_never_block_a_later_novel_row` 验证 archive=0，near=0.9，novel=1.8，threshold=1，计 1；新增同结构及候选逆序也通过。
3. **等阈值边界**：archive、已计数集、候选内部均使用 >。原有 `[1,2,3]` 反例计 1；补充 np.nextafter 验证阈值上下两侧。
4. **更新后才计数导致恒零**：当前引擎先计数再更新，现有 pre-round 测试通过。
5. **发现率停机使用 raw 重复数**：现在优先读取 unique_novel_environments；原有停机回归通过。不过该 unique 指标的缩放缺陷仍会传入停机判断。
6. **局域选择只按均值挑结构**：新增局域策略真正用环境增量选结构、用结构 maximin 处理增量同分；但不保证所有数据上优于 FPS。

## 5. 仍存风险与优先级

### P0：计数器使用混合距离空间

engine.py 126–129 把 raw rows 传给共享过滤器；nearest_per_row 内部会缩放，但共享过滤器不知道 scaling。局域选择 287–300 则显式传 scaled_rows。

因而“共用一个 helper”只能保证过滤步骤相同，不能保证数值语义相同。尺度 > 1 可以过计数，尺度 < 1 可以欠计数，候选间和候选内部都受影响。benchmark numerator 和 discovery_saturated 停机时间也受影响。

建议最小修复：在计数器入口应用 archive.scaling；archive mask 继续对 raw rows 查询；过滤和 counted 全部存 scaled rows。不要把 scaled rows 再传给 nearest_per_row 造成二次缩放。最好将 raw、scaled、archive_novel 明确分开，或共用准备函数。

### P1：顺序依赖需要明确契约

threshold 距离关系不是等价关系：A 近 B、B 近 C，并不意味着 A 近 C。贪心先选 B 可能只计 1；先选 A 可能计 A 和 C 两个。这不能通过简单修改 > 或统一缩放消失。

如目标是固定输入可复现，保留此算法但明确指标为“给定顺序的贪心严格间隔代表数”。如目标是结构/原子排列不变，应先定义稳定 candidate identity 和规范化原子行顺序，并重新版本化指标。规范化贪心也不等于最大间隔集合；连通分量聚类又是不同定义。

两个接口没有 candidate_id 参数，因此仅靠它们当前的索引排序无法获得跨输入排列稳定的身份选择。固定随机种子也不能消除输入顺序影响。

### P1：局域与 FPS 的 elite 排序不一致

FPS：`argsort(fitness, stable)[::-1]` 会反转相同 fitness 的输入顺序；局域：`argsort(-fitness, stable)` 保留相同 fitness 的输入顺序。

局域首选：最大环境增量→fitness 排名顺序。后续：最大环境增量→最大结构最小距离→fitness 排名顺序。这里 fitness 主要控制 elite 和末级同分，并非每一步增量同分后先比 fitness。

FPS 首选：elite centroid 最近的点，centroid 距离同分取 pool 首行。后续：最大最近距离，同分取 pool 首行。

预算和 pool cutoff 正好落在同 fitness 一组时，两个策略可直接看到不同 elite 集合，影响公平对照。建议两者共享稳定 elite helper；若改变 FPS 历史顺序，应重新产生基线并记录策略版本。

### P1：等接收预算的承诺超过当前边界

局域 max_candidates=128，FPS 无该上限。129 个有限候选、budget=129：局域仅接收 128，FPS 接收 129。请求解析允许 batch_accept 超过 128（上限为 n_seeds × children_per_seed），不是仅内部非法调用。

即使 budget 小于 128，当 budget×top_pool_factor 大于 128 时，两者考虑的 pool 也不相同。需要统一候选池上限，或显式限制并披露局域策略的预算。对超上限 budget 静默少接收不符合“零增量填满同等预算”的文字承诺。

### P1：全空局域行批次不是完整端到端支持

evaluator.evaluate_batch 允许零长度结构行段，objective 对空行返回 0，selector 会以零增量填入结构。随后结构 archive 已 add，而 LocalEnvironmentArchive.add 拒绝 shape[0]==0，引擎抛 ValueError。

实际非空原子描述符通常应有行，真实计算输出触发频率未测量；但当前数据契约允许这一边界。应在修改 result/structure archive 之前验证，或明确定义全空局域批次跳过 local add，或者整体拒绝此类候选。

### P2：性能上限仅约束候选数

helper 逐行最近距离并反复 vstack，单结构 r 行最坏有 O(r²D) 距离工作和二次复制；每个选择步骤对剩余候选重做。selector 还对全部 atomic_values 做 scaling 和 archive 查询，包含不在 elite 里的候选。nearest_per_row 内部又会缩放一次，因此“一次 scaling pass”的注释不严格成立，但空间结果正确。

分块 kernel 避免全局 N×M 矩阵，不代表大原子结构的运行时间已经受控。本次未测性能，仅列源码风险。建议缓存 elite 的 scaled rows 和 archive mask，预分配 memory，增加大原子数性能门槛。

## 6. 最小复现案例

除指定 scaling 外，以下全部使用一维 raw 距离、archive reference=[0]、threshold=1、相同 fitness。结构值如未另指定就是每候选原子行的均值。

### 案例 A：同一候选内部，缩放导致过计数和欠计数

| reference / scaling | 一个候选的 atomic rows | 缩放后候选内距离 | selector 实际增量 | counter 返回 |
|---|---|---:|---:|---:|
| [-10,10] / standardized，center=0、scale=10 | [30,35] | 0.5 | 1 | **2** |
| [-0.1,0.1] / standardized，center=0、scale=0.1 | [0.3,0.45] | 1.5 | 2 | **1** |

两例所有行都真正远离 reference。差异来自候选内部 raw 去重；把两行拆成两个候选、依次计数，也有同样问题。

等价的单位变换案例：reference [-1,1]、候选 [3,3.5] 的 standardized 计数为 1；所有值×10 后 standardized 距离不变，计数却变为 2。

### 案例 B：改变候选顺序导致计数改变

候选身份 A=[3]，B=[3.75]，C=[4.5]。

| 输入候选顺序 | 局域选择身份顺序，budget=3 | strict count |
|---|---|---:|
| A,B,C | A,C,B | **2** |
| B,A,C | B,A,C | **1** |

第一行 A 被选后 C 距离 1.5，继续计数；第二行 B 被选后 A 和 C 均距 0.75，只能作为零增量填充。三者接受身份集合相同，计数仍不同。

直接计数相同 atomic_values=[3,3.75,4.5]、offsets=[0,1,2,3]：selected=[0,1,2] 返回 2，selected=[1,0,2] 返回 1。若只打乱存储但保持同一个身份访问顺序、同步重映射 selected 和 offsets，则当然不应变化；本例改变的是实际访问/选择顺序。

### 案例 C：改变同一结构原子行顺序导致计数改变

一个候选 [3,3.75,4.5] 计 2；同一个行集合重排为 [3.75,3,4.5] 计 1。结构均值相同，archive 判定也相同，因此不能用结构均值掩盖这项风险。

### 案例 D：archive-near 行不会排斥 novel——修复有效

先计 archive-near=[0.9]，再计 novel=[1.8]：计 1；候选次序逆转也计 1；放在同候选 [0.9,1.8] 仍计 1。

区别：near 行在本轮临时 memory 被过滤；如果其结构被接受，正式 archive 更新仍会存入该行，下一轮它会成为合法 archive 参照。这不是同轮污染复发。

### 案例 E：局域选择可以输给 FPS，且结构值确实是均值

三个候选原子行（每结构均为两个原子）：

- A=[3.75,-1]，结构均值 1.375；
- B=[3,1]，结构均值 2；
- C=[4.5,1]，结构均值 2.75。

-1 和 1 都恰在 archive threshold，不能计为 novel。budget=2，三个候选初始增量均为 1。

局域首选 A，剩余 B/C 增量都为 0；结构 tie-break 选择离 A 更远的 C，得到 [A,C]，strict count=**1**。

FPS centroid 最近的是 B，下一点离 B 最远的是 C，得到 [B,C]；3 与 4.5 相距 1.5，strict count=**2**。

此例不涉及缩放缺陷、不涉及 pool 截断、每个结构原子数相同，直接推翻“所有输入下局域策略不会输给 FPS”的外推。现有测试仅声称固定合成 benchmark 的验收结果，这一有限结论可以保留。

### 案例 F：两候选完全同分的排序差异

atomic rows=[3],[5]，结构值=[0],[2]，fitness=[1,1]，budget=1。局域返回索引 [0]，FPS 返回 [1]；完整反转输入后，两者仍分别返回 [0]/[1]，但对应身份交换。

这里结构值是显式给定用于隔离 tie-break 的特征，非原子均值。FPS 的两个点距 centroid 相等，因此选反转 pool 的第一项。

### 案例 G：跨轮只通过正式 archive 更新传递行

第一轮候选 [3,3.75] 的 strict count=1，只保留 3 进入临时 counted。若同轮继续遇到 4.5，可以计第二个环境；但正式 add 会保存 [3,3.75] 两行，下一轮 [4.5] 距 archive 的 3.75 为 0.75，计 0。

补充真实引擎两轮测试：每轮两结构、每结构两个相同 novel 行；第一轮 unique=1，第二轮重复行 unique=0。spy 记录 local add 仅两次，每次四行/两 entry；第一轮所有 query 看 atom_row_count=0，第二轮看 4。确认更新顺序正确，没有逐候选提前污染。

### 案例 H：空输入与 archive 区分

- fitness=[]、atomic shape=(0,1)、offsets=[0]：局域返回 []，count 返回 0。
- 三个候选都没有局域行：单函数返回零增量选择、count=0；端到端引擎会在 local add 抛 ValueError，结构 archive 已接受两项。
- 非空 reference、accepted_matrix=None：正常工作。
- reference shape=(0,1)：LocalEnvironmentArchive 构造立即 ValueError。这是现有契约，不应把“空 accepted archive”测试解释为真正空 reference 支持。

### 案例 I：strict 两侧边界

archive=0，单行 x 分别取 nextafter(1,0)、1、nextafter(1,+inf)：计数依次 0、0、1。

一个结构先有 2，第二行分别为 nextafter(3,0)、3、nextafter(3,+inf)：候选内计数依次 1、1、2。候选内部与已 counted 分支均使用 >，同一一维数据分块后语义一致。不要为了修缩放额外引入 epsilon 而改变严格边界。

### 案例 J：budget 超过 128

129 个一行候选、fitness 相同、budget=129、所有原子行两两间距 2：局域接受 128，FPS 接受 129，违反等接收数的广泛承诺。

## 7. 现有测试的具体不足与修复顺序

1. `test_local_strategy_reports_what_it_optimizes`（selection.py 106–122）注释声称增量总和等于严格指标，实际只断言 `unique > 0`。这是最直接的断言强度缺口。应与独立 scaled oracle 精确相等，并锁定尺度大/小两方向、候选内部/跨候选两分支。
2. strict、内部重复、near 阻挡回归主要是 raw。固定 robust 随机 benchmark 把双方结果又交给同一个缺陷计数器，不能证明指标定义正确。
3. 缺少候选/原子 permutation 回归。应明确哪些重排按契约应不变，哪些当前算法允许变，并按稳定身份核对，不能只看返回索引。
4. 缺少 elite 截断同分组、首选同分、后续结构距离同分的双方对照。统一排序后锁定 pool 成员和身份选择。
5. `test_archive_updates_once_per_accepting_round` 实际核对的是 structure_archive.size 与接受数量，并未 spy add 调用次数，且没有锁定 local add。本文补充的 spy 测试可弥补。
6. 增补重复跨轮/全部 raw 行进入 formal archive 的精确测试，明确被同轮去重丢弃的行下一轮依然可以作为 archive 参照。
7. 增补真正空候选、全空局域行引擎路径、空 reference 契约、budget=129 回归。
8. 修复顺序：先统一计数缩放并加强 oracle；再统一 elite tie-break/预算契约；处理全空批次状态更新；最后明确并版本化顺序语义和 benchmark 口径，补性能基准。

## 8. 可运行诊断代码

下方 15 个测试用于复现当前行为。缺陷测试刻意断言当前错误结果；修复实现后，应把这些断言改为正确的 scaled 结果/约定行为，不应长期把缺陷当作正常验收。

把代码保存为 `test_local_audit_diagnostics.py`，在已安装 NumPy、SciPy、pytest 的环境执行：

```bash
MDS_AUDIT_REPO=/absolute/path/to/MDescriptorStudio python -m pytest test_local_audit_diagnostics.py -q -p no:cacheprovider
```

代码中的两个引擎测试复用仓库已有测试 helper，需保留 tests 目录。

```python
import sys
import os
import json
from pathlib import Path
import numpy as np
REPO = Path(os.environ.get('MDS_AUDIT_REPO', '.')).resolve()
sys.path.insert(0, str(REPO / 'backend'))
from mdescriptor_studio_backend.analysis.sampling import fit_scaling, apply_scaling
from mdescriptor_studio_backend.generation.archive import LocalEnvironmentArchive
from mdescriptor_studio_backend.generation.engine import count_strict_unique_environments as count, select_local_incremental_batch as local, select_diverse_batch as fps


def archive(reference=(0.,), mode='raw'):
    x = np.array(reference, dtype=float)[:,None]
    s,_ = fit_scaling(x, mode)
    return LocalEnvironmentArchive(x,s)


def call(blocks, ar=None, threshold=1., fitness=None, budget=None):
    ar = archive() if ar is None else ar
    atom = np.array([v for b in blocks for v in b], dtype=float).reshape(-1,1)
    off = np.r_[0, np.cumsum([len(b) for b in blocks])]
    struct = np.array([np.mean(b) if len(b) else 0 for b in blocks])[:,None]
    f = np.ones(len(blocks)) if fitness is None else np.array(fitness)
    b = len(blocks) if budget is None else budget
    picked = local(f,struct,atom,off,b,local_archive=ar,threshold=threshold)
    return picked, count(picked,atom,off,threshold,ar), atom,off,struct


def oracle(selected, raw, offsets, threshold, ar):
    # Independent scalar loop: all comparisons in the frozen scaled space.
    rows = apply_scaling(ar.scaling, raw)
    memory=[]
    for i in selected:
        for j in range(int(offsets[i]), int(offsets[i+1])):
            if ar.nearest_per_row(raw[j:j+1])[0] <= threshold:
                continue
            if all(np.linalg.norm(rows[j]-k)>threshold for k in memory):
                memory.append(rows[j])
    return len(memory)


def test_raw_threshold_and_archive_near():
    assert call([[1.,2.,3.]])[1] == 1
    assert call([[3.,3.]])[1] == 1
    ar=archive()
    atom=np.array([[.9],[1.8]])
    off=np.array([0,1,2])
    assert count([0,1],atom,off,1.,ar)==1
    assert count([1,0],atom,off,1.,ar)==1
    assert call([[.9,1.8]])[1]==1


def test_scale_large_diagnostic():
    ar=archive((-10.,10.),'standardized')
    sel,n,rows,off,_=call([[30.,35.]],ar)
    assert n==2 and oracle(sel,rows,off,1.,ar)==1  # confirmed overcount


def test_scale_small_diagnostic():
    ar=archive((-.1,.1),'standardized')
    sel,n,rows,off,_=call([[.3,.45]],ar)
    assert n==1 and oracle(sel,rows,off,1.,ar)==2  # confirmed undercount


def test_candidate_order_changes_counts_and_selection():
    ar=archive()
    rows=np.array([[3.],[3.75],[4.5]])
    off=np.arange(4)
    assert count([0,1,2],rows,off,1.,ar)==2
    assert count([1,0,2],rows,off,1.,ar)==1
    p,n,*_=call([[3.],[3.75],[4.5]])
    q,m,*_=call([[3.75],[3.],[4.5]])
    assert p==[0,2,1] and n==2
    assert q==[0,1,2] and m==1


def test_atom_order_changes_counts():
    assert call([[3.,3.75,4.5]])[1]==2
    assert call([[3.75,3.,4.5]])[1]==1


def test_local_can_lose_to_fps_with_real_mean_pooling():
    ar=archive()
    selected,n,rows,off,struct=call([[3.75,-1.],[3.,1.],[4.5,1.]],ar,budget=2)
    baseline=fps(np.ones(3),struct,2)
    assert selected==[0,2] and n==1
    assert baseline==[1,2] and count(baseline,rows,off,1.,ar)==2


def test_tie_break_opposite_fitness_order():
    ar=archive()
    rows=np.array([[3.],[5.]])
    off=np.arange(3)
    f=np.ones(2)
    struct=np.array([[0.],[2.]])
    assert local(f,struct,rows,off,1,local_archive=ar,threshold=1.)==[0]
    assert fps(f,struct,1)==[1]
    assert local(f,struct[::-1],rows[::-1],off,1,local_archive=ar,threshold=1.)==[0]
    assert fps(f,struct[::-1],1)==[1]


def test_empty_candidates_and_archive_contract():
    ar=archive()
    x=np.empty((0,1)); off=np.array([0])
    assert local(np.empty(0),x,x,off,3,local_archive=ar,threshold=1.)==[]
    assert count([],x,off,1.,ar)==0
    assert call([[],[],[]])[1]==0
    assert ar.accepted_matrix is None  # empty accepted archive is supported
    try:
        LocalEnvironmentArchive(x,ar.scaling)
    except ValueError as e:
        assert 'non-empty' in str(e)
    else:
        raise AssertionError('empty reference archive unexpectedly supported')


def test_across_round_archive_contains_all_accepted_rows():
    ar=archive()
    rows=np.array([[3.],[3.75]])
    off=np.array([0,2])
    assert count([0],rows,off,1.,ar)==1
    ar.add(rows,[])
    next_rows=np.array([[4.5]])
    assert count([0],next_rows,np.array([0,1]),1.,ar)==0
    # Before formal update, the discarded 3.75 cannot repel 4.5:
    assert count([0,1],np.array([[3.],[3.75],[4.5]]),np.array([0,2,3]),1.,archive())==2


def test_local_tie_uses_structure_distance_then_rank():
    ar=archive()
    rows=np.array([[3.],[5.],[7.]])
    off=np.arange(4)
    p=local(np.array([3.,2.,1.]),np.array([[0.],[1.],[10.]]),rows,off,2,local_archive=ar,threshold=1.)
    assert p==[0,2]
    p=local(np.array([3.,2.,1.]),np.array([[0.],[10.],[10.]]),rows,off,2,local_archive=ar,threshold=1.)
    assert p==[0,1]


def test_nextafter_threshold_both_sides():
    ar=archive()
    for x,expected in [(np.nextafter(1.,0.),0),(1.,0),(np.nextafter(1.,np.inf),1)]:
        assert count([0],np.array([[x]]),np.array([0,1]),1.,ar)==expected
    for x,expected in [(np.nextafter(3.,0.),1),(3.,1),(np.nextafter(3.,np.inf),2)]:
        assert count([0],np.array([[2.],[x]]),np.array([0,2]),1.,ar)==expected


def test_scaling_invariance_failure():
    base=archive((-1.,1.),'standardized')
    big=archive((-10.,10.),'standardized')
    assert call([[3.,3.5]],base)[1]==1
    assert call([[30.,35.]],big)[1]==2


def test_budget_above_pool_cap_not_equal_to_fps():
    ar=archive()
    atom=np.arange(3.,261.,2.)[:,None]  # 129 candidates
    off=np.arange(130)
    f=np.ones(129)
    picked=local(f,atom,atom,off,129,local_archive=ar,threshold=1.)
    baseline=fps(f,atom,129)
    assert len(picked)==128 and len(baseline)==129


def test_engine_two_rounds_frozen_queries_and_one_update(monkeypatch):
    sys.path.insert(0, str(REPO / 'tests'))
    from test_generation_engine import _engine
    from mdescriptor_studio_backend.generation.models import Budget
    from mdescriptor_studio_backend.generation.objectives import CompositeObjective
    from mdescriptor_studio_backend.generation.constraints import build_constraints
    from mdescriptor_studio_backend.generation.evaluator import DescriptorEvaluation
    class ConstantEvaluator:
        def evaluate(self,candidates,*,return_atomic=False,control=None):
            n=len(candidates)
            return DescriptorEvaluation(np.full((n,3),3.),np.full((2*n,3),3.),np.arange(0,2*n+1,2))
    e=_engine(42,evaluator=ConstantEvaluator(),objective=CompositeObjective(structure_weight=0.,local_weight=1.,novelty_threshold=1.),budget=Budget(max_evaluations=100,max_accepted=4,max_generations=2),batch_accept=2)
    e.selection_strategy='local_incremental_maximin_v1'
    e.constraints=build_constraints({'min_distance_mode':'none'})
    ref=np.zeros((1,3));scaling,_=fit_scaling(ref,'raw')
    e.local_archive=LocalEnvironmentArchive(ref,scaling)
    events=[]
    original_query=e.local_archive.nearest_per_row
    original_add=e.local_archive.add
    def query(rows):
        events.append(('query',e.local_archive.atom_row_count))
        return original_query(rows)
    def add(rows,entries):
        events.append(('add',e.local_archive.atom_row_count,len(rows),len(entries)))
        return original_add(rows,entries)
    monkeypatch.setattr(e.local_archive,'nearest_per_row',query)
    monkeypatch.setattr(e.local_archive,'add',add)
    result=e.run()
    assert [r.unique_novel_environments for r in result.rounds]==[1,0]
    assert [r.accepted for r in result.rounds]==[2,2]
    assert [x for x in events if x[0]=='add']==[('add',0,4,2),('add',4,4,2)]
    first=events.index(('add',0,4,2));second=events.index(('add',4,4,2))
    assert all(x==('query',0) for x in events[:first])
    assert all(x==('query',4) for x in events[first+1:second])


def test_all_empty_atom_blocks_engine_raises_after_structure_update():
    sys.path.insert(0, str(REPO / 'tests'))
    from test_generation_engine import _engine
    from mdescriptor_studio_backend.generation.models import Budget
    from mdescriptor_studio_backend.generation.objectives import CompositeObjective
    from mdescriptor_studio_backend.generation.constraints import build_constraints
    from mdescriptor_studio_backend.generation.evaluator import DescriptorEvaluation
    class EmptyEvaluator:
        def evaluate(self,candidates,*,return_atomic=False,control=None):
            n=len(candidates)
            return DescriptorEvaluation(np.zeros((n,3)),np.empty((0,3)),np.zeros(n+1,dtype=int))
    e=_engine(42,evaluator=EmptyEvaluator(),objective=CompositeObjective(structure_weight=0.,local_weight=1.,novelty_threshold=1.),budget=Budget(max_evaluations=100,max_accepted=2,max_generations=1),batch_accept=2)
    e.selection_strategy='local_incremental_maximin_v1'
    e.constraints=build_constraints({'min_distance_mode':'none'})
    try:
        e.run()
    except ValueError as exc:
        assert 'accepted local-environment values must be a non-empty 2D matrix' in str(exc)
        assert e.structure_archive.size==2 and e.local_archive.atom_row_count==0
    else:
        raise AssertionError('expected current empty local update bug')

```
