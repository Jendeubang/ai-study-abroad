# StudyCompass AI：留学项目检索

这是一个可本地运行的 FastAPI 留学项目检索应用。数据库初始项目为**完全模拟**的数据；经过人工核对后，也可将少量官网招生要求摘要导入本地检索数据库。每条结果显示“模拟项目”或“官网已核对 · 招生要求”，并附来源和数据更新时间。应用支持自然语言推荐与基于当前结果的问答。首页采用已选定的 A 版界面，详情页沿用相同样式。已有本地 token 保护的数据审核页；Docker 属于后续阶段。

## 环境要求与变量

- Python 3.11+，SQLite 编译时须包含 FTS5。
- 将 `.env.example` 复制为 `.env`。`DATABASE_URL` 默认指向项目目录内 `study_abroad.db`；目前仅支持 SQLite。
- `ALLOWED_ORIGINS` 为以逗号分隔的精确前端来源。默认空值，不允许跨域浏览器调用。
- `REQUEST_TIMEOUT_SECONDS` 默认 60 秒，为解析和推荐理由两次顺序 AI 请求预留时间；浏览器请求等待 65 秒。SQLite 忙等待最多 5 秒。
- `AI_API_KEY`、`AI_BASE_URL`、`AI_MODEL` 配置后端模型调用。项目默认接入 DeepSeek OpenAI 兼容接口：`AI_BASE_URL=https://api.deepseek.com`、`AI_MODEL=deepseek-flash`；密钥只放 `.env`，不会发送到浏览器。可在 [DeepSeek 开放平台](https://platform.deepseek.com/)创建 API Key。
- `AI_TIMEOUT_SECONDS` 默认 25 秒，单次模型请求超时或 HTTP 错误时自动降级。DeepSeek JSON Output 由服务端请求 `response_format: {"type":"json_object"}`，模型提示中也要求返回 json；API 暂不可用或输出无效时保留规则检索结果。
- `ADMIN_TOKEN` 保护本地数据审核 API。请设置长随机值，仅保存在被 Git 忽略的 `.env` 中；审核页要求人工输入，服务端不会把 token 渲染到页面。未配置时管理 API 返回 503。
- `VECTOR_BACKEND=hash` 默认在本地生成轻量文本向量。可改为 `sentence_transformers` 使用本地多语言句向量模型；`VECTOR_MODEL_ID` 指定首次手动下载的模型，`VECTOR_MODEL_PATH` 指定保存在本机的目录。模型文件不提交到 Git。

### 配置 DeepSeek API

1. 在 [DeepSeek 开放平台](https://platform.deepseek.com/)创建 API Key。
2. 只把密钥写入项目根目录 `.env` 的 `AI_API_KEY`；保持 `AI_BASE_URL=https://api.deepseek.com` 和 `AI_MODEL=deepseek-flash`。
3. 保存后重启 Uvicorn，使后端重新读取环境变量。不要把 API Key 放进前端代码、浏览器请求或 Git 提交。
4. 调用 `POST /api/recommend`，响应中的 `data.ai_available` 为 `true` 表示该请求成功使用 AI；否则应用会返回规则检索结果并提示降级。

## 本地运行

在 `ai-study-abroad` 目录中运行。以下命令在 Windows CMD 和 PowerShell 都可用，无需先激活虚拟环境：

```bat
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --no-access-log
```

如果要激活虚拟环境，CMD 使用 `.venv\Scripts\activate.bat`，PowerShell 使用 `.\.venv\Scripts\Activate.ps1`。macOS/Linux 则使用 `.venv/bin/python -m ...` 或 `source .venv/bin/activate`。启动时应用也会运行 Alembic 迁移；已由旧版启动逻辑创建的完整数据库会安全登记初始版本，不会清空数据。首次启动会将 `data/programs.json` 中的模拟记录装入空数据库；再次启动不会重复插入。访问 `http://127.0.0.1:8000/docs` 查看交互式 API 文档。

打开 `http://127.0.0.1:8000/` 使用正式首页。输入留学需求后可选择“仅检索”或“智能推荐”；结构化筛选和排序会与当前推荐条件一起生效，不会被清空。点击项目标题进入详情页。窄屏下可展开筛选栏。B/C 早期方案仍可通过 `/preview/research` 和 `/preview/studio` 查看。

当前页面由 Jinja2、Tailwind CSS、原生 JavaScript 和本地 `app/static/style.css` 渲染，启动时不依赖外部 JavaScript 或 CSS CDN。仓库已包含构建好的 `app/static/tailwind.css`，只运行 Python 即可展示页面。修改 Tailwind 类名后，使用 Node.js 运行 `npm ci` 和 `npm run css:build` 更新静态文件；开发时可用 `npm run css:watch`。构建时禁用 Tailwind Preflight，以保留现有页面的基础样式。

三个页面的定位：

| 方案 | 页面重点 | 适合的使用场景 |
| --- | --- | --- |
| A 清晰列表 | 自然语言输入与逐条来源阅读 | 首次了解留学项目的用户 |
| B 检索工作台 | 紧凑筛选、排序与逐项比较 | 反复缩小申请名单的用户 |
| C 探索卡片 | 卡片浏览与更轻的问答区域 | 移动端探索和初步发现 |

布局与交互参考了 [Vercel Chatbot](https://github.com/vercel/chatbot) 的问答入口、[shadcn/ui](https://github.com/shadcn-ui/ui) 的组件组织，以及 [Meilisearch](https://github.com/meilisearch/meilisearch) 的检索产品思路。页面与代码为本项目重新实现，未复制这些项目的组件。

若当前 Python 环境的 `venv`/`ensurepip` 已损坏，可在 PowerShell 中把依赖安装到项目内，临时运行：

```powershell
python -m pip install --target .deps -r requirements.txt
$env:PYTHONPATH = '.deps'
python -m alembic upgrade head
python -m uvicorn app.main:app --no-access-log
```

## API 示例

所有接口返回 `{ "code": 0, "message": "ok", "data": ..., "request_id": "..." }`。错误也使用相同结构，并设置对应 HTTP 状态码。

```bash
curl http://127.0.0.1:8000/api/health
curl "http://127.0.0.1:8000/api/programs?q=computer%20science&country=%E8%8B%B1%E5%9B%BD&page=1&page_size=10"
curl http://127.0.0.1:8000/api/programs/1
curl -X POST http://127.0.0.1:8000/api/parse -H "Content-Type: application/json" -d '{"query":"英国 CS 硕士 GPA 3.6/4 雅思7"}'
curl -X POST http://127.0.0.1:8000/api/recommend -H "Content-Type: application/json" -d '{"query":"英国 CS 硕士 GPA 3.6/4 雅思7","page_size":10}'
curl -X POST http://127.0.0.1:8000/api/recommend -H "Content-Type: application/json" -d '{"query":"英国 CS 硕士","filters":{"country":"香港","sort":"ranking"}}'
curl -X POST http://127.0.0.1:8000/api/chat -H "Content-Type: application/json" -d '{"message":"第一个项目的学费是多少？","program_ids":[1]}'
```

搜索参数：`q`、`country`、`degree`、`discipline`、`gpa`、`ielts`、`budget`（人民币）、`intake`、`ranking_max`、`source_type`（`all`、`official`、`demo`）、`retrieval`（`hybrid`、`keyword`、`vector`）、`page`、`page_size`（最多 100）、`sort`。排序只接受 `relevance`、`deadline`、`updated_at`、`ranking`、`tuition_asc`、`tuition_desc`。学费排序按币种分组后按金额排列，不能作为跨币种价格比较。

搜索词使用 SQLite FTS5；中文词使用参数化的子串匹配。结构化筛选在 SQLAlchemy 中参数化。GPA 在 4、5、100 分制之间仅做粗略归一，缺失要求和预算币种不一致会降低匹配分并给出提示，不会淘汰项目。匹配分仅用于检索排序，不代表录取概率。当前示例数据的排名也是模拟值。

### 向量检索

首页“检索方式”默认选择综合检索：合并关键词与向量命中，仍先应用地区、学位、专业、入学季及数据来源筛选，再按规则分和文本相似度排序。选“向量”仅显示向量命中的项目；选“关键词”沿用原检索。`data.retrieval_mode` 表示实际使用方式；向量模型或索引不可用时自动降级为关键词。页面显示降级提示，项目卡片里的“条件分”始终是原有规则分，不是语义相似度或录取概率。智能推荐也会用原始需求做向量召回，再由已有规则与 AI 生成带项目来源的推荐依据。

默认 `hash` 后端只用中文字符片段和英文词形成确定性向量，能提供轻量的近似文本匹配，但不理解同义词。真正的语义检索需安装可选依赖并显式下载模型：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-vector.txt
# 在 .env 中设置 VECTOR_BACKEND=sentence_transformers 后运行：
.\.venv\Scripts\python.exe -m scripts.build_vector_index --download-model
# 后续人工核对导入或离线更新项目后重建；日常运行不需重复下载：
.\.venv\Scripts\python.exe -m scripts.build_vector_index
```

默认模型是 [多语言 MiniLM 句向量模型](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2)。下载前检查模型使用条款；命令会下载模型到 `.models/`，网页请求只读取本地文件，不把项目数据发送给模型下载站。`hash` 后端会在应用启动及管理页批准项目变更后自动更新索引；`sentence_transformers` 后端需要手动执行上面的重建命令，索引在重建前不会使用已变化的项目向量。索引仅包含已发布项目字段，不包含未审核候选或官网网页全文。

```bash
curl "http://127.0.0.1:8000/api/programs?q=computer%20science&retrieval=hybrid&source_type=official"
```

`POST /api/parse` 返回结构化条件。`POST /api/recommend` 先解析、检索和规则打分，再让 AI 从现有字段中选择推荐依据。服务端逐项校验项目 ID 和字段值，并用数据库值生成最终推荐文本和来源引用。AI 超时、限流、非法 JSON 或引用虚构数据时，响应保留规则检索结果，标记 `ai_available=false` 和 `AI 推荐暂不可用`。`POST /api/chat` 仅接受客户端选定的项目 ID，依据真实字段生成带 `source_url` 的回答；无依据时返回“根据当前检索结果无法确定”。AI 接口每 IP 每分钟最多 10 次，普通接口最多 60 次。

## 测试

```powershell
python -m pytest tests -q
```

## 官方招生要求采集脚本

`scripts/crawl_official_requirements.py` 目前只支持两条预设的剑桥大学公开课程要求页：[高级计算机科学 MPhil](https://www.postgraduate.study.cam.ac.uk/courses/directory/cscsmpacs/requirements) 与 [科学计算 MPhil](https://www.postgraduate.study.cam.ac.uk/courses/directory/pcphmpscm/requirements)。采集前会检查该站点的 `robots.txt`，限定官网域名和页面、请求间隔至少 2 秒；遇到禁止抓取、403、跳转、网页结构变化或字段缺失会停止，不尝试绕过限制。脚本仅保存课程名、学历门槛、雅思及托福分数等明示事实，以及来源链接、抓取时间和页面哈希；不保存网页全文，也不访问申请系统、个人资料或登录区域。

在项目目录执行：

```powershell
.\.venv\Scripts\python.exe -m scripts.crawl_official_requirements --course all
# 本地网络需要代理时，使用本机回环代理，例如：
.\.venv\Scripts\python.exe -m scripts.crawl_official_requirements --course all --proxy http://127.0.0.1:3067
```

可将 `--course all` 改成 `--course cambridge-acs` 或 `--course cambridge-scientific-computing`；`--output 路径` 可指定输出文件。默认输出为 `data/official_requirements/latest.json`，已加入 `.gitignore`。代理只接受本机 HTTP 回环地址，不接受外部代理或代理凭据。脚本需要联网；无法读取 `robots.txt` 时会停止。

这份采集结果供**本地非商业研究与人工核对**，爬虫不会自动导入数据库。剑桥大学[网站使用条款](https://www.cam.ac.uk/about-this-site/terms-and-conditions)对资料的复制和商业再利用设有限制；若用于公开网站、生产服务、商业用途或扩大采集范围，应先核实并取得所需授权。输出中的 `manual_review_required=true` 和 `subject_requirement_needs_review=true` 提醒核对专业背景等未提取条件。英国学位等级不被推断为 GPA；费用、截止日期和录取概率也不在本脚本的采集范围内。

### 核对后导入本地检索库

先运行上述采集命令，再生成**默认不批准**的核对清单：

```powershell
.\.venv\Scripts\python.exe -m scripts.review_and_import prepare
```

打开 `data/official_requirements/review.json`，对每条记录点击 `source_url` 核对课程名、学历等级和各项语言成绩，并检查网站使用条款。确认只在允许的本地非商业范围使用后，将顶层 `rights_confirmed` 改为 `true`；只把确认无误的记录的 `approved` 改为 `true`，并为每条填写至少 8 字的 `review_note`。未批准的记录不会导入。清单绑定采集快照的摘要；采集内容发生变化后，须生成新的清单，不能沿用旧批准。默认清单已存在时，`prepare` 不会覆盖它；可用 `--review 另一文件.json` 保存新清单。

核对完成后执行：

```powershell
.\.venv\Scripts\python.exe -m scripts.review_and_import import
```

脚本使用与网页相同的 `.env` `DATABASE_URL`。它会自动运行 Alembic 迁移，按课程来源 ID 更新同一条官网项目；同一网页快照重复导入不会生成重复项目或刷新核对时间。导入后刷新首页，在“数据来源”中选择“官网已核对”；卡片和详情页会显示大学官网链接、官网采集时间及人工核对时间。**“数据更新时间”指最近一次采集，不是官网页面的发布日期。** 未采集的学费、截止日期、排名等继续显示为缺失，不会从模拟项目填充。采集 JSON 和核对清单均被 Git 忽略，不会随代码上传。

### 扩充香港院校课程

`scripts/crawl_additional_programs.py` 另外预设了[香港城市大学计算机系](https://www.cs.cityu.edu.hk/academic-programmes/msc-computer-science/admissions)的计算机科学、人工智能和网络安全硕士，以及[香港科技大学工程学院](https://seng.hkust.edu.hk/academics/taught-postgraduate/msc-it)的信息技术、大数据技术和人工智能硕士，共 6 个固定课程页面。只采集港城大页面明确列出的雅思总分，及港科大页面明确列出的 **2027/28 全日制学费和入学季**；其他字段保持缺失。港城大雅思要求有适用条件，托福出现新旧两种分制，因此当前不会把托福数字写入旧的 0–120 分字段。港科大页面有多轮申请日期，当前不将其中任一日期填为通用截止日期。

脚本对每个官网先读取 `robots.txt`，仅请求预设的 HTTPS 域名与页面，至少间隔 2 秒；遇到禁止抓取、跳转、403、页面结构变化或超大响应时停止，不尝试绕过限制。只在本地保存短证据、来源网址、抓取时间和页面哈希，不保存网页全文，也不会自动导入数据库。

```powershell
.\.venv\Scripts\python.exe -m scripts.crawl_additional_programs --course all --proxy http://127.0.0.1:3067
.\.venv\Scripts\python.exe -m scripts.review_additional_programs prepare
```

不需要代理时省略 `--proxy`。`--course` 可指定单个来源 ID，使用 `--help` 查看列表。默认采集文件是 `data/official_requirements/additional_latest.json`，核对清单是 `data/official_requirements/additional_review.json`；两者都不会提交到 Git。重新采集后应使用另一个 `--review` 文件名生成新清单，旧清单绑定旧快照，不能重复批准新数据。

逐条打开清单里的 `source_url`，对照 `evidence` 核对项目名称、雅思分数或学费与入学季，并核查拟批准课程所属网站的最新版权和使用条款，包括[港城大版权说明](https://www.cityu.edu.hk/web/copyright)。确认当前**本地非商业研究**用途得到允许后，才把顶层 `rights_confirmed` 设为 `true` 并在 `rights_note` 写明已核查的网站（至少 8 字）；仅把逐项核实的记录设为 `approved: true` 并填写至少 8 字的 `review_note`。不能确认使用权限或事实时，保持未批准。准备完成后先验证，再导入：

```powershell
.\.venv\Scripts\python.exe -m scripts.review_additional_programs validate
.\.venv\Scripts\python.exe -m scripts.review_additional_programs import
```

导入采用来源 ID 更新，不覆盖模拟数据，也不自动填补未采集字段。若未来要将这些数据公开部署或用于商业服务，需另外确认相应的再利用权限。

### 逐字段证据、变化核对与申请轮次

现有 6 条港校项目可以把**此前已批准且摘要仍与数据库一致**的快照迁入逐字段证据表。此命令不会重新批准、重抓官网或改变检索项目值：

```powershell
.\.venv\Scripts\python.exe -m scripts.backfill_additional_evidence
```

证据表保留数值、简短官网证据、来源链接、页面摘要、采集时间、人工核对时间和适用条件。当前港科大学费明确限定为 **2027/28、全日制、HKD**；港城大雅思限定于需要提交英语成绩的申请人。申请轮次有独立表，不把任一轮截止日期写入项目的通用 `deadline` 字段。原有 2 条剑桥项目目前没有可用于此命令的港校格式证据，详情页会明确提示逐字段证据待补录。

在 `.env` 配置 `ADMIN_TOKEN` 并重启服务后，打开 `http://127.0.0.1:8000/admin/reviews`。先为每个官网主机登记条款链接、核查人、核查说明及**本地非商业研究**使用范围。台账中的公开展示范围必须单独核实并明确登记，不能由本地研究范围推断。管理员 token 只在当前页面内存中使用，不存到浏览器持久存储；管理 API 要求 `X-Admin-Token` 请求头。

台账登记完成后可手动运行复查：

```powershell
.\.venv\Scripts\python.exe -m scripts.sync_additional_programs --course all
# 本机需要代理时：
.\.venv\Scripts\python.exe -m scripts.sync_additional_programs --course all --proxy http://127.0.0.1:3067
```

此命令仍只访问脚本内固定的 6 个页面，逐站核对使用范围并检查 `robots.txt`。新字段值或页面摘要变化会生成待审核项；采集失败会记录状态。**这些情况都不会自动覆盖已发布值。** 审核页显示已发布值、待审核值、适用条件、官网短证据和原页链接；批准或驳回必须填写核查人和说明，并写入审核记录。审核页还可在最近一次成功采集的页面下人工登记多个申请轮次，逐条批准。

详情页与结果卡片会提示待审核变化、最近一次采集失败，以及逐字段证据超过 90 天未核对。申请轮次到期会在详情页标注。当前统一同步目录含既有港校来源和港英候选课程页；HKU、CUHK、Oxford、UCL 候选来源默认关闭。新来源只有在登记了尚未过期且覆盖相应用途的来源权限后，才允许请求。

### 港英官网每周同步

统一同步器只访问来源目录中固定的 HTTPS 页面，检查 `robots.txt`、串行请求并遵守间隔；不会发现或遍历新链接。它只提取页面明确列出的招生事实，不保存网页全文。首次发现的项目和所有后续字段变化都进入审核队列，批准前不会改变检索数据库。公开展示用途必须逐站单独核验并登记，不能从本地研究权限推断。

确认 `.venv` 已安装项目依赖，并已在管理页为目标网站登记用途权限后，可手动运行：

```powershell
.\.venv\Scripts\python.exe -m scripts.sync_program_sources --all
# 仅同步指定来源：
.\.venv\Scripts\python.exe -m scripts.sync_program_sources --source hku-msccs-application
```

Windows 可显式注册当前用户的计划任务，每周一上午 08:00（Asia/Shanghai）。注册脚本要求 Windows 时区设为 `China Standard Time`，避免系统时区不同导致运行时刻偏差：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\register_weekly_sync_task.ps1
```

此任务以当前用户的有限权限运行，忽略重叠运行；不会在应用启动时自动安装。脚本通过项目虚拟环境运行同步器，日志追加到 `logs/source-sync.log`（该目录不会提交到 Git）。查看管理页 `/admin/reviews` 的“每周同步记录”和来源清单可检查运行摘要、逐来源失败/跳过状态、最近成功时间和待审核项目数。删除计划任务：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\remove_weekly_sync_task.ps1
```

Linux 可用 `flock` 防止并发，并明确设定上海时区。将路径替换为部署目录后加入用户 crontab：

```cron
TZ=Asia/Shanghai
0 8 * * 1 cd /srv/studycompass/ai-study-abroad && /usr/bin/flock -n /tmp/studycompass-source-sync.lock /srv/studycompass/ai-study-abroad/.venv/bin/python -m scripts.sync_program_sources --all >> /srv/studycompass/ai-study-abroad/logs/source-sync.log 2>&1
```

同步失败不会自动扩大重试或绕过来源限制；管理员应检查运行记录、条款权限和官网页面后再处理。定期任务只负责提出候选，人工核实和批准仍是发布数据的必要步骤。

## 隐私与免责声明

历史记录默认关闭。用户在首页“历史记录”中主动点击“开启保存”后，才会把后续检索、智能推荐和问答保存到本站 SQLite 数据库；浏览器只保存 HttpOnly、SameSite=Strict 的随机凭证，数据库只保存其 SHA-256 摘要，不保存原始凭证或客户端 IP。常见邮箱、内地手机号及身份证号会在入库前遮盖，但不能保证识别所有个人信息，请勿输入其他敏感内容。记录保留 90 天，应用启动及访问历史接口时清理过期记录。用户可删除单条、清空全部，或“关闭并删除全部”；关闭后旧凭证失效。无账号系统，清除浏览器 Cookie 后无法恢复旧历史；服务器备份需按部署方的备份策略单独清理。

历史接口：`GET /api/history` 查看当前浏览器的最多 100 条记录，`POST /api/history/consent` 开启，`DELETE /api/history/{search|recommend|chat}/{id}` 删单条，`DELETE /api/history` 清空，`DELETE /api/history/consent` 关闭并删除。所有修改请求都要求 `X-History-Action: 1`，同源浏览器请求会携带访问凭证。旧版无归属的预留日志不会作为任何用户的历史展示。

启用 AI 后，查询和所选项目字段会发送给 `.env` 配置的模型服务商，请按该服务商的隐私条款使用。应用日志不记录请求参数或密钥。初始示例数据及 `example.org` 来源链接均为模拟内容，不能据此申请。人工核对的官网记录仅覆盖明确采集的部分招生要求；项目数据仅供参考，不保证录取，费用、要求和截止日期应以真实项目官网为准。
