# Google Books Ngram 下载与小样本验证

## 目的与范围

先验证“艺术家字典 → 分片定位 → 可恢复下载 → 姓名匹配 → 年度频率 → 人工检查”这条链路，再扩展到全部艺术家及 Spark/HDFS。此阶段无需 Spark，也不需要下载完整语料库。

使用项目已有的 `data/wikidata/` JSON，直接读取英文姓名、QID、生卒年和 movement，不需要重新获取 Wikidata 或解析 Excel。按 QID 合并、排除单词姓名及重复完整姓名；生卒年取众数。25 人试验固定包含 Monet、Van Gogh、Bouguereau、Rosa Bonheur、Caspar David Friedrich，再按 movement 轮流补足。这是可重复的便利样本，不是用于统计推断的随机样本。分组沿用原字典源文件，未重新审定艺术史归属。

固定参数：English (`eng`)、公开原始数据版本 `20200217`、分析年份 `1800–2019`。**本流程不提供 2020–2022 年的数据**；如 proposal 仍写到 2022，需要统一调整研究区间，或另行评估其他版本。不要把不同版本的 Viewer 曲线与本地原始计数拼接。

## 1. 先跑单人 smoke test

在独立的 `artist-ngram-project` 项目根目录打开 PyCharm Terminal。使用 Python 3.9+；以下 `python` 可替换为 `python3`。下载和验证脚本只用标准库，不需要先安装 proposal 的依赖。

```bash
python scripts/ngram_sample.py plan --artists Q296 --manifest data/ngram/smoke_manifest.json
python scripts/ngram_sample.py download --manifest data/ngram/smoke_manifest.json --max-gib 1
python scripts/ngram_sample.py validate --manifest data/ngram/smoke_manifest.json --output outputs/ngram_smoke --require-all
```

三个阶段分别是：

1. `plan` 获取官方索引和文件元数据，将姓名映射到最小必要分片，显示压缩下载量；不下载大分片。
2. `download` 在总预算和磁盘检查通过后下载，支持 `.part` 断点续传，核对 ETag、文件大小和官方 MD5，并记录 SHA-256。默认预算 1 GiB。
3. `validate` 再次核验本地文件，扫描完整 gzip 分片至 EOF，检查压缩流完整性、分片边界及目标姓名计数，输出报告。

Monet 的 `2-00120-of-00589.gz` 大小为 524,932,017 bytes，约 500.6 MiB。下载保持压缩，不在磁盘解压。扫描时内存仅保留目标姓名结果，不载入整个语料库。

`--require-all` 要求清单内所有姓名都在目标年份范围内命中，否则退出码为 2，但仍保存覆盖报告。它不要求“没有出生前命中”，也不能替代人工消歧。一般错误（下载、校验、格式或预算）退出码为 1；成功为 0。不加 `--require-all` 可以查看部分下载的覆盖状态，退出 0 不代表全部艺术家完成。

## 2. 查看结果

打开 `outputs/ngram_smoke/report.html`。还会生成：

| 文件 | 用途 |
| --- | --- |
| `report.json` | 覆盖状态、扫描行数、首个非零年、频率峰值年、出生前命中提示、源文件 SHA-256 |
| `annual.jsonl` | 每个已命中姓名每年一条：QID、query、n、year、match_count、volume_count、total_match_count、frequency、per_billion |
| `report.html` | 无外部依赖的年度折线图和人工检查信息；各图独立纵轴，未平滑 |

状态含义：

- `matched`：完整分片内精确命中，且分析区间至少一年非零。
- `matched_outside_window`：姓名存在，但分析区间没有非零记录。
- `not_found`：相关分片已完整校验、扫描，但未找到该精确姓名；不输出假零值曲线。
- `not_downloaded`：相关分片尚未下载完成；不解释为未命中或零。
- `unsupported_token_count`：当前姓名超出本流程支持的 2–5 个空格分隔词范围；需要人工设计 query。

只有整个姓名已命中时，才把该姓名记录中缺少的年份补为 0。总量文件缺少年份时直接报错，不把缺失分母当作 0。

## 3. 扩展到 25 人小样本

