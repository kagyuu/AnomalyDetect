"""M01 cli — 引数解析・全体制御・終了コードの決定 (P002 1章 / P003 12章)。

終了コードはこのモジュールの main() が 1 箇所で決める。
各モジュールは例外か戻り値で失敗を伝え、自分で sys.exit しない。
"""

import os
import sys
import time
import traceback
from datetime import datetime
from typing import List, Optional

from . import (
    bootstrap, causes, charts, co_anomaly, config, detectors, discovery, events,
    loaders, metrics, progress as progress_mod, reporter, schema,
)
from concurrent import futures
import threading

from .detectors import base as detectors_base

from .errors import (
    AllParseFailedError, AppError, ArgumentError, NoInputFileError,
    StorageError, UNEXPECTED_EXIT_CODE,
)
from .loaders import bqueues, dbconn, jstat

USAGE = """usage: s_anomaly.py <dir>

  <dir>  解析対象のログが格納されたディレクトリ。再帰的に探索します。

対象となるファイル名:
  DBConnection_{yyyymmdd}.csv                    DBコネクションログ
  {コンテナ名}_gc_{ホスト名}_{yyyymmdd}.txt        JavaEE の jstat 出力
  bqueues_{ホスト名}_{yyyymmdd}.txt               LSF のキュー状態

結果はカレントディレクトリの report.md に出力します。
アルゴリズムの ON/OFF は settings.properties で切り替えます。"""


#: DuckDB 例外の変換時にメッセージへ含める現在の設定値
_current_max_memory = ["(未設定)"]
_current_temp_directory = ["(未設定)"]


def _duckdb_errors():
    """DuckDB の例外基底クラスを返す。存在しなければ捕捉しない型を返す。"""
    try:
        import duckdb
    except Exception:
        return ()
    base = getattr(duckdb, "Error", None)
    if base is None:
        return ()
    return (base,)


