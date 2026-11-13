# Colab CLI：Phase 1 只读集成审计

> 审计日期：2026-11-11（Asia/Shanghai）。状态：CURRENT / inspected_only。
> 本文记录现有实现与下一阶段方案，不代表远端已连通、TPU 已运行或基线通过。未执行 Colab CLI、未读取认证缓存／会话文件／运行日志，未修改 Colab 源码、配置或依赖。

## 1. 已有资源与来源

- 工作目录：`C:/Users/fengluo/Desktop/colab/google-colab-cli`。
- 上层 Windows 使用说明：`C:/Users/fengluo/Desktop/colab/WINDOWS-COLAB.md`。
- Git HEAD 元数据：`refs/heads/fix-assignment-timeout-recovery`，commit `a41650a15e80ced719650295691cbb4a61e3656b`。本轮读取 HEAD／分支文件；没有运行 Git，工作树状态未验证。
- 项目入口：`pyproject.toml` 的 `[project.scripts] colab = "colab_cli.cli:main"`；`src/colab_cli/cli.py:152` 调用 Typer app。
- 现有可执行路径：`.venv/Scripts/python.exe`、`.venv/Scripts/colab.exe`，仅检查文件存在，没有执行。
- Python 声明为 `>=3.12`；Windows 指南记录使用 3.13.14。目录元数据显示 `google_colab_cli-0.1.dev70+ga41650a15.dist-info`、`jupyter_kernel_client-0.8.0.dist-info`；本轮未加载包，METADATA 文件读取被拒绝。
- `pyproject.toml` 要求 `jupyter-kernel-client==0.9.0`，`uv.lock:513` 实际锁定 `0.8.0`、Git commit `f18e982c3265df5e923aa9def101ab3fd737e139`。这是既有声明差异，不能擅自升级或替换来消除。
- 完整路径、大小和 SHA-256 见 [Colab 来源清单](colab-source-manifest.json)。清单只包含明确选择的源码、文档、依赖声明和测试，不包含 live auth/config/log。

Windows 指南记录了 2026-10-20 的简单远端 print 成功，以及 `tpu-work` 会话和 TPU v6e1；这属于历史记录，不能据此断言当前会话存在、设备版本相同或内核实验已验证。上游 README 仍声明 Windows 不受支持；Windows 指南列有 edit、SSH 清理和 REPL 的已知限制。本轮没有重新运行这些历史测试。

## 2. 精确命令与返回契约

以下命令均为后续已授权执行时的调用形式，本轮未执行。工作目录固定为上述 CLI checkout，使用已有解释器避免 `uv run` 的环境同步／依赖安装行为：

```powershell
& '.\.venv\Scripts\python.exe' -B -m colab_cli.cli --auth oauth2 exec -s <approved-session> -f <local-script.py> --timeout <seconds>
& '.\.venv\Scripts\python.exe' -B -m colab_cli.cli --auth oauth2 upload -s <approved-session> <local-file> <remote-file>
& '.\.venv\Scripts\python.exe' -B -m colab_cli.cli --auth oauth2 download -s <approved-session> <remote-file> <local-file>
```

`-B` 仅禁止 Python 字节码缓存，不禁止 CLI 自身的日志、状态和 updater 写入。禁止把此形式描述为“无副作用”。

