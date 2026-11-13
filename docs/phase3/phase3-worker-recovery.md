# Phase 3 远端执行回执与恢复

2026-11-12。以下描述已实现的 `colab_worker.py`，测试均为**本地 CPU 子进程软件验证**，不能据此宣布真实 TPU 正确性、计时或性能提升。

## 接口与身份

Worker 为可独立上传的标准库文件，入口 `run_command(command)` 或 stdin JSON CLI。command 包含 action、root、runtime_id、nonce；action 支持 bootstrap、lookup、submit。submit 另含 request、payload_path、runner_path。payload 为 source_bundle、oracle_bundle、blobs（SHA256→base64），所有请求、工件长度/摘要、source entry、oracle、build和固定runner摘要在执行前复核。配置、来源、可信helper不匹配时拒绝。

真实命令由已有 ColabBridge 送达，worker不读取账户凭据、不分配/重启/终止Runtime、不创建第二实验数据库。远端spool只是执行队列与回执载体，结果回本地后由现有ColabBackend导入原ArtifactStore及ExperimentStore，再走固定Evaluator和consider。

Candidate与Oracle为独立目录，源码由字节复制构建；不将可修改Candidate硬链接到工件。文件路径拒绝逃逸、symlink、Windows junction。正式stage在独立Python进程中加载固定runner，启动使用`-I -B`并移除继承的PYTHONPATH，避免用户目录启动代码进入worker；runner另开CPU Oracle进程生成同输入与参考输出。

## Runtime注册、串行与不确定性

Colab固定协调域为 `/content/rsi-kernel-agent/runtime-locks/<SHA256(runtime_id)>`。首次bootstrap将canonical spool root、nonce、runtime_id持久注册；同Runtime的另一root或nonce被拒绝。因此更换Task或spool不能绕过同Runtime锁或旧unknown状态。非Colab CPU测试使用固定系统temp协调域，无调用方自选锁目录参数。

runtime marker、永久sentinel和全局registration均需匹配。丢失后lookup是unknown，不能当全新Runtime重建历史。只在有效初始化域中、operation尚无执行意图时可返回definitely_not_started。执行意图在调用runner前fsync；一旦意图存在而没有有效completion且进程锁释放，状态为unknown，并阻止该Runtime后续操作。进程PID不承担执行身份，因此没有PID复用误判。

每个operation持有OS锁，所有正式generate/compile/correctness/benchmark/profile又共用Runtime锁。客户端断开或退出不会杀远端后台子进程；completed回取原回执，running继续查，unknown不重跑。未到execution intent的启动失败可以按原request重交；相同operation异值请求拒绝。一次已完成但本地尚未收回的操作不会再运行。

## 回执和原始证据

completion用独占原子发布，附整记录digest；回执保持原execution envelope，real额外含`artifact_refs`闭包。lookup校验canonical request、completion digest以及每个工件大小/摘要。损坏保持unknown，不覆盖修复为成功。

`completed.artifacts`提供ref/path供桥接取回，包含固定runnertyped输出所引用的原始NPZ、input manifest、XPlane、parser报告，也包含其他已生成HLO、specialization、compile/benchmark详情、stdout/stderr及设备可见性探针。失败stage已写出的trace/数组也保留；typed输出仍按real_protocol精确schema，不为附加日志随意扩字段。

进程异常退出可能留下intent和未封口的原始目录，此时没有伪造完成回执。需要人工/可信外部查询重建结果后再处理，不自动删记录或重新执行。当前未提供unknown强制清除API，也不强杀用户Notebook。

## 设备可见性

worker在执行前后扫描Linux `/proc/*/fd`中可见进程对 `/dev/accel*`、`/dev/vfio/<group>`的持有。发现其他owner时不执行候选；对benchmark/profile，设备节点或进程可见性无法核实时返回invalid/InterferenceUnknown。只排除当前worker PID，不排除用户Notebook。探针原始JSON归档。

这证明的是同一主机命名空间中的合作worker串行和前后可见设备持有检查。不能排除隐藏容器、其他TPU主机、两次观察之间的瞬时竞争，不能声称分布式恰好一次或绝对无人干扰。正式结果还须经过runner的实际设备/环境与计时检查。Windows/无TPU的CPUfixture不会变成有效benchmark。

## 验证

测试在 `tests/test_phase3_worker.py`：不同Task全stage串行、同operation并发只一次外部effect、异值冲突、source/runner/oracle拒绝、路径逃逸、marker及registration丢失、跨spool绕锁拒绝、原始工件/回执损坏、失败输出与日志回收。真实子进程`os._exit(73)`覆盖客户端提交后死亡和runner写fsync外部counter后死亡，确认恢复没有重复执行。

worker与生产`mla_runner.py`还有真实独立进程入口负例，错误entry在JAX加载前失败，证明两者API及身份拒绝实际衔接。本地测试不替代真实JAX/TPU分支。最终专项 **Windows20/20，8.052秒；既有WSL Ubuntu系统Python3.12.3的Linux20/20，4.917秒**，无跳过。完整输出见 [Windows日志](../../knowledge-site/manifests/phase3-2026-11-12/worker-windows-tests.txt)、[Linux日志](../../knowledge-site/manifests/phase3-2026-11-12/worker-linux-tests.txt)。其他模块与根级完整套件由Phase3总验收报告记录，不将本专项等同全项目验收。

已观察并修复的失败包括：回执body缺摘要校验、全部marker丢失误初始化旧spool、poll探测锁与新child启动竞争、Windows锁文件初始化写NUL竞争、跨spool独立锁绕过Runtime排他。runner依赖根匹配问题由worker集成负例实际暴露并交由runner模块修复，最终日志不隐藏失败结果。
