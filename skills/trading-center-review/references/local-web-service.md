# 本地常驻展示与发布

仅在用户明确选择本地常驻 UI 后使用。本机已于 2026-08-31 批准；不表示其他机器或自动化获得安装/调度权限。此服务不是 Plugin，不读取券商、不运行 Codex、不写数据库、不接 Obsidian Bridge。

## 三层职责

SQLite v5 仍是事实、分析缓存、计划版本和周度 revision 的权威，并追加估值、非执行性管理原则、严格日记版本绑定及工具/观察周期上下文。原始响应仍按授权留在私有运行区，只有固定白名单入库。DB 不保存全部 UI 整理文案，不能宣称 DB-only 可完整还原页面；已确认日记的完整脱敏包另以不可变私有工件保存。

发布命令验证每日/周度包，生成 `trading-review-display.v1` 固定展示快照与由 TypeScript 渲染的同一 V2 HTML。展示快照剔除账户模块及标签/时间、后台说明、委托计数、未成交/非美股操作；保留可见内容和必要时间/状态/相关证据。不是任意 JSON 存档，不允许原始响应、券商 ID、成本或凭据。

HTTP 服务只读取完整发布，只绑定 `127.0.0.1:8765`。`/` 为最近成功页面；发布时指定的历史路由固定到原发布。`/healthz` 只提供脱敏服务检查；不提供 JSON 文件、SQLite、源码、目录或日志。刷新网页不读取行情，也不更新周度。

## 文件与安全

固定目录为 `~/Library/Application Support/MarsTradingCenter/web-ui/`。生产命令不接受任意输出根；owner-only 0700/0600，拒绝软/硬链接、Git/Vault、其他所有者与过宽权限。

- `publications/<内容哈希>/`：固定展示快照、HTML、验证清单。
- `publications.json`：原子发布索引与上一个成功版本、固定历史路由。
- `code/<代码哈希>/`：安装时固定的 TS 展示/发布/HTTP/维护代码、Python 数据适配器与校验模块、原模板；没有 node_modules。
- `installation.json`、上一版安装清单与 LaunchAgent 备份：维护回退依据。
- `logs/`：仅脱敏启动和错误类别，无请求明细或业务数据。

先完整验证、写入并回读，再原子切换索引；写者锁与比较当前版本防止丢失更新。发布中断可补完字节一致的未发布工件，矛盾/损坏工件不能覆盖。失败保留最后成功页面。当前文件损坏时 HTTP 503，不静默换成别的日期；通过明确 rollback 选择上一版。历史文件不自动删除。

写者意外退出可能留下 `<publish|install>.lock/owner.json`；不按超时抢锁。维护者核对该文件中的 nonce 后，可显式运行 `node skills/trading-center-review/web/cli.ts recover-lock --lock publish --nonce <原 nonce>`。只有原 PID 确认已不存在、nonce 和目录内容均未变化才释放；PID 仍活着、权限不足、缺 owner 文件或内容不明则停止人工处理，不删除整个运行目录。

## 正式发布命令

以下命令在仓库根执行，本机原生 Node 24.12+，数据验证沿用 `/usr/bin/python3` 3.9+（不能用残留 Intel Python）。TypeScript 原生执行不做类型检查，源码提交前另跑 `npm run typecheck` 和 `npm run test:web`。私有输入路径替换成本次真实合格工件，不能使用测试 fixture 充当真实数据。

~~~bash
/Users/archer/.local/bin/node skills/trading-center-review/web/cli.ts publish \
  --daily-input /private/tmp/trading-center-review-runtime/<date>/<run>/daily-dashboard.json \
  --weekly-input /private/tmp/trading-center-review-runtime/<date>/<run>/weekly-dashboard.json \
  --route /review-<date>/
~~~

首次必须有日包或完整已验证展示快照。后续每日 `publish --daily-input ...` 不指定周度时，复用保存的周度内容和原时间；不能把 daily 更新时间改成 weekly 更新时间。若只修正已批准展示内容，可使用 `--display-input <私有展示快照>`，其独立 Schema 不接受账户字段；不能以此确认计划或改写历史成交。