| 命令／API | 参数和默认值 | 实际输出／副作用／失败语义 | 源码依据 |
|---|---|---|---|
| 全局 callback | `--auth oauth2|adc`，实际默认 **oauth2**；`-c/--client-oauth-config`；`--config`；`--logtostderr` | 设置全局 State，创建日志 handler；普通执行／文件／状态命令可触发更新检查。README 与 bundled Skill 所说默认 adc 已过时 | `cli.py:50-114` |
| `exec` | `-s/--session`，`-f/--file`，`--timeout` 默认 **30.0 秒**，`--output-image`，可重复 `--env KEY=VALUE` | `.py` 按本机默认编码读为文本发给远端；不自动上传依赖，不设置脚本式 argv。无 `-f` 时读 stdin；交互终端无输入 exit 1；空白代码 exit 0 | `commands/execution.py:140-197` |
| `exec` notebook | `-f file.ipynb` | 顺序执行 code cells；每个 block 使用同一个 timeout；finally 在本地写 `<name>_output.ipynb` | `commands/execution.py:174-185,234-283` |
| `exec` 输出 | stream/data/error output dictionaries | stdout、stderr分别流式输出；显示数据可能经过 Rich 格式化；error 写 stderr。**返回的远端 error output 没有被映射成非零 CLI exit**；因此 exit 0 不代表任务成功。现有 `tests/test_exec.py:227-238` 也包含 error output 与 exit 0 的断言 | `commands/execution.py:113-137,258-278` |
| `ColabRuntime.execute_code` | `code, allow_stdin=False, stdin_hook=None, output_hook=None, timeout=None` | 返回 `List[Dict]`，包含 `output_type=stream/error` 或 MIME data；error status 没有 error output 时补合成 error。不是实验结果 schema | `runtime.py:180-267` |
| `upload` | 单个本地文件 + 单个远端路径，`-s` | 成功打印 Uploaded；本地不存在／异常 exit 1。无目录递归上传 | `commands/files.py:76-102` |
| `download` | 单个远端文件 + 单个本地路径，`-s` | 成功打印 Downloaded；异常 exit 1。直接 `wb` 写目标，可能覆盖已有文件；不创建本地父目录、不校验哈希、不原子提交 | `commands/files.py:105-132`; `contents.py:72-91` |
| `ls` | `PATH` 默认 `content`，`-s` | 人类可读名称列表，目录带 `/`；记录 file_operation，异常 exit 1 | `commands/files.py:26-52` |
| `status` | 可选 `-s` | 人类可读 hardware／IDLE／BUSY／last execution；不存在指定会话仅打印信息，不保证非零 exit。BUSY 来自本地 `s.running`，不是远端作业查询 | `commands/session.py:281-321` |
| `sessions` | 无任务 ID 参数 | 查询 assignments，同步并可剪除 stale 本地记录；不是纯本地只读检查 | `commands/session.py:254-278`; `common.py:114-148` |
| `log` | `-s`、`-n/--lines`、`-t/--type`、`-o/--output` | 输出历史概要或导出 `.jsonl/.md/.txt/.ipynb`；事件含 timestamp、event_type、执行 code 和 outputs。整个历史不能直接当作已脱敏实验工件 | `commands/utility.py:129-206`; `history.py:30-47` |
| `run` | SCRIPT、转发 ARGS、`-s`、`--tpu`、`--gpu`、`--keep`、`--timeout` 默认30秒 | **总是先申请新 VM**；通常 finally teardown，`--keep` 才保留。与 exec 不同，映射远端错误和 SystemExit 为退出码；没有自动下载结果文件的实现 | `commands/run.py:198-458` |
| `restart-kernel`、`stop`、`new` | 会话／设备参数 | 分别重启 kernel、释放 VM、分配 VM；均不在本阶段允许动作中，也不能作为超时的自动恢复动作 | `commands/session.py:94,220,324` |

CLI 没有通用 `--json`、`submit/job-id/wait` 或恢复远端执行流的命令。普通 stdout 可能混入 CLI 状态、更新提示及 Rich 输出。RSI 应解析独立保存、下载并验证的结果文件，不以 stdout 某个词或 CLI exit 0 作评测结论。

## 3. 超时、重试与恢复边界

1. **Control-plane HTTP**：`client.py:220-224` 仅在没有显式 timeout 时读取 `COLAB_CLI_REQUEST_TIMEOUT`；未设置时保留底层 AuthorizedSession 默认值。它不控制 Contents API，也不等于整个命令总预算。
2. **资源分配恢复**：`client.py:274-316` 默认 `COLAB_CLI_ASSIGN_RETRIES=3`，即最多4次 POST 尝试；`COLAB_CLI_RETRY_BACKOFF=10`，默认退避10、20、40秒。ReadTimeout 或502/503/504后，在再次POST前以同一 notebook hash GET核对；412转为TooManyAssignmentsError。最后一次失败会直接抛出，不保证最后一个不确定POST一定已核销。本阶段不调用 assign。
3. **Jupyter连接启动**：`runtime.py:90-172` 对 ReadTimeout／ConnectTimeout 最多3次启动尝试，间隔2、4秒；不等于任务重跑。
4. **代码等待**：exec 的每个代码块 timeout默认30秒。前置 `os.chdir('/content')` 没传此 timeout，使用底层默认；`runtime.py` 注释记载当前底层默认约10秒，未在本轮执行验证。
5. **超时不是取消**：exec finally清空本地 running标记、关闭通道，不调用远端中断或释放。远端仍可能执行；不能根据随后 IDLE认定完成。异常可能导致该次代码的完整 outputs未记入history。
6. **认证／会话恢复**：`common.py:86-111` 在proxy token不足5分钟或无到期值时查询assignment并写回；assignment缺失会剪除本地记录。exec前置连接发现401/404会剪除记录（`execution.py:220-226`）。不自动取得新的授权，也不应把401解释为可立即重启。
7. **文件传输**：`contents.py:40` 的 requests.request没有 timeout参数、重试、断点续传或吞吐限制。调用方未来应限制本地等待并把中断后的下载视为不完整；本地进程停止也不能证明远端已停止写入。

