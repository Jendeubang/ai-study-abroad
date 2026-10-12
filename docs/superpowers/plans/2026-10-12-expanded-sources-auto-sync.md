# 港英来源扩展与自动更新实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将港英官方课程来源统一纳入权限约束的来源目录，按周采集并把新增项目与任何变化送交人工审核后再发布。

**Architecture:** 用持久化来源目录登记每个精确官网页面及其解析器；主机使用范围独立于项目记录管理。同步 CLI 调用确定性解析器，存储短证据并生成初次项目候选或字段变更候选；管理员审核后才写入可检索项目和证据。Windows Task Scheduler 与 Linux cron 调用同一个 CLI，FastAPI 不承担定时任务。

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, SQLite, httpx, BeautifulSoup, pytest, Windows PowerShell Task Scheduler, Linux cron/flock.

**Spec:** [docs/superpowers/specs/2026-10-12-expanded-sources-auto-sync-design.md](../specs/2026-10-12-expanded-sources-auto-sync-design.md)

## Global Constraints

- 只请求来源目录中的精确 HTTPS URL，不发现链接，不访问登录或申请门户。
- 每个主机先核验 `robots.txt`；请求串行且至少间隔 2 秒；403、拒绝、跳转、超时或结构变化均停止该来源。
- 新来源默认为停用和未核实；过期、未核实或范围不匹配时不发送请求。
- 单个招生事实只保存明确值、适用条件、最多 500 字证据、来源 URL、SHA-256 和采集/核对时间；不保存网页全文。
- 自动任务只生成初次导入候选、字段变化候选和采集状态；人工批准前不得更改可检索值。
- 本地研究权限不得推导公开展示权限；公开部署不返回仅限本地研究的数据。
- 自动化测试仅使用合成 HTML 和 MockTransport，不访问大学网站。

## Review Focus

- 未核实、过期或用途不匹配的来源不得产生 HTTP 请求；由 Task 1 与 Task 4 测试。
- 新项目首次采集必须进入独立的待审候选，只有批准后才创建 Program 和 EvidenceFact；由 Task 3 测试。
- 页面标题或结构变化必须停止解析并保留旧发布值；由 Task 2 与 Task 3 测试。
- 字段只在适用条件一致时比较、替换和失效旧证据；由 Task 3 测试。
- 重叠调度不能并发运行，来源失败不能阻止后续主机复查；由 Task 4 与 Task 5 测试。

---

### Task 1: 持久化来源目录与权限门控

**Files:**
- Create: `app/sources/__init__.py`
- Create: `app/sources/catalog.py`
- Create: `migrations/versions/0004_source_catalog.py`
- Modify: `app/models.py`
- Modify: `app/services/data_review_service.py`
- Modify: `app/routers/admin.py`
- Modify: `app/main.py`
- Test: `tests/test_source_catalog.py`
- Test: `tests/test_migrations.py`

**Interfaces:**
- `SourceDefinition`: persistent page-level definition with `source_id`, `programme_key`, `host`, exact `url`, programme metadata, `parser_key`, allowed fields, and `enabled`.
- `source_specs() -> tuple[SourceSpec, ...]`: returns the existing 8 source pages plus candidate documents for HKU, CUHK, Oxford, and UCL. HKU application-procedure and programme-detail pages have separate source IDs and a shared `programme_key`.
- `permission_allows(session, source, *, purpose, now) -> bool`: true only for enabled definitions with a non-expired permission covering the requested purpose.
- Source management APIs list and update registered hosts even if no Program exists for the host.

- [ ] **Step 1: Write failing tests** for exact URL catalogue validation, candidate sources initially disabled, permissions saved before Program creation, stale/unverified scope denial, and public scope not inferred from local scope.
- [ ] **Step 2: Run tests and verify expected failures** with `pytest tests/test_source_catalog.py tests/test_migrations.py -q`.
- [ ] **Step 3: Add `SourceDefinition` and migration**; seed the source catalogue idempotently during app startup without granting rights or enabling new candidates.
- [ ] **Step 4: Refactor permission checks and admin `/api/admin/sources`** to validate against catalogue hosts rather than imported Program hosts.
- [ ] **Step 5: Run targeted tests and migration suite**; verify unverified candidate sources remain request-ineligible.
- [ ] **Step 6: Commit** `feat: add permission-gated source catalogue`.

### Task 2: Deterministic candidate-page parsers

**Files:**
- Create: `app/sources/parsers/__init__.py`
- Create: `app/sources/parsers/hku.py`
- Create: `app/sources/parsers/cuhk.py`
- Create: `app/sources/parsers/oxford.py`
- Create: `app/sources/parsers/ucl.py`
- Modify: `scripts/crawl_official_requirements.py`
- Modify: `scripts/crawl_additional_programs.py`
- Test: `tests/test_source_parsers.py`

**Interfaces:**
- `parse_source_html(source: SourceDefinition, html: str) -> ParsedSourceRecord` dispatches only known `parser_key` values.
- `ParsedSourceRecord` carries programme metadata and explicit facts; each fact has a field name, typed value, applicability scope, excerpt, and exact source URL.

- [ ] **Step 1: Add synthetic HTML tests** for each source family with valid explicit data, optional missing fields, year/population scopes, malformed scores, and changed title/section.
- [ ] **Step 2: Run parser tests and verify they fail** before implementing adapters.
- [ ] **Step 3: Implement the four adapters**; never infer unspecified values or use AI extraction. HKU page facts remain separately attributed to the page that states them.
- [ ] **Step 4: Add a dispatch test** rejecting unknown parser keys and any field outside that source's allowed-field list.
- [ ] **Step 5: Run parser and existing crawler tests**; no live network is used.
- [ ] **Step 6: Commit** `feat: parse approved UK and HK programme pages`.

