"""M03 discovery — ファイル探索と種別判定 (P003 4章)。

4 パターンのファイル名を識別する。中身は読まない (T2 以降の担当)。
※CR-010 で sar (`sa-{host}-{yyyymmdd}.csv`) を追加した。
"""

import os
import re
from typing import List, Optional

STEP = "S3"

KIND_DBCONN = "db_connection"
KIND_JVMGC = "jvm_gc"
KIND_LSF = "lsf_queue"
KIND_SAR = "sar"          # ※CR-010

_DBCONN_RE = re.compile(r"^DBConnection_(\d{8})\.csv$")
_LSF_RE = re.compile(r"^bqueues_(.+)_(\d{8})\.txt$")
#: ※CR-010 `(.+)` は貪欲一致。末尾の 8 桁数字が分割位置を一意に決めるため、
#: **ホスト名に `-` を含んでいても正しく分かれる** (DS-03-02a)。
_SAR_RE = re.compile(r"^sa-(.+)-(\d{8})\.csv$")
_DATE_RE = re.compile(r"^\d{8}$")


class LogFile(object):
    """探索で見つかった 1 ファイルとそのメタ情報。"""

    def __init__(self, path, kind, container=None, host=None, date=None):
        self.path = path
        self.kind = kind
        self.container = container
        self.host = host
        self.date = date

    def __repr__(self):
        return "LogFile({0!r}, {1!r}, container={2!r}, host={3!r}, date={4!r})".format(
            self.path, self.kind, self.container, self.host, self.date
        )


def classify(name: str) -> Optional[dict]:
    """ファイル名から種別とメタ情報を判定する。該当しなければ None。

    判定の順序に注意する。①③ を先に照合し、最後に ② を試す (DS-03-01/02)。
    """
    m = _DBCONN_RE.match(name)
    if m:
        return {"kind": KIND_DBCONN, "date": m.group(1)}

    m = _LSF_RE.match(name)
    if m:
        return {"kind": KIND_LSF, "host": m.group(1), "date": m.group(2)}

    # ④ sa-{ホスト名}-{yyyymmdd}.csv  ※CR-010
    m = _SAR_RE.match(name)
    if m:
        return {"kind": KIND_SAR, "host": m.group(1), "date": m.group(2)}

    # ② {コンテナ名}_gc_{ホスト名}_{yyyymmdd}.txt
    if not name.endswith(".txt"):
        return None
    stem = name[:-4]
    if "_" not in stem:
        return None
    head, _, date = stem.rpartition("_")
    if not _DATE_RE.match(date):
        return None
    if "_gc_" not in head:
        return None
    container, _, host = head.rpartition("_gc_")
    if not container or not host:
        return None
    return {"kind": KIND_JVMGC, "container": container, "host": host, "date": date}


def discover(root: str, progress) -> List[LogFile]:
    """root 配下を再帰走査して対象ファイルを返す。

    戻り値は (date, path) の昇順で安定ソートされる (DS-03-04、NFR-009)。
    """
    found = []
    skipped = 0
    total = 0
    visited = set()

    for dirpath, dirnames, filenames in os.walk(str(root), followlinks=True):
        real = os.path.realpath(dirpath)
        if real in visited:
            # シンボリックリンクの循環を検出したら降りない (UI-01-V04)
            dirnames[:] = []
            continue
        visited.add(real)
        # 走査順を決定的にする
        dirnames.sort()
        for name in sorted(filenames):
            total += 1
            meta = classify(name)
            if meta is None:
                skipped += 1
                continue
            found.append(
                LogFile(
                    path=os.path.join(dirpath, name),
                    kind=meta["kind"],
                    container=meta.get("container"),
                    host=meta.get("host"),
                    date=meta.get("date"),
                )
            )

    found.sort(key=lambda f: (f.date or "", f.path))

    by_kind = {KIND_DBCONN: 0, KIND_JVMGC: 0, KIND_LSF: 0, KIND_SAR: 0}
    for f in found:
        by_kind[f.kind] += 1
    progress.info(
        STEP,
        "探索したファイル総数 {0} 件 / 対象 {1} 件 "
        "(DBコネクション {2}, jstat {3}, LSFキュー {4}, sar {5}) / 対象外 {6} 件".format(
            total, len(found), by_kind[KIND_DBCONN], by_kind[KIND_JVMGC],
            by_kind[KIND_LSF], by_kind[KIND_SAR], skipped,
        ),
    )
    return found
