# 港英留学项目来源扩展与自动更新设计

## 目标

扩大香港与英国计算机、数据科学和人工智能硕士项目的官网覆盖，并让已核准来源按周重新采集。每个已发布招生事实都能追溯到精确官网页面、适用范围、证据摘录和采集时间；任何新值在人工批准前不得覆盖当前检索数据。

## 当前状态

- 数据库中有 8 个官网已核对项目、8 条最近成功采集记录、0 条待审核变化。
- 采集器覆盖 6 个港校固定课程页和 2 个剑桥固定课程页；变更同步脚本当前只支持港校 6 页。
- 来源权限台账中，港城大和港科大为“未核实”，剑桥登记为“本地非商业研究”。
- 复查依赖手动 CLI 和外部 Task Scheduler/cron；没有来源统一目录，也没有采集批次摘要。
- 当前来源检查仅能从已经导入的官网项目推导主机，无法在导入项目前完成新来源授权登记。

## 已确认的产品范围

1. 首批地区是香港和英国。
2. 现有 8 个项目保留为基线；新增候选来源为以下 4 个官方课程页。
   - HKU MSc Computer Science: [2027/28 application procedure](https://master.cds.hku.hk/msccs/about/msc-in-computer-science-application-procedure-2027-28/) and [programme details](https://master.cds.hku.hk/msccs/admission/application-details/)
   - CUHK MSc Computer Science: [entry requirements](https://msc.cse.cuhk.edu.hk/?lang=en&page_id=136)
   - Oxford MSc Advanced Computer Science: [course page](https://www.ox.ac.uk/admissions/graduate/courses/msc-advanced-computer-science)
   - UCL Computer Science MSc: [2027/28 course page](https://www.ucl.ac.uk/study/prospective-students/graduate/courses/computer-science-msc)
3. These are candidate pages only. No permission to collect, store, display, or reuse their content is inferred from their being public or from `robots.txt`.
4. Candidate sources start unverified and disabled. A source can be collected only after its reuse scope, terms URL, reviewer, review date, and evidence are recorded. Public display requires a separate `public_display` scope; local research permission never implies public permission.
5. Collection runs weekly, default Monday at 08:00 Asia/Shanghai, through Windows Task Scheduler locally or cron on Linux. The FastAPI process does not own the scheduler.
6. Every changed fact or page digest enters human review. Failed or stale checks create visible review warnings. No update directly replaces a published fact.

## Source lifecycle and data flow

`candidate / unverified -> terms reviewed -> enabled -> robots check -> fetch exact allowlisted page -> deterministic parser -> snapshot and field evidence -> compare -> review candidate -> approve or reject -> publish approved value`

- A registry record identifies `source_id`, host, exact HTTPS page URL, programme identity, parser key, fields the parser may emit, terms URL, and enabled state.
- Rights are tracked independently from programmes so a host can be reviewed before its first course is imported. The record includes scope, checked time, reviewer, and review note. Rights older than 365 days are expired for collection.
- Only the exact registered URL is fetched. Redirects, robots denial, 403, oversized responses, timeouts, content-type mismatch, changed programme title, or parser structure mismatch stop that source. No links are followed and no access control is bypassed.
- A parser may emit only fields explicitly present on the source page. Each field has its own value, applicability scope (for example, year, full/part time, local/international student), concise evidence excerpt, source URL, page SHA-256, and retrieved time. Full HTML is not stored.
- Initial programmes also enter a denied-by-default review queue. Approving an initial record creates the official programme and evidence rows; it does not import demo values or infer absent facts.
- Existing programmes are diffed per field. Changed fields, new/removed application rounds, and page-only digest changes become `ReviewCandidate` rows. Approving a fact retires the old active evidence and publishes the approved value; rejection preserves the published version and records the decision.
- A source failure is stored as a `SourceCheck` failure and marks its data as needing review. Three consecutive failures are not required to show the warning; a single latest failure is visible. Evidence older than 90 days is marked stale.

## Components

### Unified source catalogue

Move source declarations out of collector-specific `SOURCES` dictionaries into a shared catalogue. Keep parsing logic in adapters keyed by `parser_key` (existing Cambridge, CityU and HKUST adapters plus candidate adapters for HKU, CUHK, Oxford and UCL). Do not add generic crawling, discovery, or LLM-based page extraction.

### Rights and review administration

Extend the existing admin source workflow to list registry entries before programmes exist. Keep source permission review distinct from programme data review. The admin page shows terms URL, scope, reviewer, checked/expiry dates, enabled state, last sync status, and last successful retrieval. Any approval is denied if the source permission is missing, unverified, expired, or does not cover the intended display scope.

### Synchronization runner

Add a single CLI entry point that:

1. creates a run summary;
2. selects only enabled, permission-valid sources;
3. checks each site's `robots.txt` and fetches pages sequentially with a minimum 2-second inter-request delay;
4. records `unchanged`, `changed`, `failed`, or `skipped_permission` per source;
5. stages evidence and review candidates without publishing values;
6. exits nonzero for failed source checks and prints only run totals and source IDs.

The Windows installer registers one weekly current-user task with overlapping runs ignored and append-only logs under the project data/log directory. Linux documentation gives a `flock -n` cron example. The same CLI works for manual checks. No task is installed implicitly by application startup.

### Run history and stale status

Add a `SyncRun` record with run ID, start/finish time, trigger, and counts for eligible, checked, changed, failed, permission-skipped, and pending-review sources. Link individual `SourceCheck` rows to a run. The review dashboard displays the latest run and per-source status without exposing full page text, user queries, API keys, or proxy credentials.

## Initial supported facts

Extract where explicitly shown: programme name, university, country/city, degree, duration, tuition amount/currency/year/study mode/student fee status, intake, each explicitly named application round and dates, IELTS/TOEFL minimums and score scale, and academic entry wording. Keep unknown values null. Ranking and admission probability are excluded; neither is inferred from admission pages. Application portals and account-only content are out of scope.

## Safety, privacy, and operational limits

- Per-host rights are reviewed separately for local research and public display. Source code publication does not grant rights to fetched content.
- The runner sends a descriptive user agent, honours robots rules as an access constraint, waits between requests, uses HTTPS only, disables redirects, enforces response size/time limits, and does not retry around denial or blocking.
- Do not store whole pages, personal application information, or applicant records. Store only normalized facts, short excerpts, source URL, digest, and times.
- Collection runs only when explicitly scheduled by Task Scheduler/cron. Application startup never triggers network collection.
- Public deployment must filter out programmes whose source permission does not include public display.

## Acceptance criteria

1. The unified catalogue lists the existing sources and four new candidate pages; new candidates start disabled and cannot make HTTP requests before permission approval.
2. An administrator can record and expire source permission before a programme exists; outdated or unverified permissions cause collection to skip without requesting the site.
3. Deterministic parser tests cover supported facts, missing values, applicability scope, title mismatch, page structure changes, and no inferred values for all four new source families.
4. Initial programme data enters human review before becoming searchable; review creates field evidence and preserves source provenance.
5. A no-change sync records `unchanged`; changed values and page-only changes create review candidates; neither changes published values before approval.
6. A collection failure records a failed source check and surfaces a stale/review warning without erasing published evidence.
7. Sync-run counts and source statuses are visible in the admin page; a CLI run is idempotent and reports permission skips and failures.
8. Windows and Linux scheduling instructions target Monday 08:00 Asia/Shanghai, avoid overlapping runs, use local project paths, and keep logs free of secrets and full page content.
9. Public serving excludes local-research-only data.
10. Tests use synthetic HTML and mock HTTP transports; routine tests do not make live requests to university websites.

## Not in scope

- Automatically deciding legal permissions or treating robots rules as reuse authorization.
- Automatically publishing changes, estimating admission probability, synthesizing rankings, currency conversion, or filling missing facts from demo data.
- Arbitrary university discovery, crawling linked pages, login portals, application systems, or third-party aggregators.
- Deploying a new public service or scheduling task automatically during development.
