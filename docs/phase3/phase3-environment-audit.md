# Phase 3 环境与目录审计

2026-11-12。四个用户指定目录均已核实存在，未选用其他同名 Kernel 仓库。

| 目录 | 权威职责 | 本阶段处理 |
|---|---|---|
| `C:\Users\fengluo\Desktop\rsi-kernel-agent` | 唯一 RSI 开发主项目 | 系统兼容扩展、测试、计划、审计和工件 |
| `C:\Users\fengluo\Desktop\training\PallasKernels` | 当前 Kernel/Reference/Benchmark/Parser | 只读审计、精确来源清单；Candidate独立包 |
| `C:\Users\fengluo\Desktop\project2` | 历史经验 | 索引、136条恢复记录、来源与证据分级；不写当前Best |
| `C:\Users\fengluo\Desktop\colab` | 现有 Colab 连接 | 原 bridge API与已有解释器，未替换连接方案 |

主项目既有 Python 环境用于本地协议测试；桥接使用它自己的已有 `.venv\Scripts\python.exe`。这两个本地环境未安装或升级依赖。远端专用 Runtime 已成功分配；默认环境与为 PallasKernels 创建的隔离环境分别实读如下，不能将两者混写。

| 项目 | Colab 默认环境 | Colab 隔离环境 |
|---|---|---|
| Runtime session ID | `tpu-v6e1-s-euw4a0-xyzyx7kofigt` | 同一 Runtime |
| Python | 3.13.18 | 3.11.17 |
| JAX / jaxlib | 0.11.2 / 0.11.2 | 0.8.1 / 0.8.1 |
| libtpu | 0.0.50 | 0.0.30 |
| TensorFlow | 未安装 | 2.20.0 |
| xprof | 未安装 | 2.22.1 |
| NumPy / PyYAML | 本次默认探针未读取 | 2.4.6 / 6.0.3 |
| 设备实读名称 / 数量 | `TPU v6 lite` / 1 | `TPU v6 lite` / 1 |
| 最小同步 JAX 运算 | 4×4 Gram matrix，预期值一致 | 平方和 140.0，预期值一致 |

分配请求类型为真实 `v6e1`；设备实读名称原样保留。可获取的 coords/core_on_chip/process_index 与 XLA backend_platform_version 保存在 [默认环境](../../knowledge-site/manifests/phase3-2026-11-12/environment.json)和[隔离环境](../../knowledge-site/manifests/phase3-2026-11-12/environment-isolated.json)中，不补写无法观测的拓扑或编译器版本。

隔离环境复用历史 Colab uv 准备方法，位于 `/content/rsi-kernel-agent/env-20261112/`；解释器是其中 `venv/bin/python`。该目录内安装 Python 3.11 及固定 JAX/jaxlib/libtpu/TensorFlow/xprof 版本，未修改默认 Colab 依赖或全局 TPU 设置。实际安装清单见 [isolated-environment-setup.json](../../knowledge-site/manifests/phase3-2026-11-12/isolated-environment-setup.json)。因此不能继续笼统声称“本阶段未安装依赖”，准确范围是本地环境保持原样、远端新增独立环境。

两种环境的真实同步计算证据、声明边界和原 Store 归档索引见 [probe-evidence.json](../../knowledge-site/manifests/phase3-2026-11-12/probe-evidence.json)。XPlane protobuf import 成功仅证明依赖可导入，不是 Profile capture 验收。此文不声称 MLA 正确性、性能或后续 Gate 已通过。

主项目原始运行时代码摘要保存在 [runtime-baseline.json](../../knowledge-site/manifests/phase3-2026-11-12/runtime-baseline.json)。Kernel 相关来源库存及摘要见 [pallaskernels-source-inventory.json](../../knowledge-site/manifests/phase3-2026-11-12/pallaskernels-source-inventory.json)。各报告独立标识 Source audit、历史真实证据和本轮测试；旧阶段报告不改写。

系统接口见 [集成审计](phase3-project-integration-audit.md)，Kernel事实见 [Kernel审计](phase3-pallaskernels-audit.md)，历史见 [恢复报告](phase3-project2-history.md)，连接状态和限制见 [Colab审计](phase3-colab-connectivity-audit.md)，执行顺序见 [实施计划](phase3-implementation-plan.md)。