后续恢复方案：每次尝试具有不可复用的 Run ID和独立结果目录；先读取已有 status／manifest和工件，确认运行是否结束、输入和代码绑定是否匹配，再决定重试。超时记录为 `incomplete/unknown`，资源不足记录为 `not_run/deferred`；不把二者强行归为 candidate错误。重试使用新 Run ID并保留前一次结果。CLI未提供可靠远端job状态；如无法判定是否仍在运行，应暂停该设备队列，不能叠加正式计时任务或自动 restart/stop/new。

## 4. 路径映射与文件传输设计

以下是**待 Phase 2 批准的路径约定**，不是现存远端目录事实。初次使用应先通过小型非秘密文件验证 Contents API根目录映射，不运行 Kernel。

| 本地对象 | 建议远端位置 | 约束 |
|---|---|---|
| 原始 `C:/Users/fengluo/Desktop/training/PallasKernels` | 无直接同步写回 | 原仓库只读 |
| 经过文件清单固定的未改基线包 | `/content/rsi-kernel-agent/sources/<snapshot-id>/PallasKernels/` | 排除 `.git`、认证、环境和缓存；运行所需源码／reference／tests／注册文件保持原始字节。具体文件由 MLA 审计确定 |
| 孤立 Candidate包 | `/content/rsi-kernel-agent/candidates/<candidate-id>/PallasKernels/` | 与基线相比只有精确批准的 MLA Decode／MLA Forward实现文件允许不同；共享工具、测试、reference、benchmark、容差、注册和环境文件必须匹配 |
| 受信执行／采集入口 | `/content/rsi-kernel-agent/harness/<harness-revision>/` | 与Candidate包分离，候选不能改裁判或结果解析；本轮不实现 |
| 某次实验／诊断结果 | `/content/rsi-kernel-agent/runs/<run-id>/` | 只含对应Run数据，禁止覆盖不同Run |
| 下载目标 | 批准的本地实验产物根目录下 `<run-id>/` | 不默认放进知识库；下载至新 `.part` 文件并校验后转正 |

exec每次先切换到 `/content`；传送的本地脚本不会自动建立本地源码目录结构。ContentsClient将路径首尾斜线移除，再以 `/api/contents/<path>`访问；文档提供 `content/...` 与 `/content/...` 用法，但实际远端根映射仍需首次验证。不能把Windows路径直接用作远端路径。

ContentsClient.upload一次读取整个文件并转base64，PUT payload为 `type=file, format=base64, chunk=1`；这个单一chunk字段不代表已有分块上传循环。download遇到目录会拒绝。可为允许的源文件制作一个有SHA-256清单的归档，上传单文件后安全解包；大trace应先限定目录，分成适当大小的独立归档／分片，再逐个下载。分片大小需实际服务限制和内存预算支持，本轮不虚构限额。不得打包整台机器、运行凭据、Colab历史或其它Kernel实验。

## 5. Result／Profile工件契约（计划）

每个Run目录至少保留：`run.json`（Task／Workload／Candidate／parent／环境／协议绑定）、`status.json`、`correctness.json`、原始timing样本、选定的环境信息、错误／费用记录及`artifact-manifest.json`。manifest给出相对路径、字节数、SHA-256、媒体类型和完整性状态；所有相对路径必须留在Run目录内。

Profile按实际capture目录原样保存，包括产生时已有的 `plugins/profile/<timestamp>/...`、XPlane／trace等文件；没有捕获的LLO／custom-call／计数器标为unavailable，不补零。诊断捕获使用独立Run ID，并记录capture／compile设置，不混进正式Benchmark计时。只对MLA Decode和MLA Forward执行正式实验。

下载顺序为status／manifest、结果文件、限定的Profile归档；核对每个工件的大小和哈希以及source/workload/environment绑定。CLI上传／下载成功消息只能证明调用返回，不能代替字节校验。没有完成标记或缺失工件时保留partial状态；不晋升、不自动重跑。

保存实验专用stdout／stderr时应排除控制面token／URL参数，并在入库前脱敏；禁止把 `~/.config/colab-cli/colab.log` 或全量history原样复制入知识库或结果目录。先用保存的证据做离线分析，缺少证据才提交CaptureRequest，由Controller决定是否授权补采。

## 6. 环境版本发现计划

不运行 `install`、`uv sync`、`pip install`、`update --install`，不改变已有JAX／jaxlib／libtpu／compiler。

