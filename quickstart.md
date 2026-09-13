# IID 造题 pipeline

源码使用 Python 3.11+；生成的任务使用 Linux 容器、Git、Bash、Python 3.11+ 及对应仓库的 Node/浏览器环境。

pipeline 将“候选”“已打包待验收任务”“正式题集”分开：`build` 只生成 staging，`publish` 才生成正式题集。正式发布要求离线执行证据、独立语义审核、官方数据去重，无法满足就报错，不生成假成功记录。

## 安装与测试

在 `C:\Users\mzh\kimitodo\src` 中执行：

```powershell
python -m pip install -e .
python -m iid_pipeline --help
python -m unittest discover -s tests -v
```

也可以直接在此目录执行 `python -m iid_pipeline`，无需安装包；图片验证依赖 Pillow。Git 和 Node 需要在 PATH 中。真实任务验收另需 Docker 的 Linux containers 模式，构建时可联网安装，解题和验证均以 `--network none` 运行。

GitHub 采集可使用环境变量 `GITHUB_TOKEN`。测试是临时的合成 Git/Node 项目，不会冒充真实 issue/PR，也不会生成一批未经验证的“正式题目”。

## 代码分工

| 文件 | 职责 |
| --- | --- |
| `policy.py` | 五仓库 allowlist、UTC PR.created_at 排除区间、根问题/issue/补丁指纹去重 |
| `github.py` | PR 分页采集、REST 关闭引用、GraphQL closingIssuesReferences、原始数据快照和重跑缓存 |
| `prepare.py` | 固定 Git merge-base、完整二进制 diff、base 快照、图片归档、题目设计说明 |
| `package.py` | 审核绑定、输入校验、严格任务目录生成和结构检查 |
| `runtime/` | 随题 vendored 的独立 logparser 与 OJ 判分器，不依赖安装 swebench |
| `verify.py` | 离线 baseline/base/gold/错误补丁验证、证据校验与发布 |
| `batch.py` | 有界并发、已通过任务复用、批量生产与官方排除索引 |
| `cli.py` | 命令行入口 |

## 1. 采集真实候选

```powershell
python -m iid_pipeline collect --output ../work/candidates.jsonl --cache ../work/github-cache --cutoff 2026-09-12T00:00:00Z --repo chartjs/Chart.js --max-prs 500
```

省略 `--repo` 采集全部五个仓库，可重复传 `--repo`。`--max-prs` 是每个仓库扫描的 PR 上限，便于先试跑；正式全量省略它。月份边界完全按根 README 定义，不改为 merged_at。cutoff 还要求 PR 在截止前已合并。

未设置 GitHub token 时，只接受 PR 正文中的明确关闭引用；设置 token 后，额外分页查询 GitHub GraphQL 的关联关闭关系。普通 `#123` 提及不会直接作为“已解决 issue”的证据。429/403 会停止并要求稍后恢复，不无限重试。

采集输出保留原始 PR/issue、来源 URL、时间、commit。issue 在修复后被编辑过时标记需要修复前上下文审核；当前 API 返回的正文不能自动保证是修复前版本。缓存按 cutoff 隔离，同一批可重跑，新批不会错误地复用旧首页。已有 JSONL 按 instance_id 去重追加。

## 2. 准备代码与图片

```powershell
python -m iid_pipeline prepare --candidates ../work/candidates.jsonl --instance <真实instance_id> --output ../work/drafts --cache ../work/repo-cache
```

命令缓存 bare repo，取固定 base/head 的 merge-base，生成 `base.tar` 与 `upstream.patch`。Git diff 使用 `--binary --full-index`。如已有对应仓库和 commits，可传 `--local-repo <路径>`，只读该仓库，无须重新 clone。

Markdown/HTML 图片自动归档、解码校验、记录哈希，题面中的 URL 改为 `/testbed/assets/<instance_id>/...`。损坏图片、过大图片、动态媒体会进入 `asset_failures.json`，不会假装下载成功。动图需要 题目设计者在保留来源说明的前提下提供审核过的静态帧。至少一张有效图片及其信息增量是 build 的硬门槛。

`prepare` 的输出是待设计草稿，不是题目。`manifest.json` 的空字段是明确待完成的 设计输入；没有独立审核和真实测试内容不能 build，更不能 publish。

在线复现链接另存 `reproduction_requests.json`。第三方 IDE 的导出和依赖恢复由 题目设计者完成，写入 `resources`（file、repo_path、source_url、sha256、instructions）并在题面引用本地路径；缺少导出或仍残留远程图片/IDE 依赖时拒绝打包，不让 agent 在断网容器中面对不可用的复现链接。

## 3. 设计新增 F2P、运行环境与图片说明