### Task 3: Unified sync CLI, run history, and review candidates

**Files:**
- Create: `scripts/sync_program_sources.py`
- Create: `migrations/versions/0005_sync_runs_and_program_candidates.py`
- Modify: `app/models.py`
- Modify: `app/services/data_review_service.py`
- Modify: `scripts/sync_additional_programs.py`
- Modify: `app/services/official_import_service.py`
- Test: `tests/test_sync_program_sources.py`
- Test: `tests/test_data_review.py`
- Test: `tests/test_reviewed_import.py`

**Interfaces:**
- `SyncRun`: records run ID, trigger, times, and eligible/checked/changed/failed/skipped/pending counts.
- `ProgramCandidate`: denied-by-default initial programme data with source IDs, typed facts, evidence, scopes, snapshot digests, retrieved time, and review state.
- `sync_sources(session, client, specs, *, now, pause) -> SyncSummary` executes eligible sources sequentially and returns source-level statuses.
- `stage_source_record(session, record, run_id) -> int` creates a new-program candidate or changed-field/page candidates, never mutating published values.

- [ ] **Step 1: Write failing tests** for initial candidate staging, field change staging, unchanged values, page-only digest change, permission skips with zero HTTP calls, per-source failure continuation, and no published-value mutation before approval.
- [ ] **Step 2: Run sync tests and verify expected failures**.
- [ ] **Step 3: Add migration and review service support** for SyncRun and ProgramCandidate; approving a new programme creates Program plus scoped EvidenceFact/ApplicationRound rows in one transaction.
- [ ] **Step 4: Implement the unified CLI** over the catalogue; apply robots, exact-host URL validation, no redirects, response limits, sequential 2-second delay, and per-source status recording.
- [ ] **Step 5: Preserve the existing sync command as a compatibility wrapper** that delegates to the unified runner.
- [ ] **Step 6: Run review, importer, crawler, and sync tests**; verify rerunning the same snapshot is idempotent and a failed host does not stop later hosts.
- [ ] **Step 7: Commit** `feat: stage source sync changes for review`.

### Task 4: Source permissions, first-import review, and sync overview UI

**Files:**
- Modify: `app/routers/admin.py`
- Modify: `app/services/data_review_service.py`
- Modify: `app/templates/admin_reviews.html`
- Modify: `app/static/admin_reviews.js`
- Modify: `app/static/style.css`
- Test: `tests/test_data_review.py`
- Test: `tests/test_frontend.py`

**Interfaces:**
- `GET /api/admin/sync-runs`: latest 30 run summaries.
- `GET /api/admin/sources`: registry and source permission state, including candidates without Programs.
- `GET /api/admin/program-candidates`: pending new-program candidates.
- `POST /api/admin/program-candidates/{id}/approve` and `/reject`: require reviewer and note; approval requires fresh evidence and permission for the requested purpose.

- [ ] **Step 1: Write failing API/UI tests** for permission state before project import, unverified candidate visibility, source enable/disable, new-program approval/rejection, and sync failure/staleness display.
- [ ] **Step 2: Run tests and verify expected failures**.
- [ ] **Step 3: Add token-protected routes** with strict Pydantic schemas and transactional review decisions.
- [ ] **Step 4: Update admin page** with source scope/expiry, candidate review, last run summary, per-source result, last success, and pending review counts.
- [ ] **Step 5: Verify all inserted data uses text nodes/safe URL validation**; no API token enters HTML or persistent browser storage.
- [ ] **Step 6: Run admin and frontend tests**.
- [ ] **Step 7: Commit** `feat: manage source review and sync status`.

### Task 5: Weekly Windows/Linux scheduling and operations documentation

**Files:**
- Create: `scripts/register_weekly_sync_task.ps1`
- Create: `scripts/remove_weekly_sync_task.ps1`
- Modify: `.env.example`
- Modify: `README.md`
- Test: `tests/test_sync_scheduling.py`

**Interfaces:**
- Windows task name: `StudyCompassOfficialSync`; runs the project virtualenv's `python -m scripts.sync_program_sources --all` every Monday at 08:00 local time; ignores overlapping instances; appends non-sensitive output to the project log directory.
- Linux cron example sets `TZ=Asia/Shanghai` and uses `flock -n` to prevent overlap while invoking the same CLI.
- Registration/removal scripts are explicit user commands; app startup never installs or triggers a schedule.

- [ ] **Step 1: Add tests** checking task scripts target the current repository and venv paths, use Monday 08:00, prevent overlapping runs, and do not include API/admin credentials.
- [ ] **Step 2: Run scheduling tests and verify they fail** before adding the scripts/docs.
- [ ] **Step 3: Implement register/remove PowerShell scripts** with validation, clear errors, least-privilege current-user scheduling, and no implicit elevation.
- [ ] **Step 4: Document Linux cron/flock setup, manual run, removal, log location, permission prerequisites, failure handling, and public-display gate**.
- [ ] **Step 5: Run all tests and `node --check` on admin JS**; inspect CLI help and scheduling scripts without registering an OS task.
- [ ] **Step 6: Commit** `feat: schedule weekly permission-gated source sync`.

## Final Verification

- Run `python -m pytest -q` with a project-local temporary base directory; all tests must pass without outbound university requests.
- Run `python -m compileall -q app scripts tests` and `node --check app/static/admin_reviews.js`.
- Run the local CLI against mock transports and a temporary SQLite database; verify permission-denied sources cause zero requests, changes remain pending, and approved candidates become searchable with field-level evidence.
- Start the app and verify admin sync overview, source permission expiry behavior, and public-display filtering.
- Confirm no live source data, secrets, or local database files were added to Git.
