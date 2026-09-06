あなたはExecutor(実装担当)です。以下は1スプリント分の作業範囲と完了条件を定義したものです。スプリントは複数のタスクから成り、各タスクに個別の完了条件とチェックボックスを持ちます。実施後は、そのタスクの完了条件を満たしたことを確認したうえで、Executor Stepの「停止条件」(`SKILL.md` 参照)に該当しない限り、自動的に次のタスクへ進んでください。人間の指示を待って停止しないでください。

# 【スプリントID】U001 — foundation

**位置づけ**: 最も不確定な要素(閉域環境への配布と DuckDB の読み込み)を最初に潰し、以降の全スプリントが動く土台を作る。

## タスク一覧(OKF副目次)

* 状態は `[ ]`(未着手) / `[~]`(進行中) / `[x]`(完了) の3種類とする。1タスクの作業を開始したら `[~]` に、完了条件をすべて満たしたら `[x]` に更新する。
* このスプリントファイル自体の状態(`docs/P007-impl-direction.md` の該当行)は、全タスクが `[x]` になって初めて `[x]` にする。1件以上のタスクが `[~]`/`[x]` で残りが `[ ]` の場合は `[~]` とする。
* **中断からの再開**: セッションが中断した場合、再開担当はまずこの一覧を確認する。`[~]` のタスクがあれば、それが中断時点の作業対象である。ただし `[~]` の状態だけで「どこまで終わっているか」は分からないため、再開時は必ず該当タスクの【完了条件】(単体テスト実行など)を実際に再実行して現状を確認してから、続きを行うか最初からやり直すかを判断する。`[ ]` のまま存在するファイル(先行して部分的に作成された形跡があるもの)は、対応するタスクが `[~]`/`[x]` でない限り「未着手」として扱い、内容を鵜呑みにしない。
* **先行実装の禁止**: 現在 `[~]` のタスクを進める際、まだ `[ ]` の後続タスク(同一スプリント内・他スプリントとも)が対象とするファイルには着手しない。