```bash
# 只创建清单，先看终端显示的总下载量。
python scripts/ngram_sample.py plan --sample-size 25

# 先检查已下载分片能覆盖多少人；本命令不会联网下载。
python scripts/ngram_sample.py validate --output outputs/ngram_sample
```

2026-10-11 生成的清单覆盖 15 个 movement、25 人、14 个不同分片，总压缩量 6,565,778,042 bytes（约 6.115 GiB，已包含年度总量）。这是下载计划，不表示 25 人已全部验证。

确认网络与空间允许后，再运行：

```bash
# 显式把预算提高到 7 GiB；已有完整文件会校验复用，不重复下载。
python scripts/ngram_sample.py download --max-gib 7
python scripts/ngram_sample.py validate --require-all
```

也可以只选择五个核心姓名：

```bash
python scripts/ngram_sample.py plan --artists Q296,Q5582,Q483992,Q241732,Q104884 --manifest data/ngram/anchors_manifest.json
```

先阅读新清单的体积，再将同一个 `--manifest` 传给 `download` 和 `validate`，并设置相应的下载预算与独立 `--output` 路径。默认的单人清单和 25 人清单互不覆盖；底层原始文件共用缓存。

## 4. 数据格式、匹配与频率算法

官方 v3 分片一行包含一个 n-gram 的多个年份：

```text
ngram<TAB>year,match_count,volume_count<TAB>year,match_count,volume_count...
```

这不同于 v2 的逐年四列格式。不能直接套用 v2 的解析代码。官方索引提供词典序分片边界；脚本用二分搜索选片，然后检查读取记录是否确实落在边界内。**分片内部行顺序不是词典序，必须扫描到底，不能提前退出。**

姓名使用大小写敏感的完整字符串精确匹配，不混入 `_NOUN` 等词性标注行，不自动合并大小写、简称、姓氏或别名。空格计数仅用于初始分片选择，不保证与语料实际分词一致；例如带连字符的 `William-Adolphe Bouguereau` 会被标注需要检查分词，未命中时应人工审核拼写/分词/别名，而不是直接认定没有关注度。带重音的拼写与去重音拼写也不自动合并。

每个 n 使用同版本的 `totalcounts-n` 作为分母：

```text
frequency(artist, year) = match_count(artist, year) / total_match_count(n, year)
per_billion = frequency × 1,000,000,000
```

总量格式为 `year,match_count,page_count,volume_count`。2-gram 与 3-gram 的年度总量分别读取，不能统一用 1-gram 总量。`volume_count` 仅作辅助信息，不作为频率分母。不同 n 的分母不同；跨姓名排名或 movement 聚合前还需要说明指标可比性与多 movement 归属策略。

人工检查至少包括：

- 出生前是否有命中：可能来自书籍年份元数据错误、同名、OCR 或其他原因；报告只提示，不武断删除。
- 核心姓名是否出现合理曲线；不要强求首次记录恰好等于出生/创作年份。
- 连字符、重音、姓名变体是否需要加入已审定的 alias。未经审核不要直接求和，避免重复计数。
- 未命中是否因为分词、拼写、语料覆盖或低频筛选；未命中不等于真实世界中无人提及。
- Google Books 英文出版物出现频率是文本可见度代理，不是艺术价值或全球影响力的直接测量。

## 5. 中断、缓存与故障

- 网络中断或 Ctrl-C 后，用原来的 `download` 命令重跑；保留 `.part` 和对应 `.part.json`。服务端若忽略 Range，脚本从头覆盖部分文件，绝不把完整响应追加到旧文件。
- URL、ETag、大小、MD5 必须与部分文件元数据一致。发生 HTTP 412 或元数据变化时应先重新 `plan`；旧缓存不要直接复用。
- 完整文件的 MD5 不匹配、损坏的完整长度 `.part`、来源不明的 `.part` 都会阻止使用。按报错定位具体文件，手动移到隔离目录后重跑；不要删除整个数据目录。
- 超预算会在下载前退出。预算按未缓存对象完整大小保守估算，包含已部分下载的对象；不因断点存在而偷偷扩大下载范围。
- 空间检查预留 128 MiB，不包含你后续复制语料到 HDFS 所需的额外空间。其他并发程序仍可能耗尽磁盘。
- 不要同时启动多个进程写同一个 manifest 或原始文件；当前简易版没有跨进程文件锁。
- 下载文件存于 `data/ngram/raw/`，官方索引与本地计划位于 `data/ngram/`，这些原始缓存仍由 `.gitignore` 排除。2026-10-14 起仅将两套正式结果 `outputs/ngram_artists_full/` 与 `outputs/ngram_movement_15/` 的计数、名单、总量、清单、报告和 checkpoint 纳入 Git；样本、临时文件与运行状态仍忽略。

