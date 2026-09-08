# app/ INDEX

s_anomaly のソースツリー。**配布時はこのディレクトリの中身一式をコピーする。**
設計の理由は `docs/ADR.md`、全体像は `docs/ArchitectureHandbook.md` を参照。

## エントリポイントと配布資産

- `s_anomaly.py` — エントリポイント。`src/` を `sys.path` に追加して `s_anomaly.cli:main()` を呼ぶだけの薄い起動スクリプト
- `settings.properties` — 既定の設定。**11 アルゴリズム**の ON/OFF と 32 個のパラメータ、**`[timezone]`(全て UTC。※CR-008。sar を含む 4 種別)**、**`[chart]`(※CR-009)**、**`[sar] activities`(※CR-010)**
- `VERSION` — バージョン文字列(現在 **0.7.0**。※CR-011)
- ※**利用者向け説明(FR-100 / FR-101)は、リポジトリ直下の [`../README.md`](../README.md) に一本化した。**
  以前は `app/README.md` にも同じ内容があったが、**片方だけが更新されて食い違う事故が実際に起きた**ため削除した。
  **配布時は `app/` と一緒に `README.md` もコピーすること**(`docs/P302-deliver.md` 6.1)
- `INDEX.md` — 本ファイル

## src/s_anomaly/ — アプリケーション本体

- `cli.py` — M01。引数解析・S1〜S9 の全体制御・**終了コードの決定(1 箇所に集約)**
- `bootstrap.py` — M14。`vendor/` の解決と DuckDB の初期化。終了コード 7 の発生源
- `config.py` — M02。`settings.properties` の読み込みと 2 段階検証(単項 → キー間の相関)。`PARAMS` が設定表そのもの。`[timezone]`(※CR-008)と `[chart]`(※CR-009)を含む
- `discovery.py` — M03。`{dir}` の再帰走査と **4 パターン**のファイル名判定 ※CR-010
- `errors.py` — `AppError` と 7 つの派生。終了コードとの対応表 `EXIT_CODE_MAP` を持つ
- `schema.py` — DuckDB の DDL・論理主キー・列型。冪等
- `metrics.py` — M07。**4 テーブル** → 統一テーブル `metrics` ※CR-010。segment 分割・累積値の差分・使用率の導出。**`DETECTABLE_METRICS` はハードコードの許可リストであり、ここに無いメトリクスは検知されない(例外も警告も出ない)**
- `events.py` — M09。検知点の統合・脅威度判定・**イベント ID の採番(`EVT-{YYYYMMDD}-{ホスト}-{連番}`。連番は「日 × ホスト」ごと)** ※CR-003。`extract_host` もここにある(DS-09-08)
- `co_anomaly.py` — M11。**同時に発生したアノマリーの突き合わせ** ※CR-002 により `correlate.py` から全面置換。`metrics` を参照せず、**検知済みイベント同士**を区間の重なりで突き合わせる(掃引法)。**`causes` より先に実行する**(ADR-010)。2 系統の出力を持つ: `co_anomalies`(レポート用。観点1 のみ)と `concurrent_by_metric`(原因候補ルール用。同一ホスト・観点を問わない)
- `causes.py` — M10。原因候補の 9 ルール(CR-01〜CR-09)。**入力は `co_anomaly.concurrent_by_metric`**(同時刻に同一ホストで検知された他メトリクスのイベント)※CR-002
- `reporter.py` — M12。**レポート群の生成** ※CR-001・CR-004。`report_{HOST}_{yyyymm}.md`(ホスト × 年月)と `report_summary_{yyyymm}.md`(全ホスト横断の集計)を、ファイルごとに一時ファイル経由で書く。**集計章は 4K トークンに収まる**(ADR-015)
- `charts.py` — **M15。アノマリーの状況を SVG の折れ線にする** ※CR-009。**外部ライブラリを使わず自前生成する**(ADR-020)。2 段構成(上=実単位 / 下=同時アノマリーを正規化して重ね合わせ)。**対象の選別は 2 つの枠に分ける**(脅威度順 + 同時アノマリーを持つもの)。枠を分けないと重ね合わせが一度も描かれない
- `progress.py` — M13。進捗ログ(標準出力=INFO、標準エラー=WARNING 以上)

### src/s_anomaly/loaders/ — 4 形式の解析 ※CR-010により 3 → 4

- `__init__.py` — 共通基盤。文字コード・改行の吸収、4 種の理由コード、`BatchInserter`(一時 CSV 経由の一括挿入)、重複排除
- `dbconn.py` — M04。DB コネクションログ(CSV)
- `jstat.py` — M05。JavaEE の GC 統計。**`-gc` / `-gcutil` をデータ行の列数から自動判別**
- `bqueues.py` — M06。LSF キュー状態。横持ち → 縦持ち変換、1/100 秒の切り捨て
- `sar.py` — **M16。sar(sysstat)の `sadf -d` 出力** ※CR-010 / **※CR-011 で NFS / NFSD を追加(8 → 10 活動)**。**活動種別ごとのブロックを見出し行の列名から判別**し、デバイス単位で縦持ちに展開する。**未知の活動・未知の列は読み飛ばす**(版差への耐性)。時刻列の TZ 表記を `[timezone] sar` と突き合わせて警告する。★`-n NFS` と `-n NFSD` は `read/s` と `sread/s` のように紛らわしい列を持つ。判別列は `retrans/s` / `scall/s`(片方にしか現れない)を使い、**完全一致で照合する**★