历史五仓库各版本的测试框架、运行命令和视觉缺陷不同。准备草稿后，题目设计者依据 `design_instructions.md`，直接补齐 `manifest.json`、测试补丁、参考修复和环境脚本；后续以执行结果验收。

题目设计者必须交付：

- `gold.patch`：对应真实 PR 的生产代码修复；`test.patch`：本题新增测试的完整 Git diff。保留二进制内容。
- `setup.sh` 与 digest-pinned `base_image`：基于 Debian/Ubuntu 的环境模板，安装精确 Node/浏览器/字体和离线依赖。生成器会安装 git/bash/python3。不能依赖解题时下载 npm 包、远程字体或 CDN。
- 可选 `environment.bootstrap_script`：在复制源码前安装共享依赖，便于五仓库复用 Docker 缓存层；该脚本同样绑定审核哈希。npm 依赖应使用锁文件和 `npm ci`，避免历史版本的自动 peer 解析停滞。仓库特有依赖仍由 `setup.sh` 安装。
- 非空的 F2P/P2P 清单、真实 `command` 与 `baseline_command` argv。baseline_command 不加载新测试，只验证原有 P2P；command 在测试补丁应用后运行全部必需用例。
- `log_parser`：`junit`、`jest`、`mocha`、标准 `json` 或显式 `regex`。结构化报告放在 `.iid-results/` 下，以避免覆盖源文件，并在每次测试前移除旧报告。
- `protected_paths`：新测试补丁接触的文件及测试配置/运行器；`p2p_files`：已有测试文件。本实现让 F2P 放入独立新增/专用测试文件，禁止 test.patch 改写声明的 P2P 文件，也禁止 gold 改测试。
- `f2p_cases`：每个 F2P 的实际文件、需求说明和针对目标断言的 `failure_regex`。`.*` 不是有效缺陷证明；base 上必须是目标 FAIL，不能用 ERROR/MISSING 充数。
- 所有二进制测试 fixture 的 `fixture_sha256`；至少一个能运行测试但会被判错的 `negative_patches`。
- 每张图的 `role`、`visual_evidence`、`information_gain`、`without_image` 和 `linked_test_ids`，及框架/渲染方式/缺陷/难度标签。

标准 JSON reporter 契约：

```json
{"tests":[{"id":"稳定且唯一的用例ID","status":"PASS","detail":""}]}
```

其他允许状态为 `FAIL`、`ERROR`、`SKIP`、`TIMEOUT`。不能只有失败列表；不能只提供 suite 汇总数。Jest ID 是去掉 `/testbed/` 前缀的文件路径加 `::fullName`；JUnit 默认 `classname::name`，可用 `id_fields` 配置；Mocha 使用 `fullTitle`，重名会拒绝。regex 要有命名组 `id`/`status` 和双向 `status_map`，只做整行匹配。基于 Karma 的任务需配置逐用例 reporter（例如 JUnit），不能直接使用旧的 fail-only parser。

语义真实性不能靠图片文件存在或字符串非空证明。独立审核者必须检查 PR 关联、修复前上下文、修复范围、gold 对应上游、需求清晰度、视觉信息增量、P2P 保留及近重复。人工审核可由组织现有审核流程提供，但 pipeline 不会自动伪造批准。

审核完后显式记录：

```powershell
python -m iid_pipeline review --manifest ../work/drafts/<instance_id>/manifest.json --reviewer reviewer-name --check real_issue_pr --check pre_fix_context --check atomic_production_fix --check unambiguous_requirements --check visual_information_gain --check p2p_preserved --check dedup_reviewed --check gold_matches_upstream
```

批准绑定 manifest、题面、代码快照、补丁、setup 和图片的哈希。批准后改内容会失效，必须重新审核。此命令记录审核事实，不代表它本身完成了语义审核。图片/代码近重复和问题族关系的语义审查由该检查项承担；自动去重覆盖实例、根问题、issue 及标准化补丁指纹。

## 4. 打包并检查目录

```powershell
python -m iid_pipeline build --manifest ../work/drafts/<instance_id>/manifest.json --output ../work/staging
python -m iid_pipeline check ../work/staging/<instance_id>
```

每个生成目录的顶层恰好是：

```text
<owner>__<repo>-<PR编号>/
├── instruction.md
├── task.toml                 # schema_version = "1.2"
├── environment/
│   ├── Dockerfile            # 构建方式；base + 图片 + 依赖
│   ├── base.tar
│   ├── setup.sh
│   └── assets/<instance_id>/...
├── tests/
│   ├── test.sh
│   ├── config.json           # F2P/P2P/命令/parser/证据元数据
│   ├── test.patch
│   ├── sweb_grade.py
│   ├── logparsers.py
│   └── negative_patches/...
└── solution/
    ├── solve.sh
    └── gold.patch
```