## 6. 自动化测试与本次实测

```bash
python -m unittest discover -s tests -v
```

测试仅使用合成数据及临时 localhost HTTP 服务，不会下载真实分片；运行环境需要允许本机端口监听。覆盖完整下载、Range 续传、服务端忽略 Range、完整部分文件恢复、ETag 变化、坏校验和、v3 稀疏行、索引边界、未排序行、重复计数、gzip 截断、缺失年份、未下载/未命中区分、预算拒绝和 CLI 输出。

2026-10-11 的真实 smoke test（不是上述合成测试）：

- 完整下载并校验 Monet 的 500.6 MiB 分片，扫描 **3,366,048** 条原始记录。
- `Claude Monet` 精确命中，生成 1800–2019 共 **220** 条年度记录，其中 **145** 年非零。
- 分析区间内合计 **143,148** 次；以标准化频率计算，峰值年为 **2007**。
- 发现 **3** 个出生前年份命中，共 3 次；因此标记 `manual_review: true`。首次非零年 1802 **不能解释为 Monet 当时已经活动**。
- 此实测只完成 **1 位艺术家**，并未宣称全部 25 人或 1,370 人通过验证。

## 7. 后续接入 Spark / HDFS

本次先用流式 Python 验证数据口径。后续可以把已校验的 gzip 分片和字典上传 HDFS，用 Spark 解析 v3 行、按精确 query 连接字典、展开年度字段，再连接同 n 的年度总量，输出 Parquet 及可视化汇总。gzip 文件通常不适合单文件内任意切分读取，应依靠多个分片或一次转换后的列式文件并行处理。

**当前流程不包含 Spark/HDFS 部署。** 50 人关键计数试验与完整字典下载分别见第 8、10 节。开展下游分析前仍应人工审核姓名、年份和异常，再制定别名与 movement 聚合规则。

## 8. 50 人关键原始计数试验：逐片提取，不保留新增大分片

本节保留历史试验方法。2026-10-14 已核对所有 50 人结果包含于全量，并将独立试验输出目录从项目移除；日常分析直接使用第 10 节全量结果，不需要重建试验目录。

此模式独立于上面的 `validate`：只保留原始计数，不计算频率、不平滑、**不补零**，未命中姓名单独列明。使用同一字典的五个核心案例加 movement 轮选，共 50 人、15 个 movement；不是随机样本。English / 20200217，筛选 1800–2019。

```bash
python3 scripts/ngram_sample.py plan --sample-size 50 --manifest data/ngram/sample_50_manifest.json
python3 scripts/download_artist_counts.py --manifest data/ngram/sample_50_manifest.json --output outputs/ngram_key_sample_50 --max-gib 12
```

本次清单涉及 23 个分片，压缩总量约 9.920 GiB；已有 Monet 分片复用后预计新增传输约 9.431 GiB。最大的单片约 519.5 MiB。脚本串行下载、完整校验、扫描、写入并回读校验小型 checkpoint，最后删除**本次 scratch 内的对应分片**；不会删除 `data/ngram/raw/` 中已有的原始文件。每次下载前预留 512 MiB 安全空间，不把全部分片同时落盘。

输出位于 `outputs/ngram_key_sample_50/`：

