"""M14 bootstrap — vendor/ の解決と DuckDB の初期化 (P003 2章)。

閉域環境では vendor/{os}-{arch}-cp{X}{Y}/ から DuckDB を読み込む (ADR-001)。
開発・CI 環境では環境に導入済みの DuckDB へフォールバックする。
"""

import os
import platform
import sys

from .errors import ConfigError, VendorError

STEP = "--"


def platform_tag():
    """実行環境に対応する vendor サブディレクトリ名を返す (DS-14-01)。

    例: win-amd64-cp314, linux-x86_64-cp310
    """
    if sys.platform == "win32":
        os_tag = "win"
    elif sys.platform.startswith("linux"):
        os_tag = "linux"
    else:
        os_tag = sys.platform
    arch = platform.machine().lower()
    return "{0}-{1}-cp{2}{3}".format(
        os_tag, arch, sys.version_info[0], sys.version_info[1]
    )


def resolve_vendor_dir(app_root):
    """実行環境に対応する vendor ディレクトリを返す。無ければ None。"""
    candidate = os.path.join(str(app_root), "vendor", platform_tag())
    if os.path.isdir(candidate):
        return candidate
    return None


def list_available_vendors(app_root):
    """vendor/ 直下のディレクトリ名をソートして返す。vendor/ 自体が無ければ空。"""
    vendor_root = os.path.join(str(app_root), "vendor")
    if not os.path.isdir(vendor_root):
        return []
    names = []
    for name in os.listdir(vendor_root):
        if os.path.isdir(os.path.join(vendor_root, name)):
            names.append(name)
    names.sort()
    return names


def _vendor_error_detail(app_root):
    """終了コード 7 のメッセージ本体を組み立てる (DS-14-03)。"""
    available = list_available_vendors(app_root)
    if available:
        available_text = ", ".join(available)
    else:
        available_text = "(なし)"
    return (
        "  期待した vendor ディレクトリ: {0}\n"
        "  実行中の環境: OS={1}, アーキテクチャ={2}, Python={3}.{4}.{5}\n"
        "  用意されている vendor: {6}\n"
        "  対処: README.md の「vendor の準備」を参照してください".format(
            os.path.join(str(app_root), "vendor", platform_tag()),
            sys.platform,
            platform.machine(),
            sys.version_info[0],
            sys.version_info[1],
            sys.version_info[2],
            available_text,
        )
    )


def load_duckdb(app_root, progress):
    """DuckDB モジュールを返す。読み込めなければ VendorError (DS-14-02)。

    戻り値は (duckdb_module, vendor_note) のタプル。
    vendor_note は report.md 1 章に載せる注記 (フォールバック経路を通った場合のみ)。
    """
    vendor_dir = resolve_vendor_dir(app_root)
    if vendor_dir is not None:
        sys.path.insert(0, vendor_dir)
        try:
            import duckdb  # noqa: F401  (動的 import)

            progress.info(
                STEP, "vendor から DuckDB を読み込みました: {0}".format(vendor_dir)
            )
            return duckdb, None
        except Exception:
            # vendor からの読み込みに失敗したら sys.path を元に戻して次を試す
            try:
                sys.path.remove(vendor_dir)
            except ValueError:
                pass

    try:
        import duckdb  # noqa: F811

        note = (
            "vendor を使わず環境の DuckDB を使用しています"
            "(バージョン {0})。配布時は vendor/ の同梱が必要です".format(
                getattr(duckdb, "__version__", "unknown")
            )
        )
        progress.info(STEP, note)
        return duckdb, note
    except Exception:
        raise VendorError(
            "DuckDB を読み込めませんでした", _vendor_error_detail(app_root)
        )


def open_connection(duckdb_module, max_memory, temp_directory, progress):
    """インメモリの DuckDB 接続を作って返す (DS-14-04)。"""
    if temp_directory:
        try:
            os.makedirs(temp_directory, exist_ok=True)
        except OSError as exc:
            raise ConfigError(
                "settings.properties の設定値が不正です",
                "  [duckdb] temp_directory = {0}\n  理由: ディレクトリを作成できません ({1})".format(
                    temp_directory, exc
                ),
            )
        if not os.path.isdir(temp_directory):
            raise ConfigError(
                "settings.properties の設定値が不正です",
                "  [duckdb] temp_directory = {0}\n  理由: ディレクトリではありません".format(
                    temp_directory
                ),
            )

    con = duckdb_module.connect(database=":memory:")

    # メモリ上限。DuckDB のバージョンにより設定名が異なるためフォールバックする
    applied = False
    for key in ("memory_limit", "max_memory"):
        try:
            con.execute("SET {0} = '{1}'".format(key, max_memory))
            applied = True
            break
        except Exception:
            continue
    if not applied:
        progress.warning(
            STEP,
            "メモリ上限を設定できませんでした (memory_limit / max_memory のいずれも受理されず)。既定値で続行します",
        )

    _try_set(con, "SET temp_directory = '{0}'".format(_sql_escape(temp_directory)), progress)
    _try_set(con, "SET preserve_insertion_order = false", progress)
    # 閉域環境で拡張の自動ダウンロードが走ると長時間のタイムアウト待ちになる (DS-14-05)
    _try_set(con, "SET autoinstall_known_extensions = false", progress, quiet=True)
    _try_set(con, "SET autoload_known_extensions = false", progress, quiet=True)
    return con


def _sql_escape(text):
    return str(text).replace("'", "''")


def _try_set(con, sql, progress, quiet=False):
    try:
        con.execute(sql)
        return True
    except Exception as exc:
        if not quiet:
            progress.warning(STEP, "設定を適用できませんでした: {0} ({1})".format(sql, exc))
        return False