限定标的估值已经写入 SQLite 时，可在现有发布上运行 `publish --enrich-db --route /new-immutable-route/`。该入口只给当前持仓/未持有买入候选附着最新白名单估值；内容确有变化才更新内容生成时间，行情截止和周度生成时间保持原值。它不读取原始响应、不补候选、不推断工具/周期，也不确认计划。

`publish --weekly-key weekly:<start>:<end>` 使用现有 SQLite 的只读事务读取最新 revision；不创建/迁移数据库、不追加周报，不用 `immutable=1` 绕过 WAL。旧库没有保存的 UI 范围声明和人工组织文案不会被补造；当旧 revision 导出的内容仍缺范围/文案时，使用经核验的私有周度展示包，不把 DB 导出直接说成完整 UI 还原。日常展示重载默认复用已发布周度，不自动执行此命令。

~~~bash
/Users/archer/.local/bin/node skills/trading-center-review/web/cli.ts rebuild
/Users/archer/.local/bin/node skills/trading-center-review/web/cli.ts rollback
~~~

`rebuild` 仅从持久展示快照重新渲染；用安装目录中相同脚本执行，可完全不依赖临时运行目录或源仓库。`rollback` 原子切回上一成功发布，保留两版和全部数据库内容。旧日期/旧时间不得经 publish 静默覆盖新记录；历史路由不接受改指向，若需修改使用新的记录地址或更新 `/`。

## 安装、启停、升级与卸载

首次先成功 publish，再安装和启动：

~~~bash
/Users/archer/.local/bin/node skills/trading-center-review/web/cli.ts install
/Users/archer/.local/bin/node skills/trading-center-review/web/cli.ts start
/Users/archer/.local/bin/node skills/trading-center-review/web/cli.ts status
~~~

`install` 固定代码/模板并写入 `~/Library/LaunchAgents/com.marstradingcenter.web-ui.plist`，不会直接启动，也不会接管占用端口的未知进程。当前旧预览必须先核对身份，再由该任务终端 Ctrl+C 交接；不按旧 PID 盲杀。安装写系统用户目录需要对应本地权限，不绕过审批。

~~~bash
/Users/archer/.local/bin/node skills/trading-center-review/web/cli.ts restart
/Users/archer/.local/bin/node skills/trading-center-review/web/cli.ts stop
/Users/archer/.local/bin/node skills/trading-center-review/web/cli.ts uninstall
~~~

`stop` 撤下当前登录会话的 job，保留 plist，下次登录仍会启动；`start` 恢复。`uninstall` 停止 job 并把 plist 移到 owner-only `disabled/` 备份，取消后续登录自启；不删除 DB、发布、日志或安装代码。恢复用 install/start。

升级必须 stop → install → start；不改正在运行的已加载任务。安装失败先 status 核实，不自动清理。旧安装清单和代码保留：需要回退代码时，停止本服务，从核对过的旧 `code/<hash>/web/cli.ts` 执行 install/start。首次安装失败可撤下本 Agent 并恢复原临时预览；不回滚数据库。

运行 readback 至少包括：LaunchAgent running/PID、仅 127.0.0.1 监听、GET `/` 的 HTML 哈希与发布清单一致、健康检查、历史路由及私有文件 404、restart 后新 PID 和同一页面。不能仅凭 plist 或 exit 0 宣称持续稳定。

## 生命周期与人工验收

登录启动、异常退出节流重启、关闭 Codex 不影响服务；注销/关机后不是持续运行的系统 daemon。休眠/重启后的实际可用性未经实测要标 NOT RUN。localhost 不是身份认证，本机其他进程仍可请求页面；不能开放到局域网或互联网。

一标签、正常桌面、100% 缩放；不做手机/窄屏矩阵。完整架构、失败/回滚说明及可执行人工清单位于 `docs/architecture/ts-web-and-obsidian-bridge.md`。原增量报告第 20 节为未部署的 Python 服务历史方案。人工 PASS 必须由用户记录，自动测试和运行回读分开。

Obsidian Bridge 的准确字段、模板和保护边界已获批准；通过独立知识中心 receiver 显式同步，Web 本身不写 Vault。每份复盘仍需“复盘完成”的确切版本确认；没有修改 Codex 调度。常驻展示不等于自动生成新复盘，未来生成运行需显式 publish 才更新 `/`。操作见知识中心交接边界。