| 文件 | 内容 |
| --- | --- |
| `artist_year_counts.jsonl` | 每行一条 JSON：artist_id（Wikidata QID）、query（检索姓名）、n（词数/分母类型）、year、match_count、volume_count |
| `artists.json` | 全部 50 人的姓名、ID、movement、生卒年、命中状态及记录数；未命中者仍在这里 |
| `totalcounts/totalcounts-2` 至 `totalcounts-5` | 官方年度总量原文件，保留原本全部年份及 year,match_count,page_count,volume_count 四字段；不只是这 50 人的合计 |
| `manifest.json` | 本次固定的样本与数据源清单、URL、大小、ETag、MD5 |
| `checkpoints/` | 每片的目标姓名原始年度计数、扫描记录数、原文件 SHA-256、checkpoint 校验和；包含源记录窗口外年份以便审计 |
| `report.json` | 全部处理完成后生成的覆盖统计、缺失姓名、行数、处理口径、文件 SHA-256 |

主数据只输出源行实际提供的 1800–2019 年份，不产生全零占位记录。`not_found` 是精确 query 在完整分片中未找到，并不代表真实世界零关注度；连字符、重音、其他拼写和语料低频筛选均可能影响覆盖。未经审核不改写名字、不合并别名。`volume_count` 是当年包含该精确词组的卷数，不是书名清单，也不是去重后的著作数量。

中断后重跑**第二条同一命令**即可：完整 checkpoint 跳过扫描，当前 `.part` 继续下载；未成功校验提取的临时分片保留以便恢复。不要重新生成同名 manifest 再接着使用旧输出目录，清单不一致会拒绝运行。需要改变样本时使用新 manifest 和新 output。`--max-gib` 是剩余未缓存对象的保守传输预算，不是最终结果文件大小。脚本用输出目录文件锁避免重复进程同时写入；`fcntl` 要求 macOS/Linux。

本轮真实结果：23 个分片全部完成，扫描 77,140,367 条源记录；50 人中 35 人精确命中、15 人未命中，主表 2,918 条源年度记录，实际年份覆盖 1800–2019。主表 333,815 bytes，整个输出目录约 607 KiB（含名单、总量、清单、checkpoint 与报告）。全部输出行均与 checkpoint 一致，ID/query/year 无重复，官方总量 MD5 与输出 SHA-256 复核通过；Monet 的 145 条记录、143,148 次出现与之前 smoke test 一致。新增 scratch 分片已清理，既有 Monet 原始分片保留。未命中姓名见 `report.json` 的 `missing_names`，需要另做姓名/分词审核，不能解释为没有书籍提及。新增 8 项离线测试通过，项目完整测试共 38 项通过。

## 9. 15 个 movement 主检索词原始计数

独立脚本 `scripts/download_movement_counts.py` 沿用上一节的逐片下载、官方 MD5 校验、完整扫描、checkpoint 回读与安全清理方法，不改动已有艺术家结果。使用检索表中的 15 个主词，保留原字典 M01–M15 编号及 Wikidata QID；大小写、其他拼写和别名不自动扩展或加总。

本次选择固定在脚本的 `PRIMARY` 中，修改 Excel 检索表不会自动改变下载选择；需要改变关键词时先审核并更新配置，再使用新的 manifest 和 output 路径，避免混用已保存结果。

```bash
# 清单已存在时直接运行 download，不要重复 plan。
python3 scripts/download_movement_counts.py plan
python3 scripts/download_movement_counts.py download --max-gib 5
```

固定 English / 20200217，1800–2019。本次共 9 个分片，压缩总量约 4.708 GiB，最大单片约 598.2 MiB。支持 1–5 gram；实际涉及 1、2、3 gram，并保留对应 `totalcounts-1`、`totalcounts-2`、`totalcounts-3` 官方总量原文件。

**分词核验**：Google English (2019) 查询元数据将 `Post-Impressionism` 返回为 `Post - Impressionism`，因此原始文件使用 3-gram，而不是按空格粗略视为 1-gram。清单及 movement 名单保留 `requested_query` 与 `query` 的映射、核验 URL 和时间。Viewer 响应只用于分词核验，**不把 Viewer 的频率作为原始次数，也不倒推出书籍数量**。全部计数均来自 MD5 校验通过的官方 v3 原始分片。

输出目录：`outputs/ngram_movement_15/`。