- [x] U001-T1 [プロジェクト初期化と DuckDB の読み込み](#u001-t1-プロジェクト初期化と-duckdb-の読み込み) — ディレクトリ構成の作成と bootstrap(M14)
- [x] U001-T2 [進捗ログ](#u001-t2-進捗ログ) — progress(M13)。出力先の振り分けと書式
- [x] U001-T3 [DuckDB スキーマ定義](#u001-t3-duckdb-スキーマ定義) — schema。4 テーブルの DDL
- [x] U001-T4 [設定ファイルの読み込みと検証](#u001-t4-設定ファイルの読み込みと検証) — config(M02)。約30キーの型・範囲・相関検証
- [x] U001-T5 [引数解析と終了コードの骨格](#u001-t5-引数解析と終了コードの骨格) — cli(M01)骨格。終了コード 1/3/7 の完成

---

## U001-T1: プロジェクト初期化と DuckDB の読み込み

### 【目的】

* コードの格納先 `app/` を初期化し、実行環境に応じて DuckDB を読み込む仕組み(M14 `bootstrap`)を作る。
* 閉域環境では `vendor/` から、開発環境では通常の import から DuckDB を読めるようにする。

### 【作成・編集対象ファイル】

* `app/s_anomaly.py` (新規)
* `app/VERSION` (新規)
* `app/src/s_anomaly/__init__.py` (新規)
* `app/src/s_anomaly/bootstrap.py` (新規)
* `app/tests/__init__.py` (新規。**空ファイルではない**。下記【実装内容】6 参照)
* `app/tests/unit/__init__.py`, `app/tests/integration/__init__.py`, `app/tests/acceptance/__init__.py` (新規、空ファイル)
* `app/tests/unit/test_bootstrap.py` (新規)

### 【参照すべき仕様箇所】

* `docs/P003-backend-spec.md` 1.1 ディレクトリ構成、DS-01-00、DS-01
* `docs/P003-backend-spec.md` 2章 DS-14-01 〜 DS-14-05
* `docs/P002-frontend-spec.md` 5章 UI-05(終了コード 7)
* `docs/P001-requirement.md` FR-001, FR-002, NFR-008, NFR-011

### 【実装内容】

1. **ディレクトリ構成を作成する。** `docs/P003-backend-spec.md` 1.1 のツリーに従い、`app/` 配下に `src/s_anomaly/`, `src/s_anomaly/loaders/`, `src/s_anomaly/detectors/`, `tools/`, `tests/unit/`, `tests/integration/`, `tests/acceptance/`, `tests/fixtures/`, `vendor/` を作る。パッケージディレクトリには空の `__init__.py` を置く。
   * **`uv` によるプロジェクト初期化(`pyproject.toml` の作成)は行わない。** `SKILL-P007-impl-direction.md` は Python の初期化に `uv` 相当の構成を目安として示しているが、本アプリは「配布資産をコピーすればそのまま動く」ことが要求(`docs/P001-requirement.md` FR-001)であり、ビルドツールもパッケージ定義も持たない素のスクリプトツリーとする。この逸脱は `docs/P007-impl-direction.md` 5章 #3 に記録済みである。
2. `app/VERSION` に `0.1.0` の 1 行を書く。
3. **`app/s_anomaly.py`** を作る。次だけを行う薄いスクリプトとする。
   * 自分の位置から `app/src` を求めて `sys.path` の先頭へ挿入する。
   * `from s_anomaly.cli import main` して `sys.exit(main(sys.argv[1:]))` する。
   * **この時点では `cli` は未実装なので、T5 完了までは import エラーになる。それでよい。** T5 で完成させる。
4. **`app/src/s_anomaly/bootstrap.py`** を作る。次の関数を持つ。
   * `platform_tag() -> str`: `"{os}-{arch}-cp{major}{minor}"` を返す。`os` は `sys.platform` が `win32` なら `win`、`linux` なら `linux`、それ以外は `sys.platform` の値そのもの。`arch` は `platform.machine()` を小文字化したもの。例: `win-amd64-cp314`, `linux-x86_64-cp310`。
   * `resolve_vendor_dir(app_root: Path) -> Optional[Path]`: `app_root / "vendor" / platform_tag()` が存在するディレクトリならそれを返し、無ければ `None` を返す。
   * `list_available_vendors(app_root: Path) -> List[str]`: `app_root / "vendor"` 直下のディレクトリ名を**ソートして**返す。`vendor/` 自体が無ければ空リスト。
   * `load_duckdb(app_root: Path, progress) -> module`: 次の順で試す。
     1. `resolve_vendor_dir` が `None` でなければ、そのパスの文字列を `sys.path` の**先頭**へ挿入してから `import duckdb`。成功したら INFO ログ「vendor から DuckDB を読み込みました: {パス}」を出して返す。
     2. 1 が失敗した(または `vendor` が無い)場合、そのまま `import duckdb` を試す。成功したら **INFO ログ「vendor を使わず環境の DuckDB を使用しています(バージョン {ver})。配布時は vendor/ の同梱が必要です」** を出して返す。この文言は `docs/P003-backend-spec.md` DS-14-02 の ★ACCEPTED★ が要求する「フォールバック経路を通ったことの記録」である。省略してはならない。
     3. どちらも失敗したら `VendorError` を送出する。メッセージは `docs/P003-backend-spec.md` DS-14-03 の 5 行の書式に厳密に従う(期待した vendor ディレクトリ、実行中の環境、用意されている vendor の一覧、対処)。
   * `open_connection(duckdb_module, max_memory: str, temp_directory: str, progress) -> connection`:
     * `temp_directory` のディレクトリが無ければ作る(`os.makedirs(..., exist_ok=True)`)。作成に失敗したら `ConfigError`(終了コード 3)を送出する。
     * `duckdb.connect(database=":memory:")` で接続する。
     * `SET memory_limit = '{max_memory}'` を実行する。例外になったら `SET max_memory = '{max_memory}'` を試す(`docs/P003-backend-spec.md` DS-14-04 の ★FIXME★)。両方失敗したら WARNING を出して続行する。
     * `SET temp_directory = '{temp_directory}'` を実行する。
     * `SET preserve_insertion_order = false` を実行する。
     * **`SET autoinstall_known_extensions = false` と `SET autoload_known_extensions = false` を実行する**(`docs/P003-backend-spec.md` DS-14-05)。存在しない設定名で例外になった場合は握りつぶして続行する(バージョン差があるため)。ただし WARNING は出す。
     * 接続を返す。
5. **`app/tests/__init__.py`** に、テストから `app/src` を import できるようにする `sys.path` ブートストラップを置く(`docs/P006-test-plan.md` TP-09a)。**空ファイルにしないこと。**
   ```python
   import sys
   import pathlib
   _SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
   if str(_SRC) not in sys.path:
       sys.path.insert(0, str(_SRC))
   ```
   * `tests` パッケージが import された時点で必ず実行されるため、`discover` でも単一メソッド指定でも確実に効く。
   * **これはテスト専用の仕組みである。** アプリ本体(`app/s_anomaly.py` → `app/src/`)は `app/tests/` に依存しない(`docs/P009-acceptance-direction/A010-portability.md` のケース C がこの独立性を確認する)。
6. **`app/src/s_anomaly/errors.py`** を作り、`docs/P003-backend-spec.md` DS-01-01 の例外クラスを定義する。`AppError` を基底とし、`exit_code` クラス属性と `user_message()` を持つ。サブクラスは `ArgumentError`(1), `NoInputFileError`(2), `ConfigError`(3), `AllParseFailedError`(4), `ReportWriteError`(5), `StorageError`(6), `VendorError`(7)。
   * `errors.py` は `docs/P003-backend-spec.md` 1.1 のツリーに記載済みである(P012 で追加)。`cli.py` に置くと `loaders` や `bootstrap` が `cli` を import することになり、DS-02(依存は一方向)に反するため、独立したモジュールとする。

### 【実装してはいけないこと】

* `cli.py` / `config.py` / `progress.py` / `schema.py` の中身(T2〜T5 の担当)。
* `pyproject.toml` / `setup.py` / `requirements.txt` の作成。
* `vendor/` に実際の DuckDB を展開すること(P302 の担当)。`vendor/` は空ディレクトリでよい。
* `duckdb` 以外の外部パッケージの import。

### 【Unit Test内容】

* **テスト対象**: `bootstrap.platform_tag`, `resolve_vendor_dir`, `list_available_vendors`, `load_duckdb`, `open_connection`
* **正常系テスト**
  * `platform_tag()` が `{文字列}-{文字列}-cp{数字2〜3桁}` の形になること(正規表現で検証)。実行中の Python のバージョンが含まれること。
  * `resolve_vendor_dir` が、一時ディレクトリに `vendor/{platform_tag()}/` を作った状態でそのパスを返すこと。
  * `list_available_vendors` が、複数のディレクトリを作った状態でソート済みのリストを返すこと。
  * `load_duckdb` が、`vendor/` の無い一時ディレクトリに対してもフォールバックで DuckDB を返すこと(開発環境には DuckDB が入っているため)。
  * `open_connection` が接続を返し、`SELECT 1` が実行できること。`temp_directory` に指定した存在しないディレクトリが作成されること。
* **主要な異常系テスト**
  * `list_available_vendors` が、`vendor/` 自体が存在しないときに空リストを返すこと(例外にしない)。
  * `VendorError.exit_code` が 7 であること。`ConfigError.exit_code` が 3 であること。他の例外クラスについても `docs/P002-frontend-spec.md` 5章の表と一致すること(表駆動テストで 7 クラスすべてを確認)。
  * `open_connection` の `temp_directory` に、ファイルが既に存在するパス(ディレクトリを作れないパス)を渡すと `ConfigError` になること。
* **合格条件**: 上記すべてが合格し、失敗・エラーが 0 件であること。

### 【実行コマンド】

```
# Windows
python -m unittest discover -s app/tests/unit -t app -v
```

### 【完了条件】

* 上記テストコマンドが `OK` で終わり、**実行されたテスト件数が 0 件でないこと**(`Ran N tests` の N を目視すること)。
* `app/` 配下に、`docs/P003-backend-spec.md` 1.1 のディレクトリ構成が存在すること。
* `python -c "import sys; sys.path.insert(0,'app/src'); from s_anomaly import bootstrap; print(bootstrap.platform_tag())"` が実行でき、タグが表示されること。

### 【次タスクに進む前の停止条件】

* 単体テストを 3 回自己修正しても合格にできない場合は、処理を停止して人間に報告する(`SKILL.md` Executor Step の停止条件)。
* `import duckdb` が開発環境でも失敗する場合は、環境側の問題であるため停止して報告する(`pip install duckdb` は実施済みのはずである)。

---

## U001-T2: 進捗ログ

### 【目的】

* M13 `progress` を実装し、以降の全モジュールが使う進捗ログの土台を作る。

### 【作成・編集対象ファイル】

* `app/src/s_anomaly/progress.py` (新規)
* `app/tests/unit/test_progress.py` (新規)

### 【参照すべき仕様箇所】

* `docs/P002-frontend-spec.md` 3章 UI-03-01 〜 UI-03-03、3.2 行の書式、3.3 出力する内容
* `docs/P003-backend-spec.md` 13章 DS-13-01 〜 DS-13-04
* `docs/P001-requirement.md` FR-031, FR-033

### 【実装内容】

1. `setup(stream_out=None, stream_err=None) -> Progress` を作る。テストで差し替えられるよう、**出力先ストリームを引数で受け取れる**ようにする(既定は `sys.stdout` / `sys.stderr`)。
2. `logging` を使い、ハンドラを 2 つ設ける。
   * stdout ハンドラ: レベル `INFO`。**`INFO` のみを通すフィルタ**を付ける(`record.levelno == logging.INFO`)。WARNING 以上を stdout に出してはならない。
   * stderr ハンドラ: レベル `WARNING`。
3. 書式は `%(asctime)s [%(levelname)s] [%(step)s] %(message)s`、日付書式は `%Y-%m-%d %H:%M:%S`。
   * `step` は `extra={"step": "S6"}` で渡す。**未指定のときに `KeyError` にならないよう、`logging.Filter` または `LoggerAdapter` で既定値 `--` を注入する**こと。
4. `Progress` クラスに次のメソッドを持たせる。すべて `step` を第1引数に取る。
   * `info(step, msg)`, `warning(step, msg)`, `error(step, msg)`
   * `progress_count(step, done, total, msg)`: `[{done}/{total}] {msg}` の形で INFO を出す。
5. 起動時に `sys.stdout.reconfigure(line_buffering=True)` を試みる(`docs/P003-backend-spec.md` DS-13-03)。`reconfigure` が無い環境(古い Python、差し替えたストリーム)では `AttributeError` を握りつぶす。
6. **時刻はローカル時刻を使う**(DS-13-04)。タイムゾーン変換を行わない。

### 【実装してはいけないこと】

* ログファイルへの出力(`docs/P001-requirement.md` NFR-005 により、標準出力・標準エラー出力のみ)。
* 色付け・プログレスバーなどの装飾(リダイレクト先で制御文字が混ざるため)。
* `logging.basicConfig` の使用(ルートロガーを汚し、テストで分離できなくなるため)。専用の `Logger` インスタンスを作り `propagate = False` にすること。

### 【Unit Test内容】

* **テスト対象**: `progress.setup` が返す `Progress` の各メソッド
* **正常系テスト**
  * `io.StringIO` を 2 本渡し、`info` の出力が**stdout 側にだけ**現れ、stderr 側が空であること。
  * `warning` / `error` の出力が**stderr 側にだけ**現れ、stdout 側が空であること。
  * 出力行が正規表現 `^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \[(INFO|WARNING|ERROR)\] \[\S+\] .+$` に一致すること。
  * `step` に `"S6"` を渡すと `[S6]` が出ること。
  * `progress_count(step, 3, 10, "処理中")` が `[3/10] 処理中` を含むこと。
* **主要な異常系テスト**
  * `step` を省略できるメソッド呼び出し(あれば)で `[--]` になること。`KeyError` を送出しないこと。
  * 同じ `setup` を 2 回呼んでもハンドラが二重に付かず、**出力が重複しない**こと(`logging` でよくある事故)。
* **合格条件**: 上記すべてが合格すること。

### 【実行コマンド】

```
python -m unittest discover -s app/tests/unit -t app -v
```

### 【完了条件】

* テストが `OK` で終わり、件数が 0 でないこと。
* T1 のテストも引き続き合格していること(退行がないこと)。

### 【次タスクに進む前の停止条件】

* 単体テストを 3 回自己修正しても合格にできない場合は停止して報告する。

---

## U001-T3: DuckDB スキーマ定義

### 【目的】

* `docs/P002-frontend-spec.md` 6.2 のテーブル定義を DDL として実装する。

### 【作成・編集対象ファイル】

* `app/src/s_anomaly/schema.py` (新規)
* `app/tests/unit/test_schema.py` (新規)

### 【参照すべき仕様箇所】

* `docs/P002-frontend-spec.md` 6.2.1 〜 6.2.5(4 テーブルの列定義)、6.4 スキーマの適用方式
* `docs/P003-backend-spec.md` DS-LD-06(`_load_seq` 列)
* `docs/P001-requirement.md` FR-020

### 【実装内容】

1. `create_all(con) -> None` を作り、次の 4 テーブルを `CREATE TABLE IF NOT EXISTS` で作成する。
   * `db_connection`: `ts TIMESTAMP, host VARCHAR, port INTEGER, datasource VARCHAR, active_connections BIGINT, _load_seq BIGINT`
   * `jvm_gc`: `ts TIMESTAMP, container VARCHAR, host VARCHAR, jvm_uptime_sec DOUBLE, gc_format VARCHAR, s0c DOUBLE, s1c DOUBLE, s0u DOUBLE, s1u DOUBLE, ec DOUBLE, eu DOUBLE, oc DOUBLE, ou DOUBLE, mc DOUBLE, mu DOUBLE, ccsc DOUBLE, ccsu DOUBLE, ygc BIGINT, ygct DOUBLE, fgc BIGINT, fgct DOUBLE, gct DOUBLE, _load_seq BIGINT`
   * `lsf_queue`: `ts TIMESTAMP, host VARCHAR, queue VARCHAR, njobs BIGINT, pend BIGINT, run BIGINT, susp BIGINT, _load_seq BIGINT`
   * `load_error`: `file_path VARCHAR, reason VARCHAR, line_count BIGINT`
2. **`NOT NULL` 制約や主キー制約は付けない。** `docs/P002-frontend-spec.md` 6.2 は「論理 PK」と記しており、参照整合性制約は張らない方針である(6.1 の注)。制約で INSERT が失敗すると、破損行を読み飛ばす方針(FR-014)と衝突する。
3. `LOGICAL_KEYS: Dict[str, List[str]]` を定義する。重複排除(U002-T5)が使う。
   * `db_connection`: `["ts", "host", "port", "datasource"]`
   * `jvm_gc`: `["ts", "container", "host"]`
   * `lsf_queue`: `["ts", "host", "queue"]`
4. `create_all` は**冪等**であること。同じ接続で 2 回呼んでも失敗しない(`docs/P002-frontend-spec.md` 6.4)。

### 【実装してはいけないこと】

* `metrics` テーブルの作成(U004 の担当)。
* マイグレーションのバージョン管理テーブル(`docs/P002-frontend-spec.md` 6.4 の ★ACCEPTED★ で不採用と決定済み)。
* データの投入(U002 の担当)。

### 【Unit Test内容】

* **テスト対象**: `schema.create_all`, `schema.LOGICAL_KEYS`
* **正常系テスト**
  * インメモリ接続に対して `create_all` を実行後、`SELECT * FROM db_connection` 等の 4 テーブルすべてがエラーなく実行でき、0 行を返すこと。
  * 各テーブルの列名の集合が、`docs/P002-frontend-spec.md` 6.2 の定義と一致すること(`DESCRIBE {table}` の結果と期待リストを突き合わせる。**4 テーブルすべてについて表駆動で確認する**)。
  * `LOGICAL_KEYS` の各値が、そのテーブルに実在する列名のみを含むこと。
* **主要な異常系テスト**
  * **`create_all` を 2 回続けて呼んでも例外にならないこと**(冪等性。`docs/P006-test-plan.md` O-01)。
  * 各数値列に NULL を INSERT できること(FR-015 で NULL 許容が必要なため)。
* **合格条件**: 上記すべてが合格すること。

### 【実行コマンド】

```
python -m unittest discover -s app/tests/unit -t app -v
```

### 【完了条件】

* テストが `OK` で終わること。
* 先行タスクのテストも引き続き合格していること。

### 【次タスクに進む前の停止条件】

* 単体テストを 3 回自己修正しても合格にできない場合は停止して報告する。

---

## U001-T4: 設定ファイルの読み込みと検証

### 【目的】

* M02 `config` を実装し、`settings.properties` の約 30 キーを型・範囲・相関の 3 観点で検証する。
* 既定の `settings.properties` を作る。

### 【作成・編集対象ファイル】

* `app/src/s_anomaly/config.py` (新規)
* `app/settings.properties` (新規)
* `app/tests/unit/test_config.py` (新規)

### 【参照すべき仕様箇所】

* `docs/P002-frontend-spec.md` 2章全体(2.1 探索場所、2.2 キー一覧とバリデーションルールの表、UI-02-V01 〜 V03)
* `docs/P003-backend-spec.md` 3章 DS-02-01 〜 DS-02-04
* `docs/P001-requirement.md` FR-080 〜 FR-084

### 【実装内容】

1. **`docs/P002-frontend-spec.md` 2.2 の表を、そのままデータとして持つ。** `PARAMS: List[Param]` の宣言的なリストとし、`Param` は `section`, `key`, `kind`(`bool`/`int`/`float`/`str`), `default`, `min_value`, `max_value` を持つ dataclass とする。ハードコードした `if` の羅列にしないこと。**`docs/P002-frontend-spec.md` 2.2 の表の全行**ぶんの要素を作る(`[algorithms]` / `[parameters]` / `[duckdb]` の 3 セクション)。件数を数え上げてハードコードせず、下記の単体テストで「表のキー名の集合と `PARAMS` のキー名の集合が完全一致すること」を確認する方式で担保する。
   * **表の全行を漏れなく写すこと。** 特に P003 15章で追加された 5 キー(`common.sigma_floor_ratio`, `common.sigma_floor_abs`, `ALG-B2.max_buckets`, `events.severe_pct`, `events.merge_gap_minutes`)を忘れないこと。
2. `load(path: Optional[Path], progress) -> Config` を作る。
   * `path` が `None` または存在しない場合、全キー既定値の `Config` を返し、INFO ログ「settings.properties が見つかりません。既定値で動作します」を出す(UI-02-L01)。
   * `configparser.ConfigParser()` で読む。真偽値は `getboolean` の受理範囲に従う(DS-02-03)。
3. **検証は 2 段階**(DS-02-02)。
   * 単項検証: 型変換と範囲。
   * 相関検証: `ALG-A1.k_fatal > ALG-A1.k_warn`、`ALG-A3.iqr_fatal > ALG-A3.iqr_warn`、`ALG-B1.long_minutes > ALG-B1.short_minutes`、`ALG-B2.p_fatal < ALG-B2.p_warn`。
   * **最初の 1 件で止めず、全違反を集めて一括で出力する。** `ConfigError` のメッセージに全件を並べる。
   * 書式は `docs/P002-frontend-spec.md` UI-02-V02 の 3 行の形に厳密に従う。
4. `max_memory` は正規表現 `^\d+(\.\d+)?(KB|MB|GB|TB)$`(大文字小文字を区別しない)で検証する。
5. 未知のセクション・未知のキーは WARNING を出して無視する(UI-02-V01)。**`[algorithms]` セクション内の未知キー**については、DS-02-04 に従い「アルゴリズム ID として解釈されませんでした。有効な ID は ALG-A1, ..., ALG-B5 です」というより強い文言にする。
6. `Config` は次を提供する。
   * `enabled_algorithms() -> List[str]`: `[algorithms]` が `true` のものを ID 昇順で返す。
   * `param(key: str) -> Any`: `[parameters]` の値を返す。例: `cfg.param("ALG-A1.window_minutes")`。
   * `max_memory` / `temp_directory` プロパティ。
   * `all_disabled() -> bool`: 全アルゴリズムが `false` かどうか(UI-02-V03 の判定に使う)。
7. **`app/settings.properties` を作る。** `docs/P002-frontend-spec.md` 2.2 の全キーを、既定値とともに書き、各行にコメントで意味を添える。これは配布資産であり、利用者が最初に読むファイルである。

### 【実装してはいけないこと】

* 表に無い設定キーの追加。
* 設定値に応じてアルゴリズムを実行すること(U005 以降の担当)。
* 環境変数からの設定読み込み(`docs/P002-frontend-spec.md` に無い)。

### 【Unit Test内容】

* **テスト対象**: `config.load`, `Config` の各メソッド, `PARAMS` の定義
* **正常系テスト**
  * ファイルが無いときに全既定値が返り、例外にならないこと。
  * `PARAMS` の要素数が `docs/P002-frontend-spec.md` 2.2 の表の行数と一致すること。**表の全キー名の集合が `PARAMS` のキー名の集合と完全一致すること**(テストコード内に期待するキー名の一覧を書き、集合比較する)。
  * 一部のキーだけを書いた設定ファイルで、書いたキーだけが上書きされ、他は既定値であること。
  * 真偽値の各表記(`true`/`TRUE`/`yes`/`on`/`1` と `false`/`no`/`off`/`0`)がすべて受理されること。
  * `enabled_algorithms()` が ID 昇順であること。
  * `all_disabled()` が、全 10 キーを `false` にしたときだけ `True` になること。
* **主要な異常系テスト**
  * **表駆動で、各パラメータキーに範囲外の値を与えて `ConfigError` になること**を一巡確認する(`docs/P006-test-plan.md` TP-17 の網羅方針)。例: `ALG-A4.lambda = 1.5`, `common.min_points = 1`, `ALG-B2.p_warn = 1.0`。
  * 数値キーに数値でない文字列を与えると `ConfigError` になること。
  * 相関違反の 4 種すべて(`k_fatal <= k_warn` など)が `ConfigError` になること。
  * **複数の違反があるとき、エラーメッセージに全件が含まれること**(1 件目で止まらないこと)。
  * `max_memory = "4 gigabytes"` のような不正表記が `ConfigError` になること。
  * 未知のキー・未知のセクションで例外にならず、WARNING が出ること(`progress` に `io.StringIO` を渡して検証)。
  * `[algorithms]` に `ALG-A6` を書いたとき、WARNING に「有効な ID は」という文言が含まれること。
  * `ConfigError.exit_code` が 3 であること。
* **合格条件**: 上記すべてが合格すること。

### 【実行コマンド】

```
python -m unittest discover -s app/tests/unit -t app -v
```

### 【完了条件】

* テストが `OK` で終わること。
* `app/settings.properties` が存在し、それを `config.load` で読んだときに検証エラーが 0 件であること(**既定の設定ファイル自体が妥当であることの確認**。これをテストケースとして含めること)。

### 【次タスクに進む前の停止条件】

* 単体テストを 3 回自己修正しても合格にできない場合は停止して報告する。

---

## U001-T5: 引数解析と終了コードの骨格

### 【目的】

* M01 `cli` の骨格を実装し、引数バリデーション(P002 UI-01)と終了コード 1 / 3 / 7 を完成させる。
* `app/s_anomaly.py` から起動できる状態にする。

### 【作成・編集対象ファイル】

* `app/src/s_anomaly/cli.py` (新規)
* `app/tests/unit/test_cli_args.py` (新規)

### 【参照すべき仕様箇所】

* `docs/P002-frontend-spec.md` 1章全体(1.1 書式、1.2 バリデーションルール、UI-01-V01 〜 V04、1.3 usage の文言)
* `docs/P002-frontend-spec.md` 5章 UI-05 の終了コード表
* `docs/P003-backend-spec.md` 12章 DS-01-01 〜 DS-01-04
* `docs/P001-requirement.md` FR-030, FR-090

### 【実装内容】

1. `parse_args(argv: List[str]) -> Path` を作る。
   * 引数が 0 個 → usage を標準エラーへ出して `ArgumentError`(UI-01-V01)。
   * 引数が 2 個以上 → 「引数は 1 個です」と受け取った引数を列挙して `ArgumentError`(UI-01-V02)。
   * パスが存在しない / ディレクトリでない / 読めない → `docs/P002-frontend-spec.md` UI-01-V03 の 2 行の書式で `ArgumentError`(理由は「存在しません」「ディレクトリではありません」「読み取り権限がありません」のいずれか)。
   * 正常なら `Path(...).resolve()` を返す。
   * **`argparse` を使ってもよいが、上記のメッセージ書式を満たすこと。** `argparse` の既定のエラー文言をそのまま使ってはならない。`argparse` の `error()` をオーバーライドするか、手書きで検証する。
2. `USAGE: str` に `docs/P002-frontend-spec.md` 1.3 の文言をそのまま持つ。
3. `main(argv: List[str]) -> int` を作る。`docs/P003-backend-spec.md` DS-01-02 の骨格に従う。
   * `try` 内: 起動ログ(バージョン、Python 版、DuckDB 版、対象ディレクトリ) → `parse_args` → `bootstrap.load_duckdb` → `config.load` → `bootstrap.open_connection` → `schema.create_all` まで行い、**この時点では 0 を返す**(後続は U002 以降で追加する)。
   * `except AppError as e:` → `progress.error` でメッセージを出し `e.exit_code` を返す。
   * `except Exception:` → `traceback.print_exc(file=sys.stderr)` して 99 を返す。
   * **`sys.exit` を `main` の中で呼ばないこと**(テスト容易性のため。DS-01-01)。`sys.exit` は `s_anomaly.py` の側で行う。
4. **`main` は `progress` の出力先を差し替えられるようにする。** シグネチャを `main(argv, stream_out=None, stream_err=None) -> int` とし、テストから `io.StringIO` を渡せるようにする。
5. `app/s_anomaly.py` を完成させ、`sys.exit(main(sys.argv[1:]))` を呼ぶようにする。

### 【実装してはいけないこと】

* ファイル探索・解析・検知・レポート生成(U002 以降の担当)。
* 終了コード 0 以外の正常系の分岐。
* `--help` 以外のオプション追加(`docs/P002-frontend-spec.md` 9.1 で拡張しないと決定済み)。**`--from` / `--to` / `--output` / `--config` を追加してはならない。**

### 【Unit Test内容】

* **テスト対象**: `cli.parse_args`, `cli.main`, `cli.USAGE`
* **正常系テスト**
  * 実在するディレクトリを渡すと、その絶対パスが返ること。
  * 相対パスを渡すと絶対パスに解決されること。
  * `main([str(既存の空ディレクトリ)])` が 0 を返すこと(この時点では解析まで行かないため)。
  * 起動ログに Python バージョンと DuckDB バージョンが含まれること。
* **主要な異常系テスト**
  * 引数 0 個 → 戻り値 1、標準エラーに usage が出ること。
  * 引数 2 個 → 戻り値 1、標準エラーに「引数は 1 個です」が出ること。
  * 存在しないパス → 戻り値 1、標準エラーに「存在しません」が出ること。
  * ファイルのパス(ディレクトリでない)→ 戻り値 1、「ディレクトリではありません」が出ること。
  * 不正な `settings.properties` を置いたディレクトリで実行 → 戻り値 3。
  * `main` の中で意図的に例外を起こすように `bootstrap.load_duckdb` を差し替え、戻り値 99 かつ標準エラーにスタックトレースが出ること。
  * **`sys.exit` が `main` から呼ばれていないこと**(`main` の戻り値でテストできている時点で満たされるが、`SystemExit` を捕捉しないテストを書くこと)。
* **合格条件**: 上記すべてが合格すること。

### 【実行コマンド】

```
python -m unittest discover -s app/tests/unit -t app -v
```

### 【完了条件】

* テストが `OK` で終わり、件数が 0 でないこと。
* **実際にコマンドを起動して確認する**(単体テストとは別に、手で 1 回実行して目視すること)。
  * `python app/s_anomaly.py` → usage が標準エラーに出て、終了コード 1(`echo $?` / `$LASTEXITCODE` で確認)。
  * `python app/s_anomaly.py app` → 終了コード 0、起動ログが標準出力に出る。
* U001 の全タスク(T1〜T5)のテストが同時に合格すること。
* **`docs/P007-impl-direction.md` の U001 行を `[x]` に更新すること。**

### 【次タスクに進む前の停止条件】

* 単体テストを 3 回自己修正しても合格にできない場合は停止して報告する。
* 手動での起動確認で、終了コードが期待値と異なる場合は停止して報告する。

## 重要

* 各タスクの範囲外のファイルは編集しないでください。
* タスクの実装後、実行したテストコマンドと結果を報告してください。
* タスクが完了したら、上記「タスク一覧」の該当行を `[x]` に更新してください。
* 全タスクが完了したら、`docs/P007-impl-direction.md` の本スプリント行を `[x]` に更新してください。
* Executor Stepの停止条件(`SKILL.md` 参照。例: 単体テストが3回自己修正しても合格しない)に該当しない限り、次のタスクに自動的に進んでください。1タスクごとに人間の指示を待つ必要はありません。

---

> **P012 修正記録**: P012 により、矛盾点 #2(`app/tests/__init__.py` の `sys.path` ブートストラップ)、#7(`errors.py` の注記)、#8(設定キー件数のハードコード廃止)を修正した。 詳細は `docs/P011-impact-analysis.md` を参照。