### src/s_anomaly/detectors/ — 11 個の異常検知アルゴリズム

- `__init__.py` — レジストリ `REGISTRY`(ID → 実装)と `enabled_detectors`
- `base.py` — `Detection` / `SkipInfo` / `Detector`、`effective_sigma`、持続性規則、季節差分、区間まとめ。**系列単位のキャッシュ**(`begin_series` / `fetch_series` / `fetch_segments`。※CR-006)
- **観点1(瞬間的な外れ値)**
  - `a1_ma_sigma.py` — ALG-A1 移動平均乖離率。残差を残差の σ で正規化
  - `a2_hampel.py` — ALG-A2 Hampel フィルタ。中央値と MAD で頑健に
  - `a3_tukey.py` — ALG-A3 Tukey の外れ値境界。系列全体の四分位数。5% 超過で全破棄
  - `a4_ewma.py` — ALG-A4 EWMA 管理図。前日同時刻との差分を平滑化
  - `a5_seasonal.py` — ALG-A5 曜日・時刻別ベースライン。leave-one-out 補正つき
- **観点2(持続的な増加)**
  - `b1_ma_cross.py` — ALG-B1 短期/長期移動平均のクロス継続
  - `b2_mann_kendall.py` — ALG-B2 Mann-Kendall 傾向検定と Theil-Sen 勾配
  - `b3_rolling_min.py` — ALG-B3 ローリング最小値の単調増加(**メモリリーク検出の主役**)
  - `b4_cusum.py` — ALG-B4 CUSUM。季節差分の累積和で水準シフトを検出。窓平均は**時刻昇順を利用した二分探索**で範囲を絞ってから走査する(`docs/ADR.md` ADR-013)。**検知点の 76% を出しており、S8 の入力量を決めている**
  - `b5_regression.py` — ALG-B5 線形回帰の傾きと決定係数
- **観点3(上限への張り付き)** ※CR-005 で新設
  - `c1_saturation.py` — **ALG-C1 上限への張り付き。** SQL 実装(gaps-and-islands)。**上限は入力に無ければ実測最大値から推定する**(`docs/ADR.md` ADR-016)

## tools/

- `gen_testdata.py` — テストデータ生成。7 種の異常を既知の位置に埋め込み `expected.json` を出力する。`--broken` で破損データを生成

## vendor/

閉域配布用に DuckDB を展開同梱する場所。**現在は空**(実体の整備は P302 / 配布時の作業)。
`vendor/{os}-{arch}-cp{major}{minor}/` の形でプラットフォーム別に置く。
無い場合は環境の DuckDB へフォールバックし、その事実をログと `report.md` に記録する。

## tests/ — 599 件

- `__init__.py` — **空ファイルではない。** `app/src` を `sys.path` へ追加するブートストラップ
- `fixtures/` — 手書きの極小テストデータ 17 ファイル(Git 管理)。文字コード 4 種、jstat 4 形式、破損ケース
- `_work/` — 生成テストデータ(**Git 管理しない**。スイート初期化が毎回作る)

### tests/unit/ — 単体テスト 426 件

- `test_bootstrap.py` — vendor 解決、DuckDB 初期化、例外の終了コード表
- `test_progress.py` — 出力先の振り分け、書式、二重登録の防止
- `test_schema.py` — 4 テーブルの列定義、冪等性
- `test_config.py` — 設定表の全キー、範囲・相関検証、未知キー
- `test_cli_args.py` — 引数バリデーション、usage
- `test_discovery.py` — ファイル名の判定と分解、走査順の安定性
- `test_loaders.py` — 共通基盤と 3 形式の解析、重複排除。**jstat の実サンプル形状**を含む
- `test_loaders_sar.py` — **sar(sysstat)の解析と各所への登録** ※CR-010 で新設 / ※CR-011 で NFS を追加。列名の正規化・**10 活動**の判別・版差への耐性・TZ 表記の検証に加え、**`DETECTABLE_METRICS` / `SOURCE_KINDS` / `NO_CEILING` への登録漏れ**を突き合わせで捕まえる。**`TestNfsIsIngested` / `TestNfsdAllZero`**(※CR-011。`TestNfsIsSkippedForNow` を置き換えた)。**NFS を取り込むこと**と、**全て 0 の `nfsd` 系列から検知が出ないこと**を確認する
- `test_cli_ingest.py` — 取り込みの結線、終了コード 2 / 4
- `test_gen_testdata.py` — 生成物の件数・決定性・埋め込んだ異常の形状
- `test_metrics.py` — segment、差分、使用率の導出、系列メタ情報
- `test_detectors_a.py` — 共通契約と ALG-A1〜A5
- `test_detectors_b.py` — ALG-B1〜B5、Mann-Kendall の数学、季節差分
- `test_events.py` — 統合、脅威度(SEVERE 条件)、**ID 採番(日 × ホスト)**、原因候補 9 ルール
- `test_co_anomaly.py` — **同時に発生したアノマリーの突き合わせ** ※CR-002 で新設。区間の重なり・観点1 限定・上限 10 件・並び順・再現性
- `test_charts.py` — **SVG チャートの生成・対象イベントの選別** ※CR-009 で新設。**重ね合わせが実際に描かれること**の回帰試験を含む
- `test_timezone.py` — **タイムゾーンの設定・投入時の変換・レポートへの併記** ※CR-008 で新設。**既定(全UTC)で変換が起きないこと**を厚く確認する
- `test_detectors_c.py` — **ALG-C1 上限への張り付き**と**系列単位のキャッシュ** ※CR-005 / CR-006 で新設。推定上限・実上限の使い分け・誤検知ガード 2 段・キャッシュの入れ替え
- `test_reporter.py` — イベント本文の 10 項目、**ホスト別 5 章構成とサマリ 4 章構成**、ホスト名の安全化、4K に収まること
- `test_cli_exitcodes.py` — **終了コード 9 種の網羅**

