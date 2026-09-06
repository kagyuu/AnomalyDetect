"""アプリケーション例外と終了コードの対応 (P003 DS-01-01)。

終了コードの意味は docs/P002-frontend-spec.md 5章 UI-05 を正とする。
各モジュールはこれらの例外を送出し、cli.main() が 1 箇所で終了コードへ変換する。
"""


class AppError(Exception):
    """終了コードを持つアプリケーション例外の基底。"""

    exit_code = 99

    def __init__(self, message, detail=None):
        super(AppError, self).__init__(message)
        self.message = message
        self.detail = detail

    def user_message(self):
        """標準エラーへ出力する文言を返す。"""
        if self.detail:
            return "error: {0}\n{1}".format(self.message, self.detail)
        return "error: {0}".format(self.message)


class ArgumentError(AppError):
    """引数不正 (終了コード 1)。"""

    exit_code = 1

    def user_message(self):
        # usage を含めるため、detail をそのまま前置する
        if self.detail:
            return "{0}\nerror: {1}".format(self.detail, self.message)
        return "error: {0}".format(self.message)


class NoInputFileError(AppError):
    """対象ログファイルが 0 件 (終了コード 2)。"""

    exit_code = 2


class ConfigError(AppError):
    """設定ファイルの内容が不正 (終了コード 3)。"""

    exit_code = 3


class AllParseFailedError(AppError):
    """全ファイルの解析に失敗した (終了コード 4)。"""

    exit_code = 4


class ReportWriteError(AppError):
    """report.md の書き込みに失敗した (終了コード 5)。"""

    exit_code = 5


class StorageError(AppError):
    """メモリ不足・DuckDB の実行時エラー (終了コード 6)。"""

    exit_code = 6


class VendorError(AppError):
    """DuckDB を読み込めなかった (終了コード 7)。"""

    exit_code = 7


#: 終了コード -> 例外クラス。テストの表駆動検証で使う。
EXIT_CODE_MAP = {
    1: ArgumentError,
    2: NoInputFileError,
    3: ConfigError,
    4: AllParseFailedError,
    5: ReportWriteError,
    6: StorageError,
    7: VendorError,
}

#: 想定外の例外に割り当てる終了コード (FR-092)。
UNEXPECTED_EXIT_CODE = 99
