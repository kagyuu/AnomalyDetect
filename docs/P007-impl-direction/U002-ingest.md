あなたはExecutor(実装担当)です。以下は1スプリント分の作業範囲と完了条件を定義したものです。スプリントは複数のタスクから成り、各タスクに個別の完了条件とチェックボックスを持ちます。実施後は、そのタスクの完了条件を満たしたことを確認したうえで、Executor Stepの「停止条件」(`SKILL.md` 参照)に該当しない限り、自動的に次のタスクへ進んでください。人間の指示を待って停止しないでください。

# 【スプリントID】U002 — ingest

**位置づけ**: 2 番目に不確定な jstat の形式判別を潰し、以降のスプリントに実データを供給する。

## タスク一覧(OKF副目次)

* 状態は `[ ]`(未着手) / `[~]`(進行中) / `[x]`(完了) の3種類とする。1タスクの作業を開始したら `[~]` に、完了条件をすべて満たしたら `[x]` に更新する。
* このスプリントファイル自体の状態(`docs/P007-impl-direction.md` の該当行)は、全タスクが `[x]` になって初めて `[x]` にする。
* **中断からの再開**: `[~]` のタスクがあれば、それが中断時点の作業対象である。再開時は必ず該当タスクの【完了条件】を実際に再実行して現状を確認すること。`[ ]` のまま存在するファイルは「未着手」として扱い、内容を鵜呑みにしない。
* **先行実装の禁止**: `[ ]` の後続タスクが対象とするファイルには着手しない。

