import type { Pair } from "../i18n";

export interface AnalysisMethodGuide {
  title: Pair;
  theory: Pair;
  application: Pair;
}

/**
 * Short, product-facing explanations for the method selected in Analysis.
 * Keep these bilingual at the definition site so the guide does not depend on
 * a second mapping that can drift from the controls.
 */
export const ANALYSIS_METHOD_GUIDES: Readonly<Record<string, AnalysisMethodGuide>> = {
  "projection.pca": {
    title: { en: "Principal Component Analysis (PCA)", zh: "主成分分析（PCA）" },
    theory: { en: "PCA finds orthogonal directions that capture the largest variance in the descriptor matrix, then projects each sample onto the leading components. It is a linear reduction, so the axes are ordered and quantitatively interpretable in terms of explained variance.", zh: "PCA 在描述符矩阵中寻找能够解释最大方差的正交方向，再将样本投影到前几个主成分上。它是线性降维方法，坐标轴按解释方差排序，具有明确的定量含义。" },
    application: { en: "Use PCA to inspect global descriptor structure, remove or summarize redundant dimensions, and create a stable first view before clustering, outlier detection, or parameter comparison.", zh: "可用 PCA 检查描述符空间的整体结构，压缩或概括冗余维度，并在聚类、离群点检测或参数比较前建立稳定的初始视图。" },
  },
  "projection.umap": {
    title: { en: "Uniform Manifold Approximation and Projection (UMAP)", zh: "统一流形逼近与投影（UMAP）" },
    theory: { en: "UMAP builds a weighted nearest-neighbor graph and optimizes a low-dimensional graph whose local topology resembles the original descriptor space. It is nonlinear and usually preserves local neighborhoods better than global distances.", zh: "UMAP 构建加权近邻图，并优化一个低维图，使其局部拓扑尽量接近原始描述符空间。它是非线性的，通常比全局距离更重视局部邻域关系。" },
    application: { en: "Use UMAP to explore nonlinear manifolds, local families, and cluster boundaries in large descriptor sets. Treat distances between far-apart islands as qualitative rather than as a calibrated metric.", zh: "可用 UMAP 探索大规模描述符数据中的非线性流形、局部族群和簇边界。相距很远的岛状区域之间的距离应作定性参考，不宜当作校准后的度量。" },
  },
  "projection.tsne": {
    title: { en: "t-distributed Stochastic Neighbor Embedding (t-SNE)", zh: "t-SNE 随机邻域嵌入" },
    theory: { en: "t-SNE converts pairwise neighborhood similarities into probabilities and chooses a low-dimensional embedding whose heavy-tailed similarities match them. The optimization emphasizes preserving nearby relationships and can reshape global geometry.", zh: "t-SNE 将样本间的邻域相似度转换为概率，并寻找低维嵌入，使其重尾相似度尽量匹配原始关系。优化重点是保留近邻关系，也可能改变全局几何结构。" },
    application: { en: "Use t-SNE for visual inspection of local groups and possible sub-populations. Do not compare axis values or island sizes quantitatively; use PCA or UMAP when global structure or repeatability matters.", zh: "可用 t-SNE 目视检查局部群组和潜在子群体。不要定量比较坐标值或岛屿大小；若关注全局结构或重复性，优先使用 PCA 或 UMAP。" },
  },

  "similarity.query": {
    title: { en: "Query-neighbor similarity", zh: "查询近邻相似度" },
    theory: { en: "For one query sample, the method ranks descriptor vectors by a selected distance or similarity measure. The nearest neighbors are the samples that are most alike under the current preprocessing, metric, and granularity.", zh: "该方法针对一个查询样本，依据选定的距离或相似度度量对描述符向量排序。在当前预处理、度量和粒度下，最近邻就是最相似的样本。" },
    application: { en: "Use it to retrieve structural analogues, inspect nearest training examples, diagnose a query that lies outside the learned space, or trace a suspicious sample back to representative structures.", zh: "可用它检索结构类似物、查看最近的训练样本、诊断脱离已学习空间的查询样本，或将可疑样本追溯到代表性结构。" },
  },
  "similarity.all_neighbors": {
    title: { en: "All-neighbor graph", zh: "全近邻图" },
    theory: { en: "Each sample is connected to its k nearest descriptor neighbors, producing a sparse graph that summarizes local connectivity rather than every pairwise relationship.", zh: "该方法为每个样本连接 k 个最近的描述符近邻，形成一个概括局部连通性而非全部两两关系的稀疏图。" },
    application: { en: "Use the graph to inspect local topology, identify disconnected regions or bridges between families, and provide neighborhood structure for downstream clustering or manifold exploration.", zh: "可用该图检查局部拓扑，发现不连通区域或不同族群之间的桥接样本，并为后续聚类或流形探索提供邻域结构。" },
  },
  "similarity.pairwise": {
    title: { en: "Pairwise similarity matrix", zh: "两两相似度矩阵" },
    theory: { en: "The method evaluates a distance or similarity for every selected sample pair and arranges the results as a symmetric matrix. Block patterns, diagonals, and extreme values reveal the geometry of the set.", zh: "该方法计算选定样本之间每一对的距离或相似度，并排列为对称矩阵。矩阵中的块状结构、对角线和极值可以揭示数据集几何。" },
    application: { en: "Use it to find near-duplicates, compare groups, inspect redundancy, and verify whether a descriptor separates structures as expected. Large matrices are sampled for display but remain bounded artifacts on the backend.", zh: "可用它查找近重复样本、比较群组、检查冗余，并验证描述符是否按预期区分结构。大矩阵在界面中有界显示，完整结果仍由后端工件保存。" },
  },

  "cluster.kmeans": {
    title: { en: "K-Means clustering", zh: "K-Means 聚类" },
    theory: { en: "K-Means alternates between assigning samples to the nearest centroid and recomputing centroids to minimize within-cluster squared distance. The chosen number of clusters is an explicit modeling assumption.", zh: "K-Means 交替执行“分配到最近质心”和“重新计算质心”，以最小化簇内平方距离。簇数是需要显式指定的建模假设。" },
    application: { en: "Use it to partition descriptor space into compact, approximately spherical regimes and to obtain a small set of centroids or representative regions for inspection and sampling.", zh: "可用它将描述符空间划分为紧凑、近似球形的区域，并得到用于检查和采样的质心或代表性区域。" },
  },
  "cluster.dbscan": {
    title: { en: "DBSCAN clustering", zh: "DBSCAN 聚类" },
    theory: { en: "DBSCAN defines clusters through density reachability using a neighborhood radius and a minimum number of points. Sparse samples can be labeled as noise, and the number of clusters does not need to be fixed in advance.", zh: "DBSCAN 通过邻域半径和最少样本数，以密度可达性定义簇。稀疏样本可以标记为噪声，且不必预先固定簇的数量。" },
    application: { en: "Use it for irregularly shaped groups, disconnected dense regions, and explicit noise discovery. Scale the descriptor features first and interpret the result relative to the radius and density settings.", zh: "可用它发现不规则形状的群组、彼此分离的高密度区域和显式噪声。使用前应先处理特征尺度，并结合半径和密度参数解释结果。" },
  },
  "cluster.hdbscan": {
    title: { en: "HDBSCAN clustering", zh: "HDBSCAN 聚类" },
    theory: { en: "HDBSCAN builds a hierarchy of density-connected components over changing density thresholds, then selects stable branches. It can separate groups with different densities and mark weakly supported samples as noise.", zh: "HDBSCAN 在变化的密度阈值下构建密度连通分量层次，并选择稳定分支。它能够区分密度不同的群组，也能将支持不足的样本标记为噪声。" },
    application: { en: "Use it when descriptor families have unequal density or when a hierarchical density view is more informative than a fixed-k partition. Check cluster stability before treating small groups as meaningful.", zh: "当描述符族群的密度不均匀，或层次密度视图比固定簇数划分更有意义时，可使用 HDBSCAN。将小群组解释为真实类别前，应先检查其稳定性。" },
  },
  "cluster.agglomerative": {
    title: { en: "Agglomerative clustering", zh: "凝聚层次聚类" },
    theory: { en: "Agglomerative clustering starts with one cluster per sample and repeatedly merges the closest pair according to a linkage rule, yielding a hierarchy that can be cut at a chosen level.", zh: "凝聚层次聚类从每个样本一个簇开始，依据链接规则反复合并最近的簇，形成可在指定层级切分的聚类树。" },
    application: { en: "Use it to inspect nested similarity structure and to compare descriptor families at several resolutions. Results depend on the distance scale and linkage rule, so report those choices with the clusters.", zh: "可用它检查嵌套的相似性结构，并在多个分辨率下比较描述符族群。结果受距离尺度和链接规则影响，应随聚类结果一并记录这些选择。" },
  },

  "outlier.lof": {
    title: { en: "Local Outlier Factor (LOF)", zh: "局部离群因子（LOF）" },
    theory: { en: "LOF compares a sample's local density with the density of its nearest neighbors. A value above the neighborhood baseline indicates that the sample is locally sparser than its surroundings.", zh: "LOF 将样本的局部密度与其最近邻的密度比较。高于邻域基准的值表示该样本相对于周围样本更稀疏。" },
    application: { en: "Use LOF to find anomalies inside uneven-density descriptor regions where a single global distance threshold would be misleading. Inspect the chosen k because it defines the local context.", zh: "可用 LOF 在密度不均匀的描述符区域中发现异常，此时统一的全局距离阈值可能会误判。需要重点检查 k，因为它决定了局部上下文。" },
  },
  "outlier.knn": {
    title: { en: "k-nearest-neighbor outlier score", zh: "k 近邻离群点得分" },
    theory: { en: "The score summarizes how far a sample lies from its k nearest descriptor neighbors. Samples in sparse regions receive larger scores because they require a longer descriptor-space distance to find support.", zh: "该得分概括样本到其 k 个最近描述符近邻的距离。稀疏区域中的样本需要更大的描述符空间距离才能找到支持，因此得分更高。" },
    application: { en: "Use it as a simple, interpretable novelty screen for quality control, manual inspection, and candidate selection before expensive recomputation or labeling.", zh: "可用它作为简单且易解释的新颖性筛查，用于质量控制、人工检查，以及昂贵的重新计算或标注前的候选选择。" },
  },
  "outlier.isolation_forest": {
    title: { en: "Isolation Forest", zh: "孤立森林" },
    theory: { en: "Isolation Forest repeatedly partitions the feature space with randomized splits. Anomalies tend to be isolated in fewer splits than dense, typical samples and therefore receive higher anomaly scores.", zh: "孤立森林通过随机切分反复划分特征空间。异常样本通常比密集的典型样本更快被孤立，因此得到更高的异常得分。" },
    application: { en: "Use it for a fast global anomaly screen on larger descriptor sets, especially when a purely local neighborhood score is too expensive or too sensitive to local density.", zh: "可用它对较大的描述符数据集进行快速全局异常筛查，尤其适合单纯的局部邻域得分过于昂贵或对局部密度过敏的情况。" },
  },
  "outlier.mahalanobis": {
    title: { en: "Mahalanobis distance", zh: "马氏距离" },
    theory: { en: "Mahalanobis distance measures how far a sample is from the descriptor mean after accounting for feature covariance. Correlated directions are discounted, while deviations along low-variance directions are emphasized.", zh: "马氏距离在考虑特征协方差后，衡量样本到描述符均值的距离。相关方向的重复偏离会被折减，而低方差方向上的偏离会被突出。" },
    application: { en: "Use it for ellipsoidal, covariance-aware anomaly detection when the descriptor distribution is reasonably summarized by a stable mean and covariance. Watch for insufficient samples or ill-conditioned covariance.", zh: "当描述符分布可以由稳定的均值和协方差合理概括时，可用它进行考虑协方差的椭球形异常检测。需要注意样本不足或协方差病态的情况。" },
  },

  "sampling.fps": {
    title: { en: "Farthest Point Sampling (FPS)", zh: "最远点采样（FPS）" },
    theory: { en: "FPS greedily adds the sample farthest from the selected set under the descriptor metric, starting from the structure closest to the descriptor-space centroid. Sampling runs in the full scaled descriptor space (robust scaling by default), and the coverage radius and residual statistics report how completely the selection spans the data.", zh: "FPS 在每一步贪心地加入相对于已选集合最远的样本，并从距描述符空间中心最近的结构出发。采样在完整的缩放描述符空间中进行（默认稳健缩放），覆盖半径与残差统计反映选中集对数据的覆盖程度。" },
    application: { en: "Use FPS to build a diverse initial training subset, reduce redundant labeling, or cover a descriptor domain with a fixed sample budget. With an existing dataset as warm start it targets the regions that dataset covers worst; a minimum descriptor distance can stop the run once no candidate adds new information.", zh: "可用 FPS 构建多样化的初始训练子集，减少冗余标注，或在固定样本预算下覆盖描述符域。指定已有数据集进行热启动时，FPS 会优先挑选该数据集覆盖最差的区域；设置最小描述符距离后，当没有候选再带来新信息时可提前停止。" },
  },
  "sampling.grouped_fps": {
    title: { en: "Grouped FPS", zh: "分组 FPS" },
    theory: { en: "Grouped FPS runs an independent farthest-point pass inside each element-set group (C, C-O, C-O-Si, …) and allocates the budget by a √N_g share, giving every group at least one sample when the target covers them all. Global FPS can let the most abundant composition class dominate the selection; √N weighting keeps minority classes visible without over-representing them.", zh: "分组 FPS 在每个元素组合（C、C-O、C-O-Si 等）内部独立执行最远点采样，并按 √N_g 分配样本预算；当目标数足以覆盖所有组时，每组至少获得一个样本。全局 FPS 容易让数量最多的组分主导选择，而 √N 权重在不过度放大小组的同时保证少数组分不被淹没。" },
    application: { en: "Use it for multi-element datasets whose composition classes differ greatly in size, or when the training set must retain representatives of every chemistry present. The quota preview lists the per-element-set budget before the run; the coverage statistics are computed over the merged selection.", zh: "适用于各组分数量差异很大的多元素数据集，或训练集必须覆盖每一种化学组成的场景。运行前的配额预览会列出各元素组合的样本预算；覆盖度统计基于合并后的整体选择计算。" },
  },
  "sampling.composite_fps": {
    title: { en: "Composite Feature Sampling", zh: "复合特征采样" },
    theory: { en: "Composite sampling builds one Euclidean space from several named feature blocks — descriptor, descriptor summary, lattice, composition, energy, force statistics — where each block is scaled on its own (robust by default) and divided by √D so a wide block cannot outvote a narrow one. The weight then sets how much each block matters, and markers are selected to be representative in the combined space rather than in the descriptor alone.", zh: "复合采样把若干具名特征块（描述符、描述符汇总、晶格、组分、能量、受力统计）组合成一个欧氏空间：每块先独立缩放（默认稳健缩放），再除以 √D，使宽块无法压制窄块。权重决定各块的相对重要性，选点则在合并空间而非仅描述符空间中保持代表性。" },
    application: { en: "Use it when a training set must also cover lattice geometry, composition, or energy range, not just descriptor similarity — the case NepTrainKit handles with a physics FPS. Unlike that fixed recipe, the blocks here are chosen explicitly, so the sampling space is always something you asked for and can be reported. Physical blocks require structure granularity and the corresponding metadata; a missing block fails the run instead of being silently dropped.", zh: "当训练集除了描述符相似性外还必须覆盖晶格几何、组分或能量范围时使用（这正是 NepTrainKit 用 physics FPS 处理的情形）。与那套固定配方不同，这里的特征块由用户显式选择，因此采样空间始终是用户要求的、可被报告的内容。物理块要求结构粒度与相应元数据；缺少某块会直接报错，而不是被静默丢弃。" },
  },
  "sampling.coverage_target_fps": {
    title: { en: "Target-coverage FPS", zh: "目标覆盖度 FPS" },
    theory: { en: "Instead of a fixed sample count, the budget is expressed as a coverage target. Sampling stops once the explained-spread fraction R² = 1 − Σ d_i² / Σ ||x_i − x̄||² reaches the requested value, so the data decides how many structures are enough. R² is monotone in the number of picks, which is what makes it a usable stopping rule.", zh: "样本预算不再固定为数量，而表示为覆盖度目标。当被解释的离散度 R² = 1 − Σ d_i² / Σ ||x_i − x̄||² 达到设定值时采样停止，由数据决定需要多少结构。R² 随选点数单调递增，这正是它能作为停止判据的原因。" },
    application: { en: "Use it to answer “how many samples do I actually need?”: run with a generous maximum and a 95% target, then watch where the R² and coverage-radius curves flatten. A target that is reached with very few samples means the dataset is highly redundant; a curve still climbing at the maximum means the budget, not the data, was the binding constraint.", zh: "用于回答“到底需要多少样本？”：把最大样本数设得宽松、目标覆盖度设为 95%，观察 R² 与覆盖半径曲线在哪里变平。很少样本就达标说明数据集高度冗余；达到上限时曲线仍在上升则说明限制来自预算而非数据。" },
  },
  "sampling.novelty_fps": {
    title: { en: "Novelty-aware FPS", zh: "新颖性感知 FPS" },
    theory: { en: "Novelty-aware FPS combines distance from a reference descriptor set with farthest-point diversity. It favors query samples that are both insufficiently represented by the reference and distinct from one another.", zh: "新颖性感知 FPS 将样本到参考描述符集的距离与最远点多样性结合起来，优先选择参考集覆盖不足且彼此差异较大的 query 样本。" },
    application: { en: "Use it for dataset expansion and active collection when the goal is to add novel structures without spending the budget on near-duplicates.", zh: "当目标是在扩充数据集时加入新颖结构、避免预算被近重复样本消耗，可使用该方法。" },
  },
  "sampling.uncertainty_diversity": {
    title: { en: "Uncertainty + diversity acquisition", zh: "不确定性 + 多样性采样" },
    theory: { en: "This method uses descriptor-space kNN extrapolation as an uncertainty proxy and combines it with a diversity term. The uncertainty is geometric extrapolation, not predictive variance from a trained model.", zh: "该方法使用描述符空间中的 kNN 外推作为不确定性代理，并与多样性项结合。不确定性表示几何外推程度，不是训练模型输出的预测方差。" },
    application: { en: "Use it to prioritize informative query structures for labeling or recalculation while preventing selected candidates from collapsing into one local region.", zh: "可用它优先选择值得标注或重新计算的 query 结构，同时避免候选样本集中在同一个局部区域。" },
  },
  "sampling.random": {
    title: { en: "Random sampling", zh: "随机采样" },
    theory: { en: "Random sampling selects samples uniformly under the requested granularity, giving every eligible item the same selection probability.", zh: "随机采样在指定粒度下均匀选择样本，使每个符合条件的对象具有相同的被选概率。" },
    application: { en: "Use it as a low-cost baseline, for unbiased control subsets, or to quantify how much a geometry-aware strategy improves over chance.", zh: "可用它作为低成本基线，构造无偏的对照子集，或量化几何感知策略相对于随机机会的提升。" },
  },
  "sampling.stratified": {
    title: { en: "Stratified sampling", zh: "分层采样" },
    theory: { en: "Stratified sampling divides the eligible data into defined strata and samples within each stratum, preserving the intended composition more reliably than one global random draw.", zh: "分层采样先将数据划分为预定义层，再在各层内部抽样，比一次全局随机抽样更可靠地保留目标组成。" },
    application: { en: "Use it when rare elements, structure classes, trajectory segments, or other known groups must remain represented in a fixed-size subset.", zh: "当稀有元素、结构类别、轨迹片段或其他已知群组必须在固定大小的子集中保持代表性时，可使用该方法。" },
  },
  "sampling.cluster_representative": {
    title: { en: "Cluster-representative sampling", zh: "簇代表采样" },
    theory: { en: "The method first identifies descriptor-space groups and then selects samples close to their centers or medoids. It trades extreme coverage for compact representatives of common regions.", zh: "该方法先识别描述符空间中的群组，再选择接近群组中心或 medoid 的样本。它以牺牲部分极端覆盖为代价，获得常见区域的紧凑代表。" },
    application: { en: "Use it to construct interpretable summaries, seed a balanced review set, or reduce a clustered dataset while keeping one or more examples from each regime.", zh: "可用它构造易解释的摘要，生成均衡的审查集，或在保留各个区域样本的同时压缩聚类数据集。" },
  },
  "sampling.per_element": {
    title: { en: "Per-element sampling", zh: "按元素采样" },
    theory: { en: "Per-element sampling allocates selection across chemical element groups so that element-specific environments are not hidden by the most abundant species.", zh: "按元素采样在化学元素群组之间分配样本，使元素特有的局部环境不会被数量最多的元素掩盖。" },
    application: { en: "Use it for multi-element datasets where minority species or element-specific descriptor environments must be represented in training and validation subsets.", zh: "对于多元素数据集，当少数元素或元素特有的描述符环境必须出现在训练和验证子集中时，可使用该方法。" },
  },

  "coverage.coverage": {
    title: { en: "Nearest-reference coverage", zh: "最近参考覆盖度" },
    theory: { en: "For every query sample, the method measures the distance to its nearest reference descriptor and summarizes the distribution with robust thresholds such as q95 and q99.", zh: "该方法为每个 query 样本计算其到最近 reference 描述符的距离，并使用 q95、q99 等稳健阈值概括距离分布。" },
    application: { en: "Use it to decide whether a validation or incoming dataset is supported by the reference set, quantify extrapolation risk, and find structures that need new labels.", zh: "可用它判断验证集或新数据是否被参考集支持，量化外推风险，并找出需要新增标注的结构。" },
  },
  "coverage.overlap": {
    title: { en: "Train / test overlap", zh: "训练 / 测试重叠" },
    theory: { en: "The method compares each query sample with the reference set and classifies its nearest distance into near-duplicate, highly similar, or independent regions.", zh: "该方法将每个 query 样本与 reference 集比较，并按最近距离将其归类为近重复、高度相似或相互独立区域。" },
    application: { en: "Use it to audit train/test leakage, identify memorization-friendly duplicates, and understand whether an evaluation set tests interpolation or genuine extrapolation.", zh: "可用它审查训练集与测试集泄漏，发现容易导致记忆化的重复样本，并判断评估集测试的是插值能力还是真正的外推能力。" },
  },

  "compare.geometry": {
    title: { en: "Descriptor geometry comparison", zh: "描述符几何对比" },
    theory: { en: "After aligning sample identities, the method compares pairwise distances, neighborhood overlap, and low-dimensional topology between two descriptor spaces. Agreement means similar sample ordering, not identical feature values.", zh: "在对齐样本身份后，该方法比较两个描述符空间的两两距离、邻域重叠和低维拓扑。一致性表示样本排序相近，并不表示特征值完全相同。" },
    application: { en: "Use it to evaluate whether a new descriptor preserves useful structure, compare parameter variants, and identify which regions of the dataset are represented differently.", zh: "可用它评估新描述符是否保留了有用结构，比较参数变体，并定位数据集中表示方式差异最大的区域。" },
  },
  "compare.mantel": {
    title: { en: "Mantel permutation test", zh: "Mantel 置换检验" },
    theory: { en: "The Mantel test correlates two aligned pairwise distance matrices and estimates a two-sided p-value by repeatedly permuting sample identities. The null distribution tests whether the observed matrix association exceeds chance.", zh: "Mantel 检验计算两个已对齐两两距离矩阵的相关性，并通过反复置换样本身份估计双侧 p 值。零分布用于检验观测到的矩阵关联是否超过随机水平。" },
    application: { en: "Use it for a formal significance check when comparing descriptor geometries or another pairwise distance representation. Report the metric, alignment rule, and permutation count with the statistic.", zh: "当需要正式检验两个描述符几何或其他两两距离表示的关联显著性时，可使用该方法。应同时报告度量、对齐规则和置换次数。" },
  },

  "local.local_diversity": {
    title: { en: "Local environment diversity", zh: "局部环境多样性" },
    theory: { en: "The method builds atom-level neighbor environments from coordinates and periodic images, then measures descriptor diversity, coordination, and local neighborhood structure under the selected cutoff.", zh: "该方法根据坐标和周期镜像构建原子级近邻环境，再在指定截断半径下衡量描述符多样性、配位数和局部邻域结构。" },
    application: { en: "Use it to find chemically unusual local environments, compare element-specific coordination patterns, and diagnose whether a descriptor resolves local structure at the chosen cutoff.", zh: "可用它发现化学上少见的局部环境，比较不同元素的配位模式，并诊断描述符是否能在指定截断半径下分辨局部结构。" },
  },

  "kernel.rbf": {
    title: { en: "RBF kernel diagnostics", zh: "RBF 核函数诊断" },
    theory: { en: "The radial-basis-function kernel converts descriptor distances into smoothly decaying similarities. Its kernel matrix and eigenvalue spectrum expose the effective geometry and rank of the nonlinear representation.", zh: "径向基函数（RBF）核将描述符距离转换为平滑衰减的相似度。核矩阵及其特征值谱可以揭示非线性表示的有效几何和秩。" },
    application: { en: "Use it to inspect nonlinear similarity, select a useful kernel scale, and identify whether samples are redundant or whether the kernel representation is effectively high-rank.", zh: "可用它检查非线性相似性，选择合适的核尺度，并判断样本是否冗余或核表示是否具有较高的有效秩。" },
  },
  "kernel.linear": {
    title: { en: "Linear kernel diagnostics", zh: "线性核函数诊断" },
    theory: { en: "The linear kernel is the inner product between descriptor vectors. Its matrix summarizes linear similarity and provides a direct baseline for more expressive nonlinear kernels.", zh: "线性核是描述符向量之间的内积。其矩阵概括线性相似性，可作为更复杂非线性核的直接基线。" },
    application: { en: "Use it to check whether the descriptor already has a useful linear geometry, inspect dominant directions, and establish a simple baseline for kernel-based models.", zh: "可用它检查描述符是否已经具有有用的线性几何，观察主导方向，并为基于核的模型建立简单基线。" },
  },
  "kernel.cosine": {
    title: { en: "Cosine kernel diagnostics", zh: "余弦核函数诊断" },
    theory: { en: "The cosine kernel compares the angle between descriptor vectors, making similarity less sensitive to their overall magnitude and more sensitive to directional composition.", zh: "余弦核比较描述符向量之间的夹角，对整体幅值不那么敏感，而更关注向量的方向组成。" },
    application: { en: "Use it when descriptor direction is more meaningful than magnitude, for example when scale differences should not dominate similarity or retrieval.", zh: "当描述符方向比幅值更有意义，或不希望尺度差异主导相似度和检索时，可使用余弦核。" },
  },
  "kernel.polynomial": {
    title: { en: "Polynomial kernel diagnostics", zh: "多项式核函数诊断" },
    theory: { en: "The polynomial kernel applies a degree-controlled nonlinear transformation to descriptor inner products, allowing interactions between features to contribute to similarity.", zh: "多项式核对描述符内积施加由阶数控制的非线性变换，使特征之间的交互可以参与相似度计算。" },
    application: { en: "Use it to inspect moderate-order nonlinear interactions and compare their effective rank with linear or RBF representations before choosing a model family.", zh: "可用它检查中等阶数的非线性交互，并在选择模型族之前，将其有效秩与线性核或 RBF 表示进行比较。" },
  },

  "overview.feature_variance": {
    title: { en: "Feature variance", zh: "特征方差" },
    theory: { en: "Feature variance uses population variance (ddof=0) to measure change in each descriptor dimension. Normalized variance is Var(feature) divided by the maximum variance in the current dataset and is only for within-dataset comparison; absolute variance remains sensitive to feature scale and outliers. Constant means range ≤ 1e-12, Near-zero means normalized variance < 1e-4, Low variation means 1e-4–<1e-2, and Active means ≥1e-2. These are relative screening thresholds, not universal validity criteria. The detail box plot uses Tukey whiskers (Q1/Q3 ± 1.5 IQR), while the histogram and KDE share count scale; KDE is density × finite sample count × bin width.", zh: "特征方差使用总体方差（ddof=0）衡量每个描述符维度的变化程度。归一化方差等于特征方差除以当前数据集中的最大方差，仅用于数据集内部比较；绝对方差仍受特征尺度和异常值影响。常量定义为极差 ≤ 1e-12，近零方差为归一化方差 < 1e-4，低变化为 1e-4–<1e-2，活跃为 ≥1e-2。这些是相对筛查阈值，不是普适的有效性标准。详情箱线图使用 Tukey 须（Q1/Q3 ± 1.5 IQR），直方图与 KDE 使用一致的计数尺度；KDE = 密度 × 有限样本数 × bin 宽度。" },
    application: { en: "Use the overview to inspect the full variance distribution, find Constant/Near-zero dimensions, and open a feature detail panel with histogram, count-scaled KDE, Tukey box plot, and complete statistics. The Std/Robust sigma ratio is a heuristic: <1.5 is within the usual baseline, 1.5–2 suggests possible skew or a long tail, and >2 is long-tail/outlier-sensitive; it is not a formal outlier test. When the positive variance span reaches 100×, the coordinate scale defaults to Log; zero-variance features are omitted from that axis. Treat low variation as a dataset diagnostic, not proof that a descriptor is invalid.", zh: "可在总览中检查完整方差分布，发现常量/近零方差维度，并打开特征详情查看直方图、计数尺度 KDE、Tukey 箱线图和完整统计量。标准差/稳健 σ 比值是启发式诊断：<1.5 处于通常基线内，1.5–2 表示可能偏斜或存在长尾，>2 表示对长尾/异常值敏感；它不是正式的异常值检验。当正方差跨度达到 100 倍时，坐标尺度默认使用对数；零方差特征不会显示在该坐标轴上。低变化是数据集诊断信号，不应直接解释为描述符无效。" },
  },
  "overview.feature_correlation": {
    title: { en: "Feature correlation", zh: "特征相关性" },
    theory: { en: "Feature correlation measures pairwise association between descriptor dimensions. Pearson captures linear dependence, while Spearman captures monotonic rank dependence. Strong absolute correlation suggests redundant information under the selected method.", zh: "特征相关性衡量描述符维度之间的两两关联。Pearson 用于刻画线性依赖，Spearman 用于刻画单调秩依赖；在所选方法下，绝对相关性较强通常意味着信息冗余。" },
    application: { en: "Use it to diagnose redundant descriptor dimensions, guide feature compression, and understand which parts of a descriptor respond together across the dataset.", zh: "可用它诊断冗余描述符维度，辅助特征压缩，并了解描述符的哪些部分会在数据集中协同变化。" },
  },
  "overview.effective_dimension": {
    title: { en: "Effective dimension", zh: "有效维度" },
    theory: { en: "This diagnostic applies PCA to the descriptor matrix after the selected preprocessing. The PR effective dimension is (sum of eigenvalues)^2 divided by the sum of squared eigenvalues, so it can be fractional; k90, k95, and k99 are separate integer counts of principal components needed to reach the corresponding cumulative explained-variance targets.", zh: "该诊断先对选定预处理后的描述符矩阵进行 PCA。PR 有效维度等于特征值和的平方除以特征值平方和，因此可以是小数；k90、k95、k99 则是达到相应累计解释方差目标所需的主成分整数个数，两者不是同一个量。" },
    application: { en: "Use it to compare descriptor complexity and inspect whether variance is concentrated in a compact linear representation. Standardized PCA reports a correlation-matrix basis, while centered PCA reports a covariance-matrix basis; thresholds describe variance compression, not a model-validated optimal component count.", zh: "可用它比较描述符复杂度，检查方差是否集中在紧凑的线性表示中。标准化 PCA 对应相关矩阵基准，中心化 PCA 对应协方差矩阵基准；这些阈值描述的是方差压缩程度，不是经过模型验证的最优主成分数。" },
  },
  "overview.property_correlation": {
    title: { en: "Property information analysis", zh: "属性信息分析" },
    theory: { en: "This module separates three questions: whether the full descriptor predicts the property out of fold, which individual dimensions show Pearson, Spearman, or mutual-information association, and whether absolute OOF error increases with distance to each held-out sample's training-fold kNN manifold. Residual is defined as prediction minus truth.", zh: "该模块区分三个问题：完整描述符能否在折外预测属性、哪些单维特征具有 Pearson、Spearman 或互信息关联，以及绝对 OOF 误差是否随留出样本到其训练折 kNN 流形的距离增大。残差定义为预测值减真实值。" },
    application: { en: "Use the Ridge CV result as evidence of predictive information rather than generic correlation. Compare it with the training-fold mean baseline, inspect signed and nonlinear feature associations, then use the adjustable sparse and OOD-like distance percentiles to locate coverage gaps or possible representation degeneracy.", zh: "Ridge 交叉验证结果应解读为描述符具有预测信息，而不是笼统的“高度相关”。先与训练折均值基线比较，再检查带符号与非线性特征关联，最后用可调的稀疏区和类 OOD 距离分位数定位覆盖不足或可能的表示退化。" },
  },
  "overview.trajectory": {
    title: { en: "Descriptor trajectory", zh: "描述符轨迹" },
    theory: { en: "Trajectory analysis keeps the dataset order and reports three distances per frame: the step distance d(t) = ||x(t) - x(t-1)||, the distance to the reference frame r(t) = ||x(t) - x(0)||, and the cumulative path length L(t) = sum of d. Only the step distance detects transitions, because a cumulative curve grows monotonically by construction. Transitions are steps above a threshold computed in the original descriptor space: MAD (median + k·1.4826·MAD) is the robust default, z-score (mean + kσ) is sensitive to a single dominant jump, and percentile flags a fixed top fraction of frames. The PCA panel is a visualization only, so PC1 and PC2 report their explained variance.", zh: "轨迹分析保留数据集顺序，并为每帧给出三种距离：步进距离 d(t) = ||x(t) − x(t−1)||、相对参考帧的距离 r(t) = ||x(t) − x(0)||，以及累计路径长度 L(t) = Σd。只有步进距离可用于检测跃迁，因为累计曲线按定义单调增长。跃迁定义为在原始描述符空间中超过阈值的步进：MAD（中位数 + k·1.4826·MAD）为稳健默认方法，Z 分数（均值 + kσ）会被单个主导跃迁抬高，分位数方法则固定标记排名最靠前的若干帧。PCA 面板仅用于可视化，因此 PC1 与 PC2 会标出各自的解释方差。" },
    application: { en: "Use it to answer whether the descriptor evolved continuously or jumped, when and how strongly it jumped, and whether the trajectory resampled a known region or drifted into a new one. The frame range, sampling interval, and coloring only change what is displayed; the threshold and its statistics always refer to the full trajectory.", zh: "可用它回答：描述符是连续演化还是发生突变、突变发生在何时且强度多大、整条轨迹是在已有区域反复采样还是漂移进入新区域。帧范围、采样间隔与着色方式只改变显示内容，阈值及其统计量始终基于整条轨迹。" },
  },
  "overview.drift": {
    title: { en: "Dataset drift", zh: "数据集漂移" },
    theory: { en: "Dataset drift summarizes how far query samples move from the reference descriptor distribution, using nearest-reference distances and reference-derived quantile thresholds.", zh: "数据集漂移概括 query 样本偏离 reference 描述符分布的程度，使用最近参考距离和由 reference 推导的分位数阈值。" },
    application: { en: "Use it to monitor incoming data, validate train/validation splits, and identify domain-shifted structures before relying on model or descriptor behavior.", zh: "可用它监测新数据，验证训练/验证划分，并在依赖模型或描述符行为前发现发生域偏移的结构。" },
  },
  "overview.sensitivity": {
    title: { en: "Parameter sensitivity", zh: "参数敏感性" },
    theory: { en: "Parameter sensitivity compares completed runs of the same descriptor under different settings, measuring changes in descriptor deltas or sample-distance ordering while preserving sample identity.", zh: "参数敏感性比较同一描述符在不同参数下的已完成计算，在保持样本身份的前提下衡量描述符变化量或样本距离排序的变化。" },
    application: { en: "Use it to find stable parameter ranges, identify settings that materially alter dataset geometry, and choose defaults before training or large-scale recomputation.", zh: "可用它寻找稳定的参数范围，识别会显著改变数据集几何的设置，并在训练或大规模重新计算前选择默认参数。" },
  },
  "overview.perturbation_sensitivity": {
    title: { en: "Structural perturbation sensitivity", zh: "结构扰动敏感性" },
    theory: { en: "The descriptor is recomputed after a seeded sweep of controlled atomic jitter or isotropic strain. Every sweep step rebuilds the selected structures and recomputes the descriptor, so the cost grows with structures x amplitudes. The run therefore samples an evenly spaced subset of the dataset instead of every frame and reports how many structures it used out of how many were available. The response curve measures local continuity and amplification of structural changes, not model energy or force uncertainty.", zh: "该方法在带固定种子的原子抖动或各向同性应变扫描后重新计算描述符。每个扫描步都会重建选中结构并重算描述符，代价随（结构数 × 振幅数）增长，因此计算在整个数据集上等距抽取子集而不是使用每一帧，并会报告使用了多少结构、数据集共有多少结构。响应曲线衡量结构变化的局部连续性和放大程度，不代表模型能量或力的不确定性。" },
    application: { en: "Use it to check descriptor smoothness, expose cutoff or representation discontinuities, and compare robustness of descriptor settings under physically motivated small perturbations. Raise Max structures when rare environments (defects, interfaces, phase-change frames) must be covered; keep it small when the descriptor is expensive and only the overall response shape matters.", zh: "可用它检查描述符平滑性，暴露截断或表示不连续问题，并在具有物理意义的小扰动下比较不同描述符设置的稳健性。当需要覆盖稀有环境（缺陷、界面、相变帧）时提高“最大结构数”；当描述符本身昂贵、只关心整体响应形状时保持较小取值。" },
  },
};

const FALLBACK_GUIDE = ANALYSIS_METHOD_GUIDES["overview.feature_variance"];

export function getAnalysisMethodGuide(key: string): AnalysisMethodGuide {
  return ANALYSIS_METHOD_GUIDES[key] ?? FALLBACK_GUIDE;
}