| 文件 | 内容 |
| --- | --- |
| `movement_year_counts.jsonl` | movement_id、query（原始语料分词形式）、n、year、match_count、volume_count；每词每年一条源记录 |
| `movements.json` | 全部 15 个 movement 的中文名、原字典 ID/QID、原检索词、分词后检索词、状态与行数 |
| `totalcounts/` | 1、2、3 gram 的年度总量完整原文件，不截断其原始年份 |
| `manifest.json` | 固定选择、官方 URL、文件大小、ETag、MD5 和分词核验元数据 |
| `checkpoints/` | 每片目标词的原始年度计数、扫描条数、原文件 SHA-256 与结果校验和；含窗口外源年份以便审计 |
| `report.json` | 完成后的覆盖情况、主表行数、缺失词、解释限制和文件 SHA-256 |

主表只保留 1800–2019 的实际源年份记录，不补零、不平滑、不标准化，不按流派的历史活动期进一步截断。大写和小写视作不同词。`Dadaism` 是达达主义本次的主词，未自动合并 `Dada`。`Expressionism` 单词也可能来自 `Abstract Expressionism` 等更长短语的语境，不能据此实现领域消歧。

这些数据是**流派术语的文本可见度**，不是仅限绘画的出现次数，也不是所属艺术家计数之和。Realism、Symbolism、Futurism 等跨领域用法仍保留，Abstract art 按宽泛艺术类别解释。不同词的 volume_count 不能直接相加当作去重书籍数。

重跑 `download` 可离线复用已校验 checkpoint，未完成的 `.part` 支持续传；输出与清单不一致时拒绝复用。新增大分片只有在成功提取并回读校验后才清理，既有 `data/ngram/raw/` 文件不会删除。正式小型结果已纳入 Git，原始缓存和临时文件仍忽略。clone 后恢复请显式指定已提交的清单：`python3 scripts/download_movement_counts.py download --manifest outputs/ngram_movement_15/manifest.json --max-gib 1`。

2026-10-13 本轮实测：9 个分片全部校验并扫描完成，共读取 29,666,023 条源记录。15 个主词全部命中，主表 2,378 条年度记录，实际年份范围 1800–2019，主表 259,842 bytes。连同名单、总量、清单和 checkpoint，整个输出约 470 KiB。逐行与 checkpoint 核对一致，movement_id/query/year 无重复，官方总量 MD5 和输出 SHA-256 验证通过。新增 movement 离线测试 8 项通过，项目测试合计 46 项通过。艺术家主表与名单校验值未变。

## 10. 完整字典：1,370 位艺术家

原始下载批次使用 `scripts/download_all_artists.py` 将原有 50 人与剩余 1,320 人合并到独立全量输出。继续使用原始全名、大小写敏感精确匹配和按空格选择 n 的口径；142 个带标点等分词提示的姓名保留警告，不自动改写、扩展别名或把未命中当作零。现已移除独立 50 人输出，脚本不再强制依赖它；只有显式传入 `--pilot` 才读取试验数据。没有提供 `--manifest` 时优先读取输出目录已提交的 `manifest.json`，其次才是 `data/ngram/artists_full_manifest.json`。

```bash
# 仅首次从头规划时执行；clone 已有正式结果时跳过此行。
python3 scripts/ngram_sample.py plan --sample-size 1370 --manifest data/ngram/artists_full_manifest.json

# 首次运行与断点恢复均使用这条命令。
python3 -u scripts/download_all_artists.py --workers 8 --max-gib 130

# 已有完整正式结果的离线恢复验证：预算设小，防止误下载全量。
python3 scripts/download_all_artists.py --max-gib 1
```

固定 English / 20200217、1800–2019，共 302 个目标分片，压缩总量约 128.776 GiB。首次运行复用 12 个查询集合完整的试验 checkpoint 和既有 Monet 原始分片，剩余传输上限约 123.472 GiB。试验 checkpoint 只覆盖部分目标姓名的分片会重新扫描，并核对原有查询结果没有变化；绝不会将 50 人试验的部分查询覆盖误认为已完成整个全量分片。