1. 通过既有CLI在批准会话执行一个仅收集信息的受信脚本（本轮未执行）。用 `importlib.metadata` 按名称查询 `jax`、`jaxlib`、`libtpu`、`xprof`、`tensorboard-plugin-profile`、`tensorflow`、`tokamax` 的版本；缺包保留null和明确availability。记录Python、平台、包路径及所需API是否存在，避免输出可能含凭据的全量 `pip freeze`／环境变量。
2. 软件元数据不能证明设备可用。后续允许初始化设备时记录 `jax.devices()` 的platform／device_kind／数量及目标型号；不得把Windows指南的v6e1当当前硬件事实。记录JAX backend／编译与capture设置中批准的非秘密字段。
3. XProf必须区分本机解析器、远端capture能力和安装包版本；无安装元数据时记录unknown，不猜测版本。高级TPU7x指标不自动适用于v6e。
4. 两个任务的现有依赖与远端不兼容时，保存发现并停在该前置条件，不修改保护文件或升级环境使之“通过”。

## 7. 两个未改基线的最小实验计划

执行需另行批准TPU、会话、预算和产物目录。本轮仅设计：

1. 固定基线源码commit及实际工作树文件哈希；建立两个精确任务ID及实现文件allowlist。以MLA审计找到的既有调用、reference和测试选择器为准，不能把README示例替代真实入口。
2. 复核源包所有受保护文件哈希，准备只含必要非秘密文件的快照；上传前核对文件清单。复用CLI传输，不构建新的连接服务。
3. 核实批准会话和环境。先完成上一节环境发现和路径探针；不足时记录blocked/deferred。不得为方便恢复而重启现有会话。
4. 对MLA Decode、MLA Forward分别选择现有测试中的一个真实workload作为冒烟基线，并保留现有reference、输入生成、容差和计时边界。冒烟不等于完整正确性或晋升资格；正式覆盖仍由Benchmark契约决定。先执行既有正确性检查，失败也保存，不调宽容差。
5. 单设备串行执行两项正式计时，等待全部输出叶子，保留warmup／编译／执行分界及原始样本。若原harness不能提供这些事实，先补受信外围适配设计并获准实施，不修改测试、reference或benchmark规则。
6. 按固定协议确认／重测；首次原样基线仅建立证据，不宣称优化收益。Profile按需另建诊断Run，不默认同时抓取。
7. 下载、校验并绑定结果；在所有attempt、失败、回归及deferred记录完整后，再讨论多个Strategy的多个Plan/Candidate。不能在实验前选唯一赢家。

## 8. 权限、日志及复用边界

- 用户指定只读的Colab源码、凭据和持久配置均未修改。CLI实际执行会正常写session/history/log，并可能刷新认证；未来运行审批须明确这些已有工具的正常副作用，不手工编辑认证状态。
- `--config` **只覆盖session state路径**；`common.py:57-68` 的settings/history及`:195-199`的日志仍走用户默认目录。它不是完整的沙箱隔离开关。
- `auto_update.py:216-230` 只有持久设置 `enable_update_check`；没有找到受支持的环境变量或exec选项关闭更新检查。默认检查可写settings缓存。若后续仍要求持久settings绝对只读，必须先明确合法的执行方式或额外授权，不能编造禁用参数、修改现有设置或替换CLI。
- `common.py:186-199` 创建DEBUG日志；`client.py:233-236`记录请求header及响应header/body。源码显示敏感数据可能进入本机日志的风险，但本轮没有读日志，也没有泄露事实证据。后续不加 `--logtostderr`，不采集原始控制面日志到报告。
- `--env`内容会进入发送代码及history，不能用于传递需要保密的值。
- 仓库内已有 `skills/colab-operator/SKILL.md`，可作为 `COMPOSE`候选，状态 `inspected_only`，未安装到全局，也未在当前会话catalog中自动暴露。其默认auth=adc、超时即restart等旧建议与源码／本次授权不同，不能照抄；用户的禁止重置、禁止安装及只读限制优先。
- 未运行本仓库要求的实时集成测试：本任务是只读审计，且用户明确禁止TPU任务和会话变更；不是声称实现完成后跳过验证。

## 9. 本轮验证与剩余风险

已读README、Windows说明、AGENTS、命令／runtime／Contents／状态／历史／请求／updater源码及相关测试断言；核对Git HEAD元数据、文件存在和静态哈希。没有运行CLI、连接远端、创建会话、重启、删除、安装、上传或下载。没有读取token／session/config/log秘密内容。源码清单验证不能替代运行验证。

待解决重点：既有依赖版本声明差异、Windows支持边界、exec退出码与任务结果不等价、Contents无timeout／原子校验、没有远端job状态契约、默认updater写settings，以及当前会话／设备／版本未验证。它们应进入Phase 2受信适配和验收计划，不在本阶段修改现有CLI。