`task.toml` 选择规范允许的 Dockerfile 构建路线，因此没有假造一个不存在的 docker_image；有 cpus、memory_mb、storage_mb、构建/agent/verifier timeout，且 allow_internet=false。image 层不复制新测试、judge 或 gold，只保留基础版本原有测试。源码 archive 在镜像内重建单提交 Git 仓库，移除未来修复的 Git 历史；原始 base commit 作为 provenance 保留，不把重建的 commit hash 冒充原 SHA。

评测一条命令：`bash /tests/test.sh`。oracle 一条命令：`bash /solution/solve.sh`。Harbor 风格 runner 将 tests/solution 在适当阶段放入容器即可；本 pipeline 的 Docker 验收器会按阶段只读挂载它们。

测试入口保留候选修复，不会为了“检出 base”把 agent 的生产代码修改清空。它检查保护文件，使用 `git apply --check` + `git apply` 加载测试补丁，校验二进制 fixture，执行测试并写 `/logs/verifier/reward.txt`。

- 所有 F2P/P2P 明确 PASS、进程退出码为 0且无超时，才写 `1`；缺失、SKIP、FAIL 等写 `0`。
- 判分器崩溃、结构化报告损坏、测试命令不能启动等是异常：删除旧 reward，写 error.json，返回非零，不静默写 0。
- 候选补丁不能应用、保护测试被改、正常执行后缺少用例等是候选失败，写 0。
- 不使用 `patch(1)`，不采用“不在失败列表就通过”的语义，不用重试成功覆盖前一次失败。

## 5. 离线双状态与错误补丁验收

```powershell
python -m iid_pipeline qualify ../work/staging/<instance_id> --output ../work/qualification/<instance_id> --runs 5
```

实际构建 Docker 镜像并保存不可变 image ID，每次启动独立且无网络的容器。依次重复验证：

1. baseline：不加 test.patch，已有 P2P 全过。
2. base：加 test.patch，F2P 全部因指定目标断言失败，P2P 全过。
3. gold：加 gold 再加 test.patch，F2P/P2P 全过。
4. negative：每个错误补丁都必须实际运行完整用例，产生真实断言失败，而非因补丁无法应用/缺依赖而“被杀死”。

任何状态不稳定或缺用例都不生成 qualified receipt。资格证据包含任务树哈希、每轮原始日志、测试结果、镜像 ID和文件哈希。报告保存在任务目录外，不破坏要求的目录结构。CPU/内存和断网在 Docker 命令中设置；storage_mb 写入任务 schema，由正式运行平台执行其磁盘配额策略，Docker CLI 的本地校验不跨 storage-driver 强行设置通用磁盘限额。

## 6. 正式发布

创建 `batch.json`，路径相对于此 JSON 文件：

```json
{"tasks":[{"task":"staging/<instance_id>","qualification":"qualification/<instance_id>/qualification.json"}]}
```

```powershell
python -m iid_pipeline index-exclusions --input ../work/official-dev.jsonl --output ../work/exclusions.json
python -m iid_pipeline publish --batch ../work/batch.json --exclusions ../work/exclusions.json --output ../tasks
```

`official-dev.jsonl` 是真实官方数据的本地 JSON/JSONL 导出，至少含 instance_id，尽量包含原 patch、issue_urls、root_task_id，以增强去重；不要填虚构占位记录。已有题库也可合并进排除数据。

发布逐题验证独立审核、容器验收凭据及其文件哈希，并与官方数据及同批任务去重。空题集、缺少凭据、任务或证据变动、重复根问题均禁止发布。全部通过后，原子生成正式题集；release 索引保存在题集目录旁，记录各题的任务哈希、镜像 ID、验收凭据哈希和排除数据哈希。

## 7. 批量运行与扩展

完成题目设计和独立审核后，可以用 `run --plan` 自动串联 build → qualify → publish：

```json
{
  "workspace": "../work/run-001",
  "release": "../tasks/run-001",
  "manifests": ["../work/drafts/<instance_id>/manifest.json"],
  "exclusions": "../work/exclusions.json",
  "jobs": 2,
  "runs": 5,
  "docker": "docker"
}
```

```powershell
python -m iid_pipeline run --plan production-plan.json
```

任务级并发有上限，各任务的容器、目录和日志隔离。重跑复用哈希仍匹配的合格任务，失败日志保留。正式题集不覆盖已存在目录，变更输入或预算时使用新版本的工作目录。

尚不能自动证明的语义属性（真正的图像信息增量、需求是否充分、PR 是否原子）通过明确的独立审核记录输入，执行门槛负责可机器核验的部分。本工具不是通过填满 JSON 字段就证明所有语义正确的生成器。

接口参考：[GitHub PR REST API](https://docs.github.com/en/rest/pulls/pulls)。目录规范以本项目 `design_requirement.md` 为准。