本轮先以 4 并发验证，确认磁盘余量后提高到 8 并发；最终使用多进程，让解压扫描利用多个 CPU 核心。CLI 默认并发数仍为 4，允许 1–8，可按机器空间降低。不将百余 GiB 同时存盘。启动前按并发数乘最大分片大小加 1 GiB 检查可用空间（8 并发全量上限约 5.94 GiB）；恢复时优先处理已有临时分片，扣除首批已占用的字节，避免重复计算空间。每片仍执行下载校验、全文件扫描、checkpoint 回读校验和自有临时文件清理。已有 `data/ngram/raw/` 原始文件不删除。输出目录加进程锁，禁止同时向同一目录运行两个下载进程。

结果位于 `outputs/ngram_artists_full/`：

- `artist_year_counts.jsonl`：artist_id、query、n、year、match_count、volume_count，仅源数据实际提供的年度记录，不补零。
- `artists.json`：全部 1,370 人的字典信息、命中状态与行数，包括未命中和尚未下载者。
- `totalcounts/`：原版 2–5 gram 年度总量文件，完整保留原始年份和字段。
- `manifest.json`、`checkpoints/`：固定检索清单、源对象身份、校验和与逐片原始提取结果。
- `progress.json`：每完成一个分片更新一次，含已完成分片、已检查艺术家、失败信息和磁盘余量。
- `report.json`：阶段性或最终统计及主表校验和；每完成约 10 个新分片更新主表和报告，故主表可能略晚于进度文件。

**只有 `report.json` 的 `run_status` 为 `complete`、`completed_shards` 等于 302 且无失败分片，才表示全量处理完成。** 运行中的主表只是部分结果；`not_downloaded` 不等同于 `not_found`。所有查询已处理也不保证每个姓名均能命中。

断点恢复复用完整 checkpoint，未完成的 `.part` 按原机制续传。保持 manifest 不变，重新运行上述下载命令即可。`--max-gib` 是剩余传输预算而非最终结果大小。新增 8 项离线测试覆盖部分查询 checkpoint 复用限制、全量并发与离线恢复、失败状态、来源一致性、参数校验、安全清理、真实多进程扫描及断点磁盘预算；项目测试合计 54 项通过。

2026-10-13 全量实测已完成：302/302 个分片均通过校验，覆盖源记录合计 **1,018,844,845** 条（含复用检查点所对应的完整分片）。字典 **1,370 人全部完成检索**，其中 **877 人精确命中、493 人未命中**；没有 `not_downloaded` 或失败分片。主表为 **73,523 条**实际源年度记录，年份 1800–2019，大小 **8,327,654 bytes（约 7.94 MiB）**。含名单、总量、清单、检查点和报告，全部输出约 **13.3 MiB**。

相对于原 50 人试验，本轮剩余 1,320 人中新增命中 **842 人**、未命中 **478 人**，新增 **70,605 条**年度记录。原 50 人的年度计数和状态逐项核对不变，原 movement 主表与名单的 SHA-256 也未改变。493 人未命中仅表示当前精确姓名没有源记录，不代表没有书籍提及；别名、拼写和分词扩展仍需单独审核。

终检逐行对照全部 checkpoint，核对字典 ID/query/n、年度范围、计数约束、唯一键、官方年度总量 MD5 和输出 SHA-256，均通过。新增临时大分片全部清理，原有 Monet 原始分片保留并复核校验和一致。随后禁止网络访问和重新扫描执行恢复测试，302 个 checkpoint 全部离线复用，主表与名单 SHA-256 不变。主表 SHA-256：`03201b44ccd5a230fb362286ce2013f9b365ba9ed6e526fbd1c491c21c1da653`。

## 来源与许可

- [Google Books 官方原始语料目录](https://storage.googleapis.com/books/ngrams/books/datasetsv3.html)
- [English 20200217 2-gram 官方索引](https://storage.googleapis.com/books/ngrams/books/20200217/eng/eng-2-ngrams_exports.html)
- [English 20200217 2-gram 年度总量](https://storage.googleapis.com/books/ngrams/books/20200217/eng/totalcounts-2)
- [Google Ngram Viewer 官方说明](https://books.google.com/ngrams/info)

数据来自 Google Books Ngram，目录注明 CC BY 3.0；引用项目结果时应保留数据来源、版本和处理口径。