def app_root():
    """app/ ディレクトリの絶対パスを返す。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def read_version():
    path = os.path.join(app_root(), "VERSION")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return "unknown"


def parse_args(argv: List[str]) -> str:
    """引数を検証して対象ディレクトリの絶対パスを返す (P002 1.2)。"""
    if len(argv) == 0:
        raise ArgumentError("引数が指定されていません", USAGE)
    if len(argv) > 1:
        raise ArgumentError(
            "引数は 1 個です。受け取った引数: {0}".format(", ".join(argv)), USAGE
        )

    raw = argv[0]
    if not os.path.exists(raw):
        raise ArgumentError(
            "指定されたパスを読み取れません: {0}".format(raw), "  理由: 存在しません"
        )
    if not os.path.isdir(raw):
        raise ArgumentError(
            "指定されたパスを読み取れません: {0}".format(raw),
            "  理由: ディレクトリではありません",
        )
    if not os.access(raw, os.R_OK):
        raise ArgumentError(
            "指定されたパスを読み取れません: {0}".format(raw),
            "  理由: 読み取り権限がありません",
        )
    return os.path.realpath(raw)


def main(argv: List[str], stream_out=None, stream_err=None,
         now: Optional[datetime] = None) -> int:
    """アプリ本体。終了コードを返す。sys.exit は呼ばない (DS-01-01)。"""
    started = time.monotonic()
    progress = progress_mod.setup(stream_out, stream_err)
    err = stream_err if stream_err is not None else sys.stderr
    root = app_root()
    exit_code = 0
    try:
        duckdb_module, vendor_note = bootstrap.load_duckdb(root, progress)
        progress.info(
            "--",
            "s_anomaly {0} を起動しました (Python {1}.{2}.{3}, DuckDB {4})".format(
                read_version(),
                sys.version_info[0], sys.version_info[1], sys.version_info[2],
                getattr(duckdb_module, "__version__", "unknown"),
            ),
        )

        target = parse_args(argv)
        progress.info("S1", "対象ディレクトリ: {0}".format(target))

        cfg = config.load(os.path.join(root, "settings.properties"), progress)
        _current_max_memory[0] = cfg.max_memory
        _current_temp_directory[0] = cfg.temp_directory
        con = bootstrap.open_connection(
            duckdb_module, cfg.max_memory, cfg.temp_directory, progress
        )
        schema.create_all(con)
        # ※CR-008 タイムゾーン名の検証。pg_timezone_names() を引くため、
        # 設定を読み込む S2 ではなく、接続を開いたここで行う。
        config.validate_timezones(con, cfg, progress)

        exit_code = run_pipeline(
            con=con,
            duckdb_module=duckdb_module,
            cfg=cfg,
            target=target,
            progress=progress,
            now=now,
            vendor_note=vendor_note,
            out_stream=stream_out if stream_out is not None else sys.stdout,
        )
        return exit_code
    except AppError as exc:
        progress.error("--", exc.user_message())
        exit_code = exc.exit_code
        return exit_code
    except _duckdb_errors() as exc:
        storage = StorageError(
            "データストアの処理に失敗しました",
            "  理由: {0}\n"
            "  現在の設定: [duckdb] max_memory = {1}, temp_directory = {2}\n"
            "  対処: settings.properties の max_memory を小さくするか、"
            "temp_directory に空き容量のあるパスを指定してください".format(
                exc, _current_max_memory[0], _current_temp_directory[0]
            ),
        )
        progress.error("--", storage.user_message())
        exit_code = storage.exit_code
        return exit_code
    except Exception:
        traceback.print_exc(file=err)
        exit_code = UNEXPECTED_EXIT_CODE
        return exit_code
    finally:
        elapsed = time.monotonic() - started
        progress.info(
            "--",
            "終了しました (総所要時間 {0:.1f}s, 終了コード {1})".format(elapsed, exit_code),
        )


#: 対象とするファイル名パターン (終了コード 2 のメッセージで使う)
FILE_PATTERNS = [
    "DBConnection_{yyyymmdd}.csv",
    "{コンテナ名}_gc_{ホスト名}_{yyyymmdd}.txt",
    "bqueues_{ホスト名}_{yyyymmdd}.txt",
]


def ingest(con, target, progress, cfg=None):
    """S3・S4 — ファイル探索と解析。取り込み結果の要約を返す。"""
    logfiles = discovery.discover(target, progress)
    if not logfiles:
        raise NoInputFileError(
            "対象となるログファイルが見つかりません",
            "  探索したパス: {0}\n  対象とするファイル名:\n{1}".format(
                target, "\n".join("    " + p for p in FILE_PATTERNS)
            ),
        )

    loader_by_kind = {
        discovery.KIND_DBCONN: dbconn.load,
        discovery.KIND_JVMGC: jstat.load,
        discovery.KIND_LSF: bqueues.load,
    }
    total = len(logfiles)
    ok_rows = 0
    failures = []
    load_errors = []

    for index, logfile in enumerate(logfiles, start=1):
        loader = loader_by_kind[logfile.kind]
        result = loader(con, logfile, progress, cfg)   # ※CR-008 タイムゾーン
        ok_rows += result.ok_rows
        rel = loaders.relative_path(logfile.path, target)
        progress.progress_count(
            "S4", index, total,
            "{0} ({1}行, スキップ{2}件)".format(rel, result.ok_rows, result.skipped_rows),
        )
        if result.errors:
            detail = ", ".join(
                "{0} {1}件".format(reason, count)
                for reason, count in sorted(result.errors.items())
            )
            progress.warning("S4", "解析できない行があります: {0} — {1}".format(rel, detail))
            loaders.record_errors(con, rel, result.errors)
            for reason, count in sorted(result.errors.items()):
                load_errors.append((rel, reason, count))
        if result.ok_rows == 0:
            failures.append((rel, result.errors))

    dropped = loaders.dedupe_all(con)
    dropped_total = sum(dropped.values())
    if dropped_total:
        progress.info(
            "S4",
            "同一時刻の重複レコードを {0} 件破棄しました ({1})".format(
                dropped_total,
                ", ".join("{0}: {1}".format(k, v) for k, v in sorted(dropped.items())),
            ),
        )

    remaining = 0
    for table in ("db_connection", "jvm_gc", "lsf_queue"):
        remaining += con.execute("SELECT count(*) FROM {0}".format(table)).fetchone()[0]

    if remaining == 0:
        lines = []
        for rel, errors in failures:
            if errors:
                reason = ", ".join(
                    "{0} {1}件".format(r, c) for r, c in sorted(errors.items())
                )
            else:
                reason = "有効な行がありません"
            lines.append("  {0}: {1}".format(rel, reason))
        raise AllParseFailedError(
            "すべてのファイルの解析に失敗しました (有効なレコードが 0 件)",
            "\n".join(lines) if lines else "  (詳細なし)",
        )

    progress.info("S4", "総レコード数 {0} 件".format(remaining))
    return {
        "file_count": total,
        "record_count": remaining,
        "load_errors": load_errors,
    }


def run_pipeline(con, duckdb_module, cfg, target, progress, now, vendor_note,
                 out_stream=None):
    """S3 以降のパイプライン。U005 以降のスプリントで段階的に埋める。"""
    summary = ingest(con, target, progress, cfg)

    metrics.build_jvm_gc_derived(con)
    metrics.build_metrics(con)
    metric_summary = metrics.summarize(con, progress)
    series = metrics.list_series(con)
    progress.info("S5", "検知対象の系列は {0} 本です".format(len(series)))
    summary.update(metric_summary)
    summary["series"] = series

    detect_result = detect(con, cfg, series, progress)
    summary.update(detect_result)

    # 実行順序は S7 -> S7'(ID 採番) -> S8 -> S8' (ADR-010 の改訂 2026-09-05)。
    # causes は co_anomaly の結果を条件に使うため、順序を逆にすると
    # 同時アノマリーを参照する 5 ルールが無言で発火しなくなる。
    # ID の採番は merge_detections の内部で行われる (DS-09-07)。同時アノマリー欄が
    # **相手のイベント ID を出力する**ため、突き合わせより前に確定している必要がある。
    event_list = events.merge_detections(detect_result["detections"], cfg)
    events.assign_severity(event_list, con, cfg)
    counts = {}
    for e in event_list:
        counts[e.severity] = counts.get(e.severity, 0) + 1
    progress.info(
        "S7",
        "検知点 {0} 点を {1} 件のイベントに統合しました "
        "(SEVERE: {2}, FATAL: {3}, WARN: {4}, INFO: {5})".format(
            len(detect_result["detections"]), len(event_list),
            counts.get("SEVERE", 0), counts.get("FATAL", 0),
            counts.get("WARN", 0), counts.get("INFO", 0),
        ),
    )

    co_anomaly.collect(event_list)
    point_events = sum(1 for e in event_list if e.co_anomalies is not None)
    linked = sum(1 for e in event_list if e.co_anomalies)
    progress.info(
        "S8",
        "同時に発生したアノマリーを突き合わせました "
        "(観点1 のイベント {0}/{1} 件、うち同時発生あり {2} 件)".format(
            point_events, len(event_list), linked),
    )

    causes.assign_causes(event_list, cfg)
    with_causes = sum(1 for e in event_list if e.causes)
    progress.info("S8", "原因候補を付与しました ({0}/{1} 件に候補あり)".format(
        with_causes, len(event_list)))

    ordered = events.sort_for_report(event_list)

    # S8'' チャートの生成 (※CR-009)。**レポートより前に行う。**
    # レポートは `event.chart_path` を見て画像リンクを出すため。
    out_dir = os.getcwd()
    make_charts(con, cfg, ordered, out_dir, progress)

    ctx = reporter.ReportContext(
        now=now if now is not None else datetime.now(),
        target_dir=target,
        period=summary.get("period"),
        file_count=summary.get("file_count", 0),
        record_count=summary.get("record_count", 0),
        series_count=len(series),
        events=ordered,
        algorithms=_algorithm_rows(cfg),
        load_errors=summary.get("load_errors", []),
        skipped=_skip_rows(summary.get("skips", [])),
        failures=summary.get("failures", []),
        vendor_note=vendor_note,
    )
    # ※CR-001・CR-004: 単一の report.md ではなくレポート群を出力する。
    try:
        # ※CR-008 レポートに併記するタイムゾーン。格納時のものをそのまま示す
        # (表示のための再変換は行わない)。
        reporter.set_timezone(cfg.storage_timezone)
        written = reporter.write_reports(ctx, out_dir)
    except Exception:
        # 長時間の解析結果を書き込み失敗だけで失わない (UI-05-01)。
        # **既に書き終えたファイルは残す** (UI-04-04)。標準出力へ出すのは、
        # 生成できた分の全文である。
        stream = out_stream if out_stream is not None else sys.stdout
        try:
            stream.write(reporter.render_all_for_fallback(ctx))
            stream.flush()
        except Exception:
            pass
        raise
    total = 0
    for path in written:
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        total += size
        progress.info("S9", "レポートを出力しました: {0} ({1:,} バイト)".format(
            path, size))
    progress.info("S9", "レポートを {0} ファイル出力しました (合計 {1:,} バイト)".format(
        len(written), total))
    return 0


def make_charts(con, cfg, event_list, out_dir, progress):
    """S8'' — アノマリーの状況を SVG チャートにする (※CR-009)。

    **全イベントには付けない。** 30 日規模でイベントは 7,229 件あり、
    上限が無いと出力が肥大する。絞りは `[chart]` の設定で行う。

    **チャートが無いイベントには「チャートなし」の画像を出す**
    (※CR-009。2026-09-06 の指示)。図が無いと「ツールの不備」に見えるためである。
    **画像は 1 つだけ作り、全イベントが同じものを参照する。**

    **1 件の失敗で全体を止めない。** チャートは補助的な情報であり、
    描けなかったことを理由にレポートを失うほうが損失が大きい (FR-032 と同じ考え)。
    """
    if not cfg.chart_enabled:
        # **無効なら画像を一切出さない。** 誰も図を期待しないため、
        # `[NO CHART]` を並べる必要がない。
        # **明示的に None を渡す**(同一プロセスで続けて実行する場合に、
        # 前回の設定が残らないようにするため)。
        reporter.set_chart_placeholder(None)
        progress.info("S8", "チャートの出力は無効です")
        return 0

    targets = charts.select_events(event_list, cfg)
    if not targets:
        # 対象が 1 件も無くても、**有効である以上は画像を出す。**
        reporter.set_chart_placeholder(charts.write_no_chart(out_dir))
        progress.info("S8", "チャートの対象となるイベントはありません")
        return 0

    by_id = dict((e.event_id, e) for e in event_list)
    written = 0
    tabled = 0
    failures = []
    for event in targets:
        try:
            svg, rows = charts.build_for_event(
                con, event, cfg, by_id, timezone=cfg.storage_timezone)
            if svg is None:
                # 点数が足りず線にならない。**生の点列を表で出す**
                # (※CR-009。2026-09-06 の指示)。
                event.chart_points = rows
                tabled += 1
                continue
            event.chart_path = charts.write_svg(out_dir, event.event_id, svg)
            written += 1
        except Exception as exc:
            failures.append((event.event_id, str(exc)))

    # **「チャートなし」の画像を 1 つだけ作る**(※CR-009。2026-09-06 の指示)。
    # 図が無いと「ツールの不備」に見えるため、チャートが無いイベントにも
    # 画像を出す。**全イベントが同じファイルを参照する。**
    placeholder = charts.write_no_chart(out_dir)
    reporter.set_chart_placeholder(placeholder)

    if failures:
        failures.sort()
        progress.warning(
            "S8", "チャートを作れなかったイベントが {0} 件あります (例 {1}: {2})".format(
                len(failures), failures[0][0], failures[0][1]))
    progress.info(
        "S8", "チャートを {0} 件出力しました ({1}/ 配下、点数不足で表にしたもの {2} 件)".format(
            written, charts.CHART_DIR, tabled))
    return written


def _algorithm_rows(cfg):
    """report.md 2 章の行 (10 個すべて。無効なものも載せる)。"""
    enabled = set(cfg.enabled_algorithms())
    rows = []
    for alg_id in sorted(detectors.REGISTRY):
        det = detectors.REGISTRY[alg_id]
        params = cfg.algorithm_params(alg_id)
        params_text = ", ".join(
            "{0}={1}".format(k, v) for k, v in sorted(params.items())
        )
        rows.append((alg_id, det.name, det.aspect, alg_id in enabled, params_text))
    return rows


def _skip_rows(skips):
    """(アルゴリズム, 理由, 件数) に集約する。"""
    counts = {}
    for skip in skips:
        key = (skip.algorithm, skip.reason)
        counts[key] = counts.get(key, 0) + 1
    return [(alg, reason, count) for (alg, reason), count in sorted(counts.items())]


def detect(con, cfg, series, progress):
    """S6 — 有効な検知器を全系列に適用する。

    ※CR-006・CR-007 により、ループの向きを **検知器ごと → 系列ごと** に変えた。

    **なぜ向きを変えたか (実測にもとづく)**

    1. 従来は検知器の数だけ同じ系列を読み直しており、**S6 の 52% が
       `fetch_series`** であった (30 系列 / 11 検知器で 330 回のうち 300 回が
       同内容の読み直し)。系列ごとにまとめれば 1 回で済む。
    2. 系列ごとにまとめると、**系列を単位としてスレッドに割り当てられる**。
       各スレッドが `con.cursor()` を持てば DuckDB が並列に処理する
       (実測: 8 スレッドで 5.68 倍。共有接続のままだと 1.36 倍で頭打ち)。

    **結果の順序は実行順に依存しない。** 末尾で `all_detections` を
    (series_id, metric, algorithm, start_ts) で整列し、`failures` も整列する
    (NFR-009)。
    """
    active = detectors.enabled_detectors(cfg)
    if not active:
        progress.warning("S6", "有効なアルゴリズムがありません。検知は行いません")
        return {"detections": [], "skips": [], "failures": []}

    total = len(series)
    workers = _detect_workers(cfg, total)
    progress.info(
        "S6",
        "{0} アルゴリズムを {1} 系列に適用します (並列度 {2})".format(
            len(active), total, workers
        ),
    )

    lock = threading.Lock()
    all_detections = []
    all_skips = []
    failures = []
    per_alg = {d.id: [0, 0, 0.0] for d in active}   # 検知数, スキップ数, 所要秒
    done = [0]
    started = time.monotonic()

    def handle(meta, cursor):
        """1 系列に全検知器を適用する。**戻り値はこのスレッド内に閉じる。**"""
        local_d, local_s, local_f = [], [], []
        local_stat = {}
        detectors_base.begin_series(meta)
        try:
            for detector in active:
                t0 = time.monotonic()
                try:
                    d, sk = detector.run(cursor, cfg, meta, progress)
                except Exception as exc:
                    # 1 つの検知器が失敗しても他を止めない (FR-032)
                    local_f.append(
                        (detector.id, meta.series_id, meta.metric, str(exc)))
                    local_stat.setdefault(detector.id, [0, 0, 0.0])[2] +=                         time.monotonic() - t0
                    continue
                local_d.extend(d)
                local_s.extend(sk)
                st = local_stat.setdefault(detector.id, [0, 0, 0.0])
                st[0] += len(d)
                st[1] += len(sk)
                st[2] += time.monotonic() - t0
        finally:
            detectors_base.end_series()
        return local_d, local_s, local_f, local_stat

    def merge(result):
        local_d, local_s, local_f, local_stat = result
        with lock:
            all_detections.extend(local_d)
            all_skips.extend(local_s)
            failures.extend(local_f)
            for alg_id, st in local_stat.items():
                agg = per_alg[alg_id]
                agg[0] += st[0]
                agg[1] += st[1]
                agg[2] += st[2]
            done[0] += 1
            if done[0] % 20 == 0 or done[0] == total:
                progress.info("S6", "{0}/{1} 系列".format(done[0], total))

    if workers <= 1:
        for meta in series:
            merge(handle(meta, con))
    else:
        # **各スレッドが自分のカーソルを持つ** (※CR-007)。共有接続のままだと
        # DuckDB 側で直列化され、並列化の効果がほとんど出ない。
        local = threading.local()

        def task(meta):
            cursor = getattr(local, "cursor", None)
            if cursor is None:
                cursor = con.cursor()
                local.cursor = cursor
            return handle(meta, cursor)

        with futures.ThreadPoolExecutor(max_workers=workers) as pool:
            for result in pool.map(task, series):
                merge(result)

    for detector in active:
        found, skipped, spent = per_alg[detector.id]
        progress.info(
            "S6",
            "{0} 完了: 検知 {1} 点, スキップ {2} 系列, 所要 {3:.1f}s(全スレッド合計)".format(
                detector.id, found, skipped, spent
            ),
        )
    progress.info("S6", "検知の総所要 {0:.1f}s".format(time.monotonic() - started))

    # **実行順に依存しない順序にする** (NFR-009)
    all_detections.sort(
        key=lambda d: (d.series_id, d.metric, d.algorithm, d.start_ts)
    )
    failures.sort()
    progress.info("S6", "検知点は合計 {0} 点です".format(len(all_detections)))
    return {
        "detections": all_detections,
        "skips": all_skips,
        "failures": failures,
    }


def _detect_workers(cfg, total) -> int:
    """S6 のスレッド数 (※CR-007)。

    実測では 8 が最適で、16 にしても伸びない (5.68x -> 5.78x)。
    系列数が少ないときはそれ以上のスレッドを起こしても意味がない。
    """
    configured = int(cfg.param("common.detect_threads"))
    if configured <= 0:
        configured = min(8, os.cpu_count() or 1)
    return max(1, min(configured, total))