- [x] U002-T1 [ファイル探索と種別判定](#u002-t1-ファイル探索と種別判定) — discovery(M03)。3 パターンの照合と名前の分解
- [x] U002-T2 [解析の共通基盤](#u002-t2-解析の共通基盤) — loaders/__init__。文字コード・改行・数値変換・エラー集計
- [x] U002-T3 [DBコネクションログと LSF キューの解析](#u002-t3-dbコネクションログと-lsf-キューの解析) — M04, M06
- [x] U002-T4 [jstat の解析と形式自動判別](#u002-t4-jstat-の解析と形式自動判別) — M05。本スプリントの最重要タスク
- [x] U002-T5 [重複排除と cli への結線](#u002-t5-重複排除と-cli-への結線) — 終了コード 2 と 4 の完成

---

## U002-T1: ファイル探索と種別判定

### 【目的】

* M03 `discovery` を実装し、`{dir}` 配下を再帰的に走査して 3 種のログファイルを識別する。

### 【作成・編集対象ファイル】

* `app/src/s_anomaly/discovery.py` (新規)
* `app/tests/unit/test_discovery.py` (新規)

### 【参照すべき仕様箇所】

* `docs/P003-backend-spec.md` 4章 DS-03-01 〜 DS-03-04
* `docs/P002-frontend-spec.md` 1.2 UI-01-V04(シンボリックリンクの循環)、1.3 usage(対象ファイル名)
* `docs/P001-requirement.md` FR-010, FR-011

### 【実装内容】

1. `LogFile` を dataclass で定義する: `path: Path`, `kind: str`(`"db_connection"` / `"jvm_gc"` / `"lsf_queue"`), `container: Optional[str]`, `host: Optional[str]`, `date: str`(8 桁)。
2. `classify(name: str) -> Optional[LogFile 相当のメタ情報]` を作る。DS-03-01 の 3 パターンに順に照合する。
   * ① `^DBConnection_(\d{8})\.csv$` → 日付を取る。
   * ③ `^bqueues_(.+)_(\d{8})\.txt$` → ホスト名と日付を取る。
   * ② は正規表現ではなく **DS-03-02 の分解手順**で行う。拡張子 `.txt` を除き、末尾の `_` で 1 回 `rsplit` して日付(8 桁数字であること)を取り、残りを `_gc_` で **`rsplit(..., 1)`** してコンテナ名とホスト名に分ける。`_gc_` を含まない、または日付部が 8 桁数字でない場合は `None`。
   * **判定の順序に注意する。** `bqueues_host_20260601.txt` は ② の分解でも一見通ってしまうように見えるが、`_gc_` を含まないため ② にはならない。①③ を先に照合し、最後に ② を試すこと。
3. `discover(root: Path, progress) -> List[LogFile]` を作る。
   * `os.walk` で再帰走査する。
   * **訪問済みディレクトリの実パス(`Path.resolve()`)を集合で保持し、既訪問なら降りない**(DS-03-03、UI-01-V04)。`os.walk(followlinks=True)` を使う場合は特に必要。
   * どのパターンにも一致しないファイルは黙って読み飛ばし、件数を数える。
   * 結果を **`(date, path の文字列)` の昇順で安定ソート**して返す(DS-03-04、再現性 NFR-009)。
   * INFO ログに「探索したファイル総数」「種別ごとの該当件数」「対象外として読み飛ばした件数」を出す(`docs/P002-frontend-spec.md` 3.3 の S3 行)。ステップ ID は `S3`。

### 【実装してはいけないこと】

* ファイルの中身を読むこと(T2 以降の担当)。ここではファイル名だけで判定する。
* 日付による絞り込み(`--from` / `--to` は仕様に無い)。
* 対象外ファイルを WARNING として報告すること(FR-011 は「黙って読み飛ばす」と定めている。INFO の件数集計のみ)。

### 【Unit Test内容】

* **テスト対象**: `discovery.classify`, `discovery.discover`
* **正常系テスト**
  * `DBConnection_20260601.csv` → `db_connection`、日付 `20260601`。
  * `app01_gc_host01_20260601.txt` → `jvm_gc`、コンテナ `app01`、ホスト `host01`。
  * `bqueues_lsfhost01_20260601.txt` → `lsf_queue`、ホスト `lsfhost01`。
  * **コンテナ名に `_` を含む場合**: `my_app_01_gc_host1_20260601.txt` → コンテナ `my_app_01`、ホスト `host1`。
  * **コンテナ名に `_gc_` を含む場合**: `a_gc_b_gc_host1_20260601.txt` → `rsplit("_gc_", 1)` により コンテナ `a_gc_b`、ホスト `host1`。
  * 一時ディレクトリに 3 種 × 2 ファイル + 対象外 3 ファイルをサブディレクトリに分散して置き、`discover` が 6 件を返し、対象外 3 件を数えること。
  * 同じファイル群を 2 回 `discover` して、**返るリストの順序が完全に同一**であること(NFR-009)。
* **主要な異常系テスト**
  * `readme.txt` / `DBConnection.csv`(日付なし)/ `DBConnection_2026060.csv`(7 桁)/ `app01_gc_host_2026ab01.txt`(数字でない)がいずれも `None` になること。
  * `app01_host_20260601.txt`(`_gc_` を含まない)が `None` になること。
  * 空のディレクトリで `discover` が空リストを返し、例外にならないこと。
  * シンボリックリンクで自分自身の親を指すループを作り、`discover` が無限ループせずに終了すること(**Windows でリンクを作れない場合は、このテストを `unittest.skipUnless` でスキップし、スキップ理由を明記すること**)。
* **合格条件**: 上記すべてが合格すること。

### 【実行コマンド】

```
python -m unittest discover -s app/tests/unit -t app -v
```

### 【完了条件】

* テストが `OK` で終わり、件数が 0 でないこと。先行タスクのテストも合格していること。

### 【次タスクに進む前の停止条件】

* 単体テストを 3 回自己修正しても合格にできない場合は停止して報告する。

---

## U002-T2: 解析の共通基盤

### 【目的】

* 3 つのローダが共通で使う、文字コード・改行・数値変換・エラー集計の仕組みを作る。

### 【作成・編集対象ファイル】

* `app/src/s_anomaly/loaders/__init__.py` (新規)
* `app/tests/unit/test_loader_common.py` (新規)
* `app/tests/fixtures/` に極小のテストファイルを数点 (新規)

### 【参照すべき仕様箇所】

* `docs/P003-backend-spec.md` 5.1 DS-LD-01 〜 DS-LD-05
* `docs/P002-frontend-spec.md` 6.2.5 `load_error` の `reason` 4 種
* `docs/P001-requirement.md` FR-014, FR-015, NFR-007

### 【実装内容】

1. `LoadResult` を dataclass で定義する: `ok_rows: int`, `skipped_rows: int`, `errors: Dict[str, int]`(理由コード → 件数)。
2. **理由コードは 4 種のみ**を定数として定義する: `REASON_COLUMNS`(`"列数不一致"`)、`REASON_DATE`(`"日付書式不正"`)、`REASON_NUMBER`(`"数値変換不能"`)、`REASON_HEADER`(`"見出し不明"`)。**この 4 種以外の理由コードを作ってはならない**(`docs/P002-frontend-spec.md` 6.2.5)。
3. `open_text(path: Path)` を作る。DS-LD-02 に従う。
   * まず `encoding="utf-8-sig"`、`newline=None`(ユニバーサル改行)で開いて全文を読む。
   * `UnicodeDecodeError` なら `encoding="cp932"` で再試行する。
   * それも失敗したら `None` を返す。**呼び出し側は `見出し不明` として `load_error` に記録し、ファイル全体を読み飛ばす**(`docs/P003-backend-spec.md` DS-LD-02)。理由コードは 4 種に限定されているため `文字コード不明` という 5 種目を作ってはならない。**失われる情報は WARNING ログに具体的な理由(「UTF-8 でも CP932 でもデコードできません: {ファイル名}」)を出すことで補う。**
4. `parse_number(token: str) -> Tuple[bool, Optional[float]]` を作る。
   * `token` が空文字・空白のみ・`"-"` なら `(True, None)` を返す(**正常であり NULL 扱い**。FR-015)。
   * `float(token)` できれば `(True, 値)`。
   * できなければ `(False, None)`(`数値変換不能`)。
5. `parse_int(token)` も同様に作る(`int(float(token))` で小数表記も受ける)。
6. `parse_ts(token: str, fmt: str) -> Optional[datetime]` を作る。`datetime.strptime` に失敗したら `None`。
7. `BatchInserter` クラスを作る。DS-LD-04 に従い、**10,000 行ごとに `executemany` する**。
   * `__init__(con, table: str, columns: List[str])`
   * `add(row: tuple)` / `flush()`
   * `_load_seq` は `BatchInserter` が**プロセス全体で単調増加する通番**として自動採番する(モジュールレベルのカウンタ。DS-LD-06 の重複排除で使う)。
8. `record_errors(con, file_path: str, errors: Dict[str, int])` を作り、`load_error` テーブルへ書き込む。

### 【実装してはいけないこと】

* 各形式固有の解析ロジック(T3・T4 の担当)。
* 例外を投げて解析全体を止めること。**行の解析失敗は必ず理由コードとして返す**(FR-014)。
* `chardet` など外部の文字コード判定ライブラリの使用。

### 【Unit Test内容】

* **テスト対象**: `open_text`, `parse_number`, `parse_int`, `parse_ts`, `BatchInserter`, `record_errors`
* **正常系テスト**
  * `parse_number("12.5")` → `(True, 12.5)`、`parse_number("")` → `(True, None)`、`parse_number("-")` → `(True, None)`、`parse_number("  ")` → `(True, None)`。
  * `parse_ts("2026/06/01 00:00:01", "%Y/%m/%d %H:%M:%S")` が正しい `datetime` を返すこと。
  * `open_text` が、UTF-8・**BOM 付き UTF-8**・CRLF 改行・LF 改行の 4 種のファイルをすべて同じ内容として読めること(`tests/fixtures/` に 4 ファイルを置く)。**BOM が本文の先頭に残っていないこと**を明示的に確認する。
  * `BatchInserter` に 25,000 行を入れて `flush` すると、テーブルに 25,000 行入ること(バッチ境界をまたぐこと)。
  * `_load_seq` が単調増加すること。
* **主要な異常系テスト**
  * `parse_number("abc")` → `(False, None)`。
  * `parse_ts("2026-06-01", "%Y/%m/%d %H:%M:%S")` → `None`。
  * `open_text` が、UTF-8 として不正なバイト列(cp932 のみで有効なもの)を cp932 で読めること。
  * `open_text` が、どちらでも読めないバイト列に対して `None` を返し、例外を投げないこと。
  * 理由コードの定数が 4 個だけであること(モジュール内の `REASON_` で始まる定数を集めて件数を確認する)。
* **合格条件**: 上記すべてが合格すること。

### 【実行コマンド】

```
python -m unittest discover -s app/tests/unit -t app -v
```

### 【完了条件】

* テストが `OK` で終わること。`app/tests/fixtures/` に文字コード・改行の 4 種のファイルが存在すること。

### 【次タスクに進む前の停止条件】

* 単体テストを 3 回自己修正しても合格にできない場合は停止して報告する。

---

## U002-T3: DBコネクションログと LSF キューの解析

### 【目的】

* M04 `loaders.dbconn` と M06 `loaders.bqueues` を実装する。

### 【作成・編集対象ファイル】

* `app/src/s_anomaly/loaders/dbconn.py` (新規)
* `app/src/s_anomaly/loaders/bqueues.py` (新規)
* `app/tests/unit/test_dbconn_loader.py` (新規)
* `app/tests/unit/test_bqueues_loader.py` (新規)
* `app/tests/fixtures/` にサンプルファイル (新規)

### 【参照すべき仕様箇所】

* `docs/P003-backend-spec.md` 5.2(dbconn)、5.4 DS-06-01 〜 DS-06-03(bqueues)
* `docs/P002-frontend-spec.md` 6.2.1, 6.2.3
* `docs/P001-requirement.md` 4.1, 4.3(**4.3 の 1/100 秒の扱いは人間に確認済みの確定仕様**)

### 【実装内容】

**dbconn (M04)**

1. `load(con, logfile, progress) -> LoadResult` を作る。
2. `csv.reader` で読む。**1 行目(見出し)は読み飛ばし、列名の検証は行わない**(DS-05 の ★FIXME★ のとおり、列順が固定である前提)。
3. 期待する列数は 5。異なれば `列数不一致`。
4. 日付は `%Y/%m/%d %H:%M:%S`。失敗すれば `日付書式不正`。
5. `port` は整数、`active_connections` は整数(NULL 可)。失敗すれば `数値変換不能`。
6. `db_connection` テーブルへ `BatchInserter` で入れる。

**bqueues (M06)**

1. `load(con, logfile, progress) -> LoadResult` を作る。
2. 見出し行を `,` で分割する。先頭は `time`。それ以外の各列名を `^(?P<queue>.+)_(?P<field>NJOBS|PEND|RUN|SUSP)$` で解析し、**QUEUE 名 → {field: 列index}** の対応表を作る(DS-06-01)。
   * 対応表が空(1 つも QUEUE を抽出できない)なら `見出し不明` としてファイル全体を読み飛ばす。
3. 各データ行について、**QUEUE ごとに 1 レコードを生成する**(横持ち → 縦持ち。DS-06-02)。ある QUEUE で欠けているフィールドは NULL。
4. 時刻の解析(DS-06-03)。次の正規表現で末尾の 1/100 秒を切り落としてから `%Y-%m-%d %H:%M:%S` で解釈する。
   ```python
   m = re.match(r"^\s*(\d{4}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2}:\d{2})(?::\d+)?\s*$", token)
   ```
   * `-` 区切りと `/` 区切りの両方を受理する。`/` の場合は `-` に正規化してから解釈する。
   * 一致しなければ `日付書式不正`。
5. `lsf_queue` テーブルへ `BatchInserter` で入れる。
6. 列数が見出しと一致しない行は `列数不一致`。

### 【実装してはいけないこと】

* jstat の解析(T4 の担当)。
* QUEUE 名の正規化・大文字小文字変換(そのまま保持する)。
* 1/100 秒の値を保持すること(**切り捨てる**。`docs/P003-backend-spec.md` DS-06-03)。

### 【Unit Test内容】

* **テスト対象**: `dbconn.load`, `bqueues.load`
* **正常系テスト(dbconn)**
  * `docs/P001-requirement.md` 4.1 のサンプル 3 行(見出し + 2 データ行)を fixture に置き、2 行が格納され `ok_rows == 2` であること。値が正しいこと。
  * `active_connections` が `-` の行で NULL が入り、`ok_rows` に数えられること(スキップされないこと)。
* **正常系テスト(bqueues)**
  * QUEUE 3 個 × 4 フィールド = 12 列 + `time` の 13 列を持つ fixture で、1 データ行から **3 レコード**が生成されること。
  * **`docs/P001-requirement.md` 4.3 のサンプル時刻 `2026-06-01 00:00:00:02` が `2026-06-01 00:00:00` として格納されること**(1/100 秒の切り捨て。確定仕様)。
  * `/` 区切りの `2026/06/01 00:00:00` も受理されること。
  * 1/100 秒が無い `2026-06-01 00:00:00` も受理されること。
* **主要な異常系テスト(両方)**
  * 列数が足りない行 → `errors["列数不一致"] == 1`、`skipped_rows == 1`、**他の行は正常に格納されること**。
  * 日付が不正な行 → `errors["日付書式不正"] == 1`。
  * 数値でない行 → `errors["数値変換不能"] == 1`。
  * 空ファイル(0 バイト)→ `ok_rows == 0`、例外にならないこと。
  * 見出しのみのファイル → `ok_rows == 0`、例外にならないこと。
  * bqueues で、見出しに `_NJOBS` 等が 1 つも無いファイル → `errors["見出し不明"]` が記録され、`ok_rows == 0`。
  * CRLF 改行・BOM 付きの fixture でも正しく読めること。
* **合格条件**: 上記すべてが合格すること。

### 【実行コマンド】

```
python -m unittest discover -s app/tests/unit -t app -v
```

### 【完了条件】

* テストが `OK` で終わること。

### 【次タスクに進む前の停止条件】

* 単体テストを 3 回自己修正しても合格にできない場合は停止して報告する。

---

## U002-T4: jstat の解析と形式自動判別

### 【目的】

* M05 `loaders.jstat` を実装する。**`-gc` と `-gcutil` の 2 形式を自動判別して両方受け付ける。** 本スプリントで最も不確定性の高いタスクである。

### 【作成・編集対象ファイル】

* `app/src/s_anomaly/loaders/jstat.py` (新規)
* `app/tests/unit/test_jstat_loader.py` (新規)
* `app/tests/fixtures/` に jstat のサンプルを複数 (新規)

### 【参照すべき仕様箇所】

* `docs/P003-backend-spec.md` 5.3 DS-05-01 〜 DS-05-05
* `docs/P002-frontend-spec.md` 7.2 の判別フロー図、UI-07-01
* `docs/P001-requirement.md` 4.2、FR-012、5.2(`jvm_gc` の列構成)
* `docs/P001-requirement.md` 20章(列名 `S0C S1C S0U S1U` は確定仕様)

### 【実装内容】

1. 列名の定数を定義する。
   ```python
   COLS_GCUTIL = ["S0","S1","E","O","M","CCS","YGC","YGCT","FGC","FGCT","GCT"]           # 11
   COLS_GC_18  = ["S0C","S1C","S0U","S1U","EC","EU","OC","OU","MC","MU",
                  "CCSC","CCSU","YGC","YGCT","FGC","FGCT","GCT"]                          # 17
   ```
2. `detect_format(header_tokens, first_data_values_count) -> Optional[str]` を作る。**DS-05-01 の順序を厳守する。**
   1. **データ行の値の個数を優先して判定する**(DS-05-02)。時刻の 2 トークンを 1 つに結合した後の残りの個数が
      * `12` → `"gcutil"`(`Timestamp` + 11 列)
      * `17` または `18` → `"gc"`(`Timestamp` + 16/17 列)
   2. 1 で決まらない場合のみ、見出しの列名集合で判定する。`S0C` を含めば `gc`、`S0`/`E`/`O` を含めば `gcutil`。
   3. どちらでも決まらなければ `None`(呼び出し側が `見出し不明` として読み飛ばす)。
   * **この優先順位は極めて重要である。** `docs/P000-concept-analysis.md` の実サンプルは「見出しは `-gc` 系(17 列)だがデータは `-gcutil`(12 個)」という食い違いを持つ。見出しを優先するとファイル全体が `列数不一致` で失われる。
3. `load(con, logfile, progress) -> LoadResult` を作る。
   * 空白区切り(`line.split()`)で分割する。
   * **時刻は先頭 2 トークン**(`2026/06/01` と `00:00:01`)を結合し `%Y/%m/%d %H:%M:%S` で解釈する(DS-05-05)。
   * 形式に応じて列をマッピングする(DS-05-03)。
     * `gcutil`: `Timestamp, S0, S1, E, O, M, CCS, YGC, YGCT, FGC, FGCT, GCT` → `jvm_uptime_sec, s0u, s1u, eu, ou, mu, ccsu, ygc, ygct, fgc, fgct, gct`。**容量列(`s0c`,`s1c`,`ec`,`oc`,`mc`,`ccsc`)は NULL。**
     * `gc`(18 個): `Timestamp, S0C,S1C,S0U,S1U,EC,EU,OC,OU,MC,MU,CCSC,CCSU,YGC,YGCT,FGC,FGCT,GCT` → 同名の列。
     * `gc`(17 個): 上記から `CCSU` を除いたもの。**`ccsu` は NULL** とする(DS-05-04。Java 8 の `-gc` にはビルドによる列構成の差がある)。
   * `gc_format` 列に `"gc"` / `"gcutil"` を入れる。
   * `container` / `host` は `logfile` のメタ情報から入れる。
   * 列数が判別した形式と合わない行は `列数不一致`。
4. `jvm_gc` テーブルへ `BatchInserter` で入れる。

### 【実装してはいけないこと】

* `-gc` 形式のときに使用率を計算して `ou` へ入れること。**単位の変換は U004 `metrics` の担当**(`docs/P003-backend-spec.md` DS-07-05)。ここでは生の値をそのまま格納する。
* 累積値の差分の計算(U004 の担当)。
* `Timestamp` 列を時刻軸として使うこと(**`time` 列を使う**。`Timestamp` は再起動検出用に `jvm_uptime_sec` へ保存するだけ)。
* 形式を決め打ちすること。

### 【Unit Test内容】

* **テスト対象**: `jstat.detect_format`, `jstat.load`
* **正常系テスト**
  * **`-gcutil` 形式**(見出し `time Timestamp S0 S1 E O M CCS YGC YGCT FGC FGCT GCT`、データ 12 個)を読み、`gc_format == "gcutil"`、`ou` に O の値が入り、`oc` が NULL であること。
  * **`-gc` 形式 18 列**を読み、`gc_format == "gc"`、`ou` と `oc` の両方に値が入ること。
  * **`-gc` 形式 17 列**(`CCSU` なし)を読み、`ccsu` が NULL、他が正しく入ること。
  * **`docs/P001-requirement.md` 4.2 の実サンプルそのもの**(見出しは `-gc` 系 17 列、データは 12 個)を fixture に置き、**`gcutil` として解釈され、データ行が正常に格納されること**。これが本タスクの中心的な確認項目である。列数不一致で 0 行になってはならない。
  * `time` 列(2 トークン)が正しく `ts` に入り、`Timestamp`(3019.0)が `jvm_uptime_sec` に入ること。
  * `container` / `host` がファイル名由来の値であること。
* **主要な異常系テスト**
  * 見出しが `-gc` でも `-gcutil` でもなく、データ列数も 12/17/18 のいずれでもないファイル → `errors["見出し不明"]` が記録され、`ok_rows == 0`。
  * 途中の 1 行だけ列数が異なるファイル → その行だけ `列数不一致` で飛ばされ、他は格納されること。
  * 数値列に `-` がある行 → NULL として**正常に格納**されること(`数値変換不能` にしないこと。FR-015)。
  * 時刻が 1 トークンしかない行 → `日付書式不正`。
  * 空ファイル・見出しのみのファイル → `ok_rows == 0`、例外なし。
  * CRLF・BOM 付きでも読めること。
* **合格条件**: 上記すべてが合格すること。**特に「実サンプル形状」のテストが合格すること。**

### 【実行コマンド】

```
python -m unittest discover -s app/tests/unit -t app -v
```

### 【完了条件】

* テストが `OK` で終わること。
* `app/tests/fixtures/` に、`-gcutil`・`-gc`(17)・`-gc`(18)・**実サンプル形状(見出しとデータが食い違うもの)** の 4 種の jstat ファイルが存在すること。

### 【次タスクに進む前の停止条件】

* 単体テストを 3 回自己修正しても合格にできない場合は停止して報告する。
* **形式判別の仕様(DS-05-01/02)が実データと矛盾すると判断した場合は、勝手に仕様を変えず停止して報告する。**

---

## U002-T5: 重複排除と cli への結線

### 【目的】

* 重複レコードの排除(DS-LD-06)を実装し、`cli` に探索・解析を組み込んで終了コード 2 と 4 を完成させる。

### 【作成・編集対象ファイル】

* `app/src/s_anomaly/loaders/__init__.py` (編集: `dedupe_all` を追加)
* `app/src/s_anomaly/cli.py` (編集)
* `app/tests/unit/test_dedupe.py` (新規)
* `app/tests/unit/test_cli_ingest.py` (新規)

### 【参照すべき仕様箇所】

* `docs/P003-backend-spec.md` 5.5 DS-LD-06、12章 DS-01-03
* `docs/P002-frontend-spec.md` 5章 UI-05(終了コード 2, 4)、3.3(S3・S4 の進捗ログ)
* `docs/P001-requirement.md` FR-013, FR-091

### 【実装内容】

1. `dedupe_all(con) -> Dict[str, int]` を作る。3 テーブルそれぞれについて、`schema.LOGICAL_KEYS` の列で `row_number() OVER (PARTITION BY ... ORDER BY _load_seq)` を使い、`_rn = 1` の行だけを残す(DS-LD-06 の SQL)。捨てた件数をテーブル名ごとに返す。
2. `cli.main` に S3・S4 を組み込む。
   * `discovery.discover` を呼ぶ。**0 件なら `NoInputFileError`(終了コード 2)**。メッセージには探索したパスと対象パターンを含める(`docs/P002-frontend-spec.md` 5章)。
   * 種別に応じて 3 ローダを呼ぶ。**1 ファイル読むごとに** `[{処理済}/{総数}] {ファイル名} ({行数}行, スキップ{件})` を INFO で出す(`docs/P002-frontend-spec.md` 3.3 の S4 行)。ステップ ID は `S4`。
   * 解析できない行があったファイルは WARNING で理由と件数を出す。
   * `record_errors` で `load_error` に記録する。
   * `dedupe_all` を呼び、捨てた件数を INFO で出す。
   * **3 テーブルの合計有効レコード数が 0 なら `AllParseFailedError`(終了コード 4)**(DS-01-03)。ファイルごとの失敗理由をメッセージに含める。
   * 1 件でもあれば処理を続ける(FR-091)。この時点では S5 以降が未実装なので 0 を返す。
   * S5 相当の INFO(系列数・対象期間・総レコード数)は U004 で追加するため、ここでは総レコード数のみ出す。

### 【実装してはいけないこと】

* `metrics` の構築(U004 の担当)。
* 検知・レポート生成(U005 以降の担当)。
* 重複を 1 行ずつ `SELECT` で確認する実装(遅いため。**一括 SQL で行うこと**)。

### 【Unit Test内容】

* **テスト対象**: `loaders.dedupe_all`, `cli.main`(取り込みまで)
* **正常系テスト**
  * 同一論理 PK のレコードを `_load_seq` 違いで 2 件入れ、`dedupe_all` 後に**先に入れたほう(小さい `_load_seq`)が残る**こと(FR-013)。
  * 論理 PK が異なるレコードは残ること。
  * 3 テーブルすべてで重複排除が効くこと。
  * 3 種のログを含む一時ディレクトリで `main` が 0 を返し、テーブルに期待件数が入ること。
  * S3・S4 の進捗ログが標準出力に出ること(`[1/3] ...` の形式を含む)。
* **主要な異常系テスト**
  * **対象ファイル 0 件のディレクトリ → `main` が 2 を返し**、標準エラーに探索パスと対象パターンが出ること。
  * **全ファイルが破損していて有効レコード 0 件 → `main` が 4 を返す**こと。
  * **1 ファイルだけ壊れていて他が読める場合 → `main` が 0 を返す**こと(FR-091)。`load_error` に記録が残ること。
  * 重複排除を 2 回続けて実行しても結果が変わらないこと(冪等性)。
* **合格条件**: 上記すべてが合格すること。

### 【実行コマンド】

```
python -m unittest discover -s app/tests/unit -t app -v
```

### 【完了条件】

* テストが `OK` で終わり、件数が 0 でないこと。
* **手動で確認する**: 3 種のログを置いたディレクトリに対し `python app/s_anomaly.py <dir>` を実行し、終了コード 0 と S3・S4 の進捗ログを目視すること。空ディレクトリで終了コード 2 になることを目視すること。
* U002 の全タスク(T1〜T5)のテストが同時に合格すること。
* **`docs/P007-impl-direction.md` の U002 行を `[x]` に更新すること。**

### 【次タスクに進む前の停止条件】

* 単体テストを 3 回自己修正しても合格にできない場合は停止して報告する。

## 重要

* 各タスクの範囲外のファイルは編集しないでください。
* タスクの実装後、実行したテストコマンドと結果を報告してください。
* タスクが完了したら、上記「タスク一覧」の該当行を `[x]` に更新してください。
* 全タスクが完了したら、`docs/P007-impl-direction.md` の本スプリント行を `[x]` に更新してください。
* Executor Stepの停止条件に該当しない限り、次のタスクに自動的に進んでください。

---

> **P012 修正記録**: P012 により、矛盾点 #6(文字コードで読めないファイルの理由コードを `見出し不明` に統合)を修正した。 詳細は `docs/P011-impact-analysis.md` を参照。