### tests/integration/ — 結合テスト 113 件

- `_setup_baseline.py` — スイート単位のベースライン復元(スイートで 1 回だけ実行)
- `report_parser.py` — `report.md` の機械解析。**レポート書式を変えたら同時に直すこと**
- `test_ingest_pipeline.py` — T001 取り込みパイプラインの連携
- `test_testdata_loadable.py` — T002 生成データの読み取り可能性
- `test_metrics_build.py` — T003 metrics 構築の連携
- `test_detectors_run.py` — T004 検知器の一括実行と失敗耐性、**並列実行が逐次と一致すること**(※CR-007)
- `test_events_order.py` — T005 **S7 → S8 → S7' の順序**
- `test_report_expected.py` — T006 レポートと正解の突き合わせ
- `test_tmpfile_cleanup.py` — T007 一時ファイルの後始末
- `test_progress_routing.py` — T008 進捗ログの振り分け
- `test_sar_pipeline.py` — **T009 sar 取り込みパイプラインの連携** ※CR-010 で新設。**sar 由来のイベントが実際に出ること**と、**アプリ層との「同時に発生したアノマリー」**を確認する
- `test_end_to_end.py` — U008-T4 通し確認・再現性・再起動耐性・破損データ

### tests/acceptance/ — 受け入れ・システムテスト 131 件

**すべてサブプロセスとしてアプリを起動し、外側から観測する**(P009 4.2)。

- `_harness.py` — 共通ハーネス。起動・レポート読み取り・揮発部分の除去・配布物の複製
- `test_a001_normal_run.py` — A001 正常系の通し実行と `expected.json` の突き合わせ
- `test_a002_exit_codes.py` — A002 終了コード 9 種のプロセスとしての再現
- `test_a003_reproducibility.py` — A003 3 回実行で一致。**スレッド数 1 と 8 でも一致**(※CR-007)
- `test_a004_restart.py` — A004 残骸がある状態での再起動
- `test_a005_cross_os.py` — A005 Windows と WSL2 Ubuntu の一致
- `test_a006_performance.py` — A006 大規模データでの完走と実行時間。**既定ではスキップ**(`S_ANOMALY_RUN_A006=1` で実行)。規模は**通常 30 日 / 大規模 90 日**。**CR-002 適用後は通常 70.9s / 大規模 229.2s**(目標 600s)
- `test_a007_memory_spill.py` — A007 `max_memory` を絞ったときの退避
- `test_a008_no_network.py` — A008 **AST による禁止 import の静的確認**と拡張自動DLの無効化
- `test_a009_broken_input.py` — A009 破損データでの完走と 4.1 節の集計
- `test_a010_portability.py` — A010 コピー先での実行と `vendor/` 不一致時の終了コード 7
- `test_a011_user_privilege.py` — A011 一般ユーザー権限・入力とソースツリーの不変性
- `test_a012_timezone.py` — A012 タイムゾーンの疎通確認(※CR-008)。**※CR-010 で sar が既知のキーになったことも確認する**
- `test_a013_charts.py` — A013 SVG チャートの出力確認(※CR-009)
- `test_a014_sar.py` — **A014 sar 取り込みの確認** ※CR-010 で新設。**sar 由来のイベントが実際にレポートへ出ること**、**`pct_idle` を「張り付き」と誤検知しないこと**、後方互換、`[sar] activities` の絞り込み

## テストの実行

```
# 全テスト
python -m unittest discover -s app/tests -t app -v

# 単体テストのみ
python -m unittest discover -s app/tests/unit -t app -v

# 単一のテストモジュール
python -m unittest discover -s app/tests/unit -t app -p "test_config.py" -v
```

**`-t app` を省略しないこと。** 省略すると `tests` パッケージを import できず、テストが 0 件で静かに成功する。
