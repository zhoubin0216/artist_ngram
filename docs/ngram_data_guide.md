# Ngram 数据使用说明（组员版）

核对日期：2026-10-14；本批数据完成于 2026-10-13。

项目仓库：[zhoubin0216/artist_ngram](https://github.com/zhoubin0216/artist_ngram)。本文只介绍已经下载的 **Google Books Ngram 艺术家与流派数据**，不介绍 OpenAlex 或 Getty 数据。

## 1. 先看这里

两套数据都是 **UTF-8 本地文件**，尚未导入数据库或 HDFS。年度主表采用 **JSONL**：每行是一个独立 JSON 对象，不是一个外层 JSON 数组。

| 数据集 | 检索对象 | 精确命中 | 未命中 | 年度主表记录数 |
| --- | ---: | ---: | ---: | ---: |
| 艺术家全量 | 1,370 人 | 877 人 | 493 人 | 73,523 |
| 流派主检索词 | 15 个 | 15 个 | 0 个 | 2,378 |
| 合计 | — | — | — | 75,901 |

- 语料：Google Books **English / `eng`**；原始数据版本：**`20200217`**。
- 年度主表范围：**1800–2019，含首尾年份**。没有 2020 年及之后的数据。
- 一条记录是“一个精确检索词在某一出版年份的计数”，不是一本书，也不是一位艺术家的完整生涯。
- 只保留源数据实际提供的年度记录：**不补零、不平滑、不标准化、不扩展或加总别名**。
- 所有计划分片已处理完毕：艺术家 302 个，流派 9 个；未命中不等于下载未完成。

## 2. 文件在哪里

以下路径均相对于**项目根目录**（包含 `scripts/`、`docs/`、`outputs/` 的目录）。不要依赖某位组员电脑上的绝对路径。

| 用途 | 艺术家 | 流派 |
| --- | --- | --- |
| 年度计数主表 | `outputs/ngram_artists_full/artist_year_counts.jsonl` | `outputs/ngram_movement_15/movement_year_counts.jsonl` |
| 完整名单及状态 | `outputs/ngram_artists_full/artists.json` | `outputs/ngram_movement_15/movements.json` |
| 年度语料总量 | `outputs/ngram_artists_full/totalcounts/`，含 2–5 gram | `outputs/ngram_movement_15/totalcounts/`，含 1–3 gram |
| 来源与固定检索清单 | 各自目录中的 `manifest.json` | 同左 |
| 完成统计及校验值 | 各自目录中的 `report.json` | 同左 |
| 逐片提取结果和恢复依据 | 各自目录中的 `checkpoints/` | 同左 |

艺术家目录运行后可产生 `progress.json`，记录最近一次运行进度；它与 `.lock`、`.downloads/` 一样不上传 Git。艺术家 `report.json` 中 `run_status` 为 `complete`，`completed_shards` 为 302，`failed_shards` 为空。流派报告结构不同，没有同名的 `run_status` 字段；本批报告记录 15 个词全部命中。

直接打开：[艺术家主表](../outputs/ngram_artists_full/artist_year_counts.jsonl)、[艺术家名单](../outputs/ngram_artists_full/artists.json)、[流派主表](../outputs/ngram_movement_15/movement_year_counts.jsonl)、[流派名单](../outputs/ngram_movement_15/movements.json)。

**使用全量艺术家目录，不要再拼接旧的 50 人试验。** `outputs/ngram_key_sample_50/` 已在逐行核对其结果包含于全量之后，从项目移除。报告中的历史复用来源仅作审计标记，不是运行依赖。`ngram_smoke` 等试验输出的字段和补零策略也不同，不要混入本批主表。

## 3. 年度主表字段

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `artist_id` | 字符串 | 仅艺术家表：Wikidata QID，例如 `Q296` |
| `movement_id` | 字符串 | 仅流派表：项目编号 `M01`–`M15`，不是 Wikidata QID |
| `query` | 字符串 | 实际检索词，大小写、空格、重音和标点有意义 |
| `n` | 整数 | 检索所用 n-gram 类型，也是选择年度分母的依据 |
| `year` | 整数 | 源语料提供的出版年份 |
| `match_count` | 整数 | 该词组在该年度语料中的出现总次数 |
| `volume_count` | 整数 | 该年度包含该词组的卷数；不是具体书名清单，也不是去重后的著作数 |

真实记录示例：

```json
{"artist_id": "Q296", "query": "Claude Monet", "n": 2, "year": 1802, "match_count": 1, "volume_count": 1}
```

此例中 1802 年早于 Monet 出生，说明源数据可能包含年代元数据、OCR 或同名等问题，不能解释为他当时已经活动；当前保留源记录，未自动剔除这些异常。

本批唯一键分别是 `(artist_id, query, year)` 和 `(movement_id, query, year)`。每个对象目前只有一个 query，但后续扩展别名时仍应保留 query 这一维度。

### 名单文件的补充信息

`artists.json` 和 `movements.json` 是 **JSON 数组**，不是 JSONL。

- `artists.json`：`qid`、`query`、`movements`（流派 slug 列表）、`birth_year`、`death_year`、`n`、`query_warning`、`shard`、`status`、`rows`、`match_count_sum`。生卒年或警告可为 `null`。这里的快照并不包含完整字典中的所有字段，例如国籍和 Getty ULAN ID 需另查字典源。
- `movements.json`：`movement_id`、`wikidata_qid`、`movement_slug`、`name_zh`、`dictionary_label`、`requested_query`、`query`、`n`、`normalization`、`shard`、`status`、`rows`、`match_count_sum`。
- `rows` 是对象在主表中的年度记录数；`match_count_sum` 是窗口内已保留记录的出现次数之和。

**493 位未命中艺术家仍在 `artists.json` 中，但不在年度主表中。** 对这些对象，`rows=0`、`match_count_sum=0` 只是输出统计值，不是证明其历史出现次数为零。分析覆盖率时必须使用完整名单，不能只数主表中的对象。

状态 `matched` 表示在目标年份内命中，`not_found` 表示已扫描相关完整分片但未发现该精确 query。脚本还支持 `not_downloaded`（未完成）和 `matched_outside_window`（只在窗口外命中）；本批最终结果没有这两种状态。

## 4. 15 个流派与检索词映射

联表使用名单中的 `movement_slug`，不要自行翻译名称后匹配。下表 `query` 的大小写和空格应原样保留。

| ID | movement_slug | 中文名 | query | n |
| --- | --- | --- | --- | ---: |
| M01 | neoclassicism | 新古典主义 | `Neoclassicism` | 1 |
| M02 | romanticism | 浪漫主义 | `Romanticism` | 1 |
| M03 | realism | 现实主义 | `Realism` | 1 |
| M04 | academic_art | 学院派艺术 | `academic art` | 2 |
| M05 | impressionism | 印象派 | `Impressionism` | 1 |
| M06 | post_impressionism | 后印象派 | `Post - Impressionism` | 3 |
| M07 | symbolism | 象征主义 | `Symbolism` | 1 |
| M08 | art_nouveau | 新艺术运动 | `Art Nouveau` | 2 |
| M09 | fauvism | 野兽派 | `Fauvism` | 1 |
| M10 | expressionism | 表现主义 | `Expressionism` | 1 |
| M11 | cubism | 立体主义 | `Cubism` | 1 |
| M12 | futurism | 未来主义 | `Futurism` | 1 |
| M13 | dada | 达达主义 | `Dadaism` | 1 |
| M14 | surrealism | 超现实主义 | `Surrealism` | 1 |
| M15 | abstract_art | 抽象艺术 | `abstract art` | 2 |

后印象派原检索词为 `Post-Impressionism`，经分词核验后用 `Post - Impressionism` 检索 3-gram；映射证据保存在 `normalization` 中。Viewer 仅用于核验分词，计数全部来自原始分片，没有从 Viewer 频率反推计数。`Dadaism` 未与 `Dada` 合并。

艺术家仍沿用 50 人试验的原始全名与空格计数选片口径，**没有统一做同样的分词扩展**。带连字符等情况见 `query_warning`；不要把 `n` 简单视作已经人工审定的姓名词数。

## 5. Python 快速读取和基础校验

使用 Python 3.9+，在**项目根目录**运行以下代码，只需标准库，不需要安装 pandas 或 Spark。代码只读取文件，不修改原始数据。

```python
from pathlib import Path
import hashlib
import json

ROOT = Path.cwd()  # PyCharm 的 Working directory 设为项目根目录
ART = ROOT / "outputs/ngram_artists_full"
MOV = ROOT / "outputs/ngram_movement_15"

def read_jsonl(path):
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]

artist_rows = read_jsonl(ART / "artist_year_counts.jsonl")
movement_rows = read_jsonl(MOV / "movement_year_counts.jsonl")
artists = json.loads((ART / "artists.json").read_text(encoding="utf-8"))
movements = json.loads((MOV / "movements.json").read_text(encoding="utf-8"))

for folder, filename, rows, id_key, expected in [
    (ART, "artist_year_counts.jsonl", artist_rows, "artist_id", 73523),
    (MOV, "movement_year_counts.jsonl", movement_rows, "movement_id", 2378),
]:
    report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    assert len(rows) == report["row_count"] == expected
    assert hashlib.sha256((folder / filename).read_bytes()).hexdigest() == report["data_sha256"]
    assert len({(r[id_key], r["query"], r["year"]) for r in rows}) == len(rows)
    assert all(1800 <= r["year"] <= 2019 for r in rows)
    assert all(0 <= r["volume_count"] <= r["match_count"] for r in rows)

monet = sorted((r for r in artist_rows if r["artist_id"] == "Q296"), key=lambda r: r["year"])
print(len(artist_rows), len(movement_rows))  # 73523 2378
print(len(monet), sum(r["match_count"] for r in monet))  # 145 143148
```

若使用 pandas：JSONL 主表需要 `pd.read_json(path, lines=True)`；名单文件用 `pd.read_json(path)`，不要混淆二者。pandas 为可选依赖，当前项目主 `requirements.txt` 并未包含它。

## 6. 怎样联表

艺术家年度表 → 艺术家名单：`artist_id = qid`，多对一。艺术家名单 → 流派名单：先展开 `movements` 列表，再用其中的 slug 对应 `movement_slug`。流派名单 → 流派年度表：`movement_id`；比较年度曲线时还需对齐 `year`。

接着上一节运行，可构建一个小型的艺术家—流派桥接表：

```python
artist_by_id = {a["qid"]: a for a in artists}
movement_by_slug = {m["movement_slug"]: m for m in movements}
assert len(artist_by_id) == 1370 and len(movement_by_slug) == 15
assert all(r["artist_id"] in artist_by_id for r in artist_rows)

artist_movement = [
    {"artist_id": a["qid"], "movement_id": movement_by_slug[slug]["movement_id"],
     "movement_slug": slug}
    for a in artists for slug in a["movements"]
]
assert len({(r["artist_id"], r["movement_id"]) for r in artist_movement}) == len(artist_movement)
```

**一个艺术家可属于多个流派。** 展开后行数增加是正常的一对多关系，但不能再不加说明地对全表求和。按流派汇总艺术家计数前，需先约定多重归属的分配规则；不同姓名的 `volume_count` 相加也不是去重后的书籍数。

流派词自身的出现次数和“该流派艺术家的出现次数之和”是两个不同指标，不应当作同一变量。如果与 OpenAlex / Getty 联表，应先建立并核验实体 ID 映射、年份和计数单位；不要仅靠姓名字符串或相同年份直接拼接为可比指标。

## 7. 年度总量是什么，如何使用

`totalcounts-n` 是**整个英文语料库**对应 n-gram 类型的年度总量，不是这些艺术家或流派的合计。原文件保留全部原始年份，并不只截取 1800–2019。

它不是 JSON，也不是“每行一个年份”的普通 CSV。按空白切分后，每个条目为：

```text
year,match_count,page_count,volume_count
```

如需派生标准化指标，应选择**相同版本、相同 n、相同 year** 的分母。接着前面的代码运行：

```python
totals = {}
for folder in (ART, MOV):
    for path in (folder / "totalcounts").glob("totalcounts-*"):
        n = int(path.name.rsplit("-", 1)[1])
        for cell in path.read_text(encoding="utf-8").split():
            year, count, pages, volumes = map(int, cell.split(","))
            key, value = (n, year), (count, pages, volumes)
            assert count > 0
            assert key not in totals or totals[key] == value  # 两目录重复的总量应相同
            totals[key] = value

artist_normalized = [
    {**r, "per_billion": r["match_count"] / totals[(r["n"], r["year"])][0] * 1_000_000_000}
    for r in artist_rows
]
assert all(r["match_count"] <= totals[(r["n"], r["year"])][0]
           for r in artist_rows + movement_rows)
```

这里的 `per_billion` 表示每十亿个对应 n-gram 的出现次数，仅在内存中生成，没有写回源文件。分母缺失应报错排查，不能填 0。不同 n 的分母不同，跨 n 比较或聚合仍需交代可比性假设；不要统一用 1-gram 总量，也不要把 `volume_count` 当作这一频率的分母。

## 8. 分析时必须保留的限制

- **缺行不直接等于零。** 主表是稀疏源记录；未命中的艺术家不能补出一整条“零关注度”曲线。建模需要完整年份网格时，应在派生数据中显式区分未命中、缺失年份与自行假定的零值，记录填补策略。
- 区分大小写，不合并重音、简称、姓氏、连字符或其他别名。匹配字符串不是实体消歧；词义、同名和 OCR 噪声仍可能存在。
- 出生前出现或不合理年份需审核，不能自动解释为历史活动或直接删除后冒充原始数据。
- Realism、Symbolism、Futurism 等有跨领域含义；Expressionism 也可能处在更长词组的语境中。当前流派数据是**术语可见度**，不是只限绘画的计数。Abstract art 是宽泛艺术类别。
- 英文 Google Books 可见度不等于艺术价值、全球影响力、交易价格或学术引用量。
- 本数据没有书名、作者、ISBN 或书籍 ID，不能从年度卷数还原书名清单，也无法跨艺术家、流派或别名去重书籍。
- 后续生成 CSV、Parquet、模型特征或图表时，放入单独的分析目录，保留现有主表、名单、总量和来源清单不变。

## 9. 组内共享与复现

两套审核完成的结果目录已纳入 Git，**检出包含本次数据提交的分支即可直接使用，无需另取共享盘文件或重新下载语料**。本次发布分支为 `zhoubin`；合并到 `main` 后，默认分支也会包含数据。

```bash
git clone --branch zhoubin https://github.com/zhoubin0216/artist_ngram.git
```

仓库中的两套数据位于 `outputs/` 下：

- `ngram_artists_full/`：约 13.3 MiB。
- `ngram_movement_15/`：约 470 KiB。

提交内容包括年度主表、名单、`totalcounts/`、`manifest.json`、`report.json` 和小型 `checkpoints/`，合计约 13.75 MiB。`.gitignore` 只对这两套正式结果开放白名单；`data/ngram/`、旧样本、临时下载、运行锁和进度文件仍排除。**无需 Git LFS，也不分发百余 GiB 的临时大分片。** 检出后先运行第 5 节代码核对行数和主表 SHA-256；报告还包含名单及年度总量的 SHA-256。

首次全量提取涉及约 128.8 GiB 的压缩分片，本轮利用已有缓存和检查点减少了实际传输。新增大分片已在成功提取后清理，原有 Monet 原始分片仍保留。一般组员直接使用共享的提取结果即可，不必重新下载语料。

确需重新生成或恢复时，先阅读 [下载与小样本验证流程](ngram_workflow.md)，确认固定 manifest、缓存、磁盘及网络预算；不要改写现有清单后复用旧输出，也不要将大分片强制加入 Git。读取数据支持 Windows；下载/恢复脚本使用 `fcntl`，需 macOS/Linux。

完整检出后，下列恢复命令仅复用已校验的 checkpoint，不需要 50 人目录；1 GiB 的预算可阻止误触发全量大下载：

```bash
python3 scripts/download_all_artists.py --max-gib 1
python3 scripts/download_movement_counts.py download --manifest outputs/ngram_movement_15/manifest.json --max-gib 1
```

恢复命令会刷新报告时间和本地运行状态；仅需读取分析时无需执行。艺术家脚本默认优先使用输出目录已提交的 `manifest.json`；只有显式提供 `--pilot` 才会复用额外试验目录。

## 10. 来源与引用

来源为 [Google Books Ngram 官方原始数据目录](https://storage.googleapis.com/books/ngrams/books/datasetsv3.html)，本批 manifest/report 记录许可为 **CC BY 3.0**。组内报告或论文使用时请保留来源、语料版本和处理口径，例如：

> Google Books Ngram, English corpus, release 20200217. 本项目按固定艺术家全名与流派主词精确检索，提取 1800–2019 年原始 match_count 与 volume_count，不补零、不合并别名；艺术家字典及流派映射来源于项目保存的 Wikidata 数据。
