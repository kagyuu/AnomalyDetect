"""M13 progress — 進捗ログ (P002 3章 / P003 13章)。

標準出力に INFO、標準エラーに WARNING 以上を出す (UI-03-01)。
書式は `YYYY-MM-DD HH:MM:SS [LEVEL] [ステップID] メッセージ` (UI-03-02)。
"""

import logging
import sys

LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(step)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
DEFAULT_STEP = "--"

_LOGGER_NAME = "s_anomaly.progress"


class _StepFilter(logging.Filter):
    """step が未指定のレコードに既定値 '--' を注入する。"""

    def filter(self, record):
        if not hasattr(record, "step"):
            record.step = DEFAULT_STEP
        return True


class _InfoOnlyFilter(logging.Filter):
    """INFO だけを通す (WARNING 以上を標準出力へ出さない)。"""

    def filter(self, record):
        return record.levelno == logging.INFO


class Progress(object):
    """進捗ログの出力窓口。"""

    def __init__(self, logger):
        self._logger = logger

    def info(self, step, message):
        self._logger.info(message, extra={"step": step})

    def warning(self, step, message):
        self._logger.warning(message, extra={"step": step})

    def error(self, step, message):
        self._logger.error(message, extra={"step": step})

    def progress_count(self, step, done, total, message):
        """`[3/10] メッセージ` の形で INFO を出す。"""
        self.info(step, "[{0}/{1}] {2}".format(done, total, message))


def setup(stream_out=None, stream_err=None):
    """Progress を作って返す。

    stream_out / stream_err を差し替えられるようにしてあるのはテストのため。
    同じ setup を 2 回呼んでもハンドラが二重に付かない (出力が重複しない)。
    """
    out = stream_out if stream_out is not None else sys.stdout
    err = stream_err if stream_err is not None else sys.stderr

    # リダイレクト時にバッファリングで進捗が見えなくなることを防ぐ (UI-03-03)
    for stream in (out, err):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(line_buffering=True)
            except (AttributeError, ValueError, OSError):
                pass

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    # 二重登録を防ぐため、既存ハンドラを外してから付け直す
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    step_filter = _StepFilter()

    out_handler = logging.StreamHandler(out)
    out_handler.setLevel(logging.INFO)
    out_handler.setFormatter(formatter)
    out_handler.addFilter(step_filter)
    out_handler.addFilter(_InfoOnlyFilter())
    logger.addHandler(out_handler)

    err_handler = logging.StreamHandler(err)
    err_handler.setLevel(logging.WARNING)
    err_handler.setFormatter(formatter)
    err_handler.addFilter(step_filter)
    logger.addHandler(err_handler)

    return Progress(logger)
