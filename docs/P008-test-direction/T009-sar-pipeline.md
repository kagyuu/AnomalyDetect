あなたはExecutor(実装担当)です。以下は1テストタスク分の作業範囲と完了条件を定義したものです。実施後は、結果(PASS/FAIL/BLOCKED/NOT RUNいずれであっても)を記録したうえで、Executor Stepの「停止条件」(`SKILL.md` 参照)に該当しない限り、`docs/P008-test-direction.md` のWBSに従って自動的に次のテストタスクへ進んでください。人間の指示を待って停止しないでください。

# 【テストID】T009 — sar 取り込みパイプラインの連携 (※CR-010)

## 【目的】

* `sa-*.csv` が **探索 → ローダ → `sar` テーブル → `metrics` → 検知 → イベント** まで通ることを、モジュールを連結して確認する。
* **★本テストの中心は「sar 由来のイベントが 1 件以上出ること」である。★** `metrics.DETECTABLE_METRICS` への登録漏れは**例外も警告も出さず、検知 0 件のまま正常終了する**(`docs/P003-backend-spec.md` DS-07-07b)。**「例外が出ないこと」を合格条件にしない。**
* 同一ホスト・同一時刻帯のアプリ層の異常と OS 資源の異常が、**「同時に発生したアノマリー」で相互に現れる**ことを確認する(P001 FR-150)。

## 【参照テスト計画】

* `docs/P006-test-plan.md` 5.2 の T-09、観点 F-21 / F-22 / F-23 / F-24
* `docs/P003-backend-spec.md` 5.4a(DS-SAR-01〜08)、6.3a(DS-07-07a / DS-07-07b)、7.14(DS-08-C1-01a / DS-08-C1-03a)
* `docs/P002-frontend-spec.md` 6.2.3a、6.3(`series_id` の書式)

## 【対象モジュール】

* `app/src/s_anomaly/loaders/sar.py`(M16)
* `app/src/s_anomaly/discovery.py` / `metrics.py` / `detectors/c1_saturation.py` / `events.py`
* 対象スプリント: **U010**

## 【前提条件】対象スプリントの全モジュールビルドが成功していること

* `docs/P008-test-direction.md` 4.1 のビルド確認を実行し、成功していること。
* U010 の全タスクが `[x]` であること。
* **失敗した場合、`BLOCKED` として記録し本テストへ進まない。**

## 【使用するテストデータ】

* `app/tests/_work/normal/logs/`(読み取り専用。`gen_testdata` が `sa-*.csv` も生成する)
* 加えて、**版差・未知ブロックを含む sar ファイル**を本テスト専用に一時ディレクトリへ作る。
  * 未知の見出し(`# hostname;interval;timestamp;INTR;intr/s`)を 1 ブロック
  * 既知の活動だが**未知の列を含む**見出し(`cpu` に `%guest` を足したもの)
  * **タイムゾーン表記が `Asia/Tokyo`** の `cpu` ブロック(設定は `UTC` のまま)

## 【事前準備】

1. スイート初期化がこのスイートの実行で 1 回だけ済んでいること(T001 参照)。
2. `tempfile.TemporaryDirectory` に本テスト専用の作業ディレクトリを作る。`app/tests/_work/` へは書き込まない。
3. DuckDB のインメモリ接続を作り、`schema.create_all` を実行する。

## 【実行手順】

1. `discovery.scan()` に `app/tests/_work/normal/logs/` を渡し、`kind == "sar"` のファイルが**期待件数**あることを確認する。
2. 全ファイルを対応するローダで読み、`dedupe_all` を実行する。
3. `metrics.build_jvm_gc_derived(con)` と `metrics.build_metrics(con)` を実行する。
4. `metrics.list_series(con)` を呼ぶ。
5. 全検知器を全系列に適用し、`events` でイベント化・採番する。
6. `co_anomaly` を実行する。
7. 上記の専用ディレクトリ(版差・未知ブロック入り)についても 1〜3 を実行する。

## 【期待結果】

| # | 確認すること | 判定 |
| --- | --- | --- |
| 1 | `discovery` が `sa-*.csv` を `kind == "sar"` として返し、`host` と `date` を正しく抽出する | 件数とホスト名の一致 |
| 2 | `sar` テーブルに、**8 活動すべて**のレコードが入っている | `SELECT DISTINCT activity` が 8 件 |
| 3 | `disk` と `network` が**デバイスごとに別レコード**になっている | `SELECT DISTINCT device WHERE activity='disk'` が 2 件以上 |
| 4 | `metrics` に `series_id LIKE 'sar/%'` の系列が入り、書式が `sar/{host}/{activity}/{device}` である | 正規表現で照合 |
| 5 | **`list_series` の戻り値に sar の系列が含まれる** | `[m for m in metas if m.source == "sar"]` が**空でない** |
| 6 | **★sar 由来のイベントが 1 件以上できる★** | `[e for e in events if e.source == "sar"]` が**空でない**。**0 件なら FAIL** |
| 7 | **`pct_idle` の系列からイベントが出ない**(ALG-C1 の除外。FR-148) | 99% 台で平坦な `pct_idle` に対し ALG-C1 の検知が 0 件 |
| 8 | **`pct_memused` の張り付きが ALG-C1 で検知され、`detail` の `estimated` が `False`** | 推定上限を使っていないこと |
| 9 | **sar のイベントが、同一ホストのアプリ層のイベントの「同時に発生したアノマリー」に現れる**(逆向きも) | 双方向で確認 |
| 10 | `events.extract_host("sar/host01/disk/sda")` が `"host01"` を返す | `unknown` でないこと |
| 11 | **未知の見出しブロックが読み飛ばされ、実行が止まらない**。WARNING が**見出しごとに 1 回**出る | ログの件数 |
| 12 | **既知の活動の未知の列(`%guest`)は WARNING を出さずに無視される** | ログに出ないこと |
| 13 | **TZ 表記の不一致で WARNING が出る**(ファイル単位で 1 回)。**処理は続行する** | ログと `ok_rows` |
| 14 | `[sar] activities = cpu,memory` に絞ると、`sar` テーブルの `activity` が 2 種類だけになる | `SELECT DISTINCT activity` が 2 件 |
| 15 | `sa-*.csv` を 1 件も含まないディレクトリでも、従来どおり全ステップが通る(**後方互換**) | 例外なく完了し、`sar` テーブルが 0 行 |

## 【完了条件】

* 上記 15 項目がすべて PASS であること。
* **とくに #6 が FAIL の場合は、`DETECTABLE_METRICS` への登録漏れを最初に疑うこと**(`docs/P003-backend-spec.md` DS-07-07b)。
