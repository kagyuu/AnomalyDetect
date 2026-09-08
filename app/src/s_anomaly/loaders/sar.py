"""M16 loaders.sar — sar (sysstat) の `sadf -d` 出力の解析 (P003 5.4a)。※CR-010。

**1 ファイルの中に、活動種別ごとのブロックが並ぶ。**

```
# hostname;interval;timestamp;CPU;%user;%nice;%system;%iowait;%steal;%idle
host01;600;2026-06-01 00:10:00 UTC;-1;1.23;0.00;0.45;0.02;0.00;98.30
# hostname;interval;timestamp;DEV;tps;rd_sec/s;wr_sec/s;...;%util
host01;600;2026-06-01 00:10:00 UTC;sda;12.30;...;0.75
```

**設計上の要点は 3 つある。**

1. **列は位置ではなく列名で対応付ける** (DS-SAR-03)。`sadf` の出力書式は
   sysstat の版で変わる (`rd_sec/s` → `rkB/s` など)。位置で読むと、版が
   変わった瞬間に別のメトリクスを取り込む。
2. **未知の活動種別・未知の列は読み飛ばす** (DS-SAR-01)。実行を止めない。
3. **ホスト名はファイル名から取る** (DS-SAR-06)。データ行の第 1 列は使わない。
   変換スクリプトが「他のログに名前を揃えるために」ホスト名を上書きできる
   仕様であり (P001 FR-145)、**食い違うことが正常な使い方**である。

**★接頭 `pct_` と接尾 `_pct` は別物である。★** 接尾 `*_pct` (`ou_pct` など) は
本アプリが導出した使用率で検知対象外 (ADR-005)、接頭 `pct_*` は sar が直接出す
比率で**一次の検知対象**である (P001 FR-143、`docs/ArchitectureHandbook.md` 5.2a)。
"""

import re
from datetime import datetime

from . import (
    REASON_COLUMNS, REASON_DATE, REASON_HEADER, REASON_NUMBER,
    BatchInserter, LoadResult, open_text, parse_number,
)

TABLE = "sar"

#: デバイス列を持たない活動で `device` に入れる占位子 (P001 FR-144)。
NO_DEVICE = "-"

#: 見出しの固定 3 列。第 4 列以降が活動種別ごとの内容である。
FIXED_HEADER = ("hostname", "interval", "timestamp")

#: 時刻。第 2 群はタイムゾーン表記 (版によっては付かない)。
_TS_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})(?:\s+(\S+))?$")

#: 活動種別の判別規則 (DS-SAR-02)。**(活動, デバイス列名, 特徴列)**。
#:
#: 判別に使う列は、sysstat の版をまたいで改称の実績が無いものを選んでいる。
#: `disk` と `network` はデバイス列名で分ける (どちらも比率の列を持つため)。
ACTIVITY_RULES = (
    ("cpu",        "CPU",   "pct_user"),
    ("memory",     None,    "kbmemfree"),
    ("swap_space", None,    "kbswpfree"),
    ("io",         None,    "rtps"),
    ("load",       None,    "runq_sz"),
    ("swapping",   None,    "pswpin_s"),
    ("disk",       "DEV",   "pct_util"),
    ("network",    "IFACE", "rxpck_s"),
    # ※CR-011 NFS。**判別列は「片方にしか現れない」ものを選ぶ** (DS-SAR-02a)。
    # `-n NFS` は read/s・write/s・access/s を、`-n NFSD` は sread/s・swrite/s・
    # saccess/s を持つ。**接頭の `s` があるかどうかだけの違い**であり、
    # 部分一致で判別すると取り違える。正規化した名前の**完全一致**で判別すること。
    ("nfs",        None,    "retrans_s"),
    ("nfsd",       None,    "scall_s"),
)

#: デバイス列に使われる名前。デバイスを持たない活動の判別で除外に使う。
DEVICE_COLUMN_NAMES = ("CPU", "DEV", "IFACE")

#: 取り込むメトリクス (P001 FR-142)。**見出しに無い列は静かに飛ばす。**
WANTED = {
    "cpu":        ("pct_user", "pct_system", "pct_iowait", "pct_idle"),
    "memory":     ("pct_memused", "pct_commit", "kbmemfree"),
    "swap_space": ("pct_swpused",),
    "io":         ("tps", "bread_s", "bwrtn_s"),
    "load":       ("runq_sz", "ldavg_1", "ldavg_5", "blocked"),
    "swapping":   ("pswpin_s", "pswpout_s"),
    "disk":       ("pct_util", "await", "tps"),
    "network":    ("pct_ifutil", "rxkb_s", "txkb_s"),
    # ※CR-011 NFS クライアント。`retrans_s` は**不調が最初に現れる指標**である。
    "nfs":        ("call_s", "retrans_s", "read_s", "write_s",
                   "access_s", "getatt_s"),
    # ※CR-011 NFS サーバ。`packet_s` / `udp_s` / `tcp_s` は取り込まない
    # (`scall_s` と重複した情報であり、不調の指標にならない。P001 FR-142b)。
    "nfsd":       ("scall_s", "badcall_s", "hit_s", "miss_s",
                   "sread_s", "swrite_s", "saccess_s", "sgetatt_s"),
}

#: 全活動を通じた取り込み対象の集合。
#: **`metrics.DETECTABLE_METRICS` と一致していること**を単体テストで担保する。
ALL_METRICS = tuple(sorted({m for names in WANTED.values() for m in names}))


def normalize(col: str) -> str:
    """sar の列名をメトリクス名へ正規化する (P001 FR-143 / DS-SAR-04)。

    `%user` -> `pct_user` / `rd_sec/s` -> `rd_sec_s` / `ldavg-1` -> `ldavg_1`

    **接頭の `pct_` は「sar が直接出す比率」を意味する。** 本アプリが導出した
    使用率を表す接尾の `_pct` (`ou_pct`) とは別物である。
    """
    col = str(col).strip()
    if col.startswith("%"):
        col = "pct_" + col[1:]
    return col.replace("/", "_").replace("-", "_").lower()


def classify_header(columns):
    """見出しの列名リストから (活動, デバイス列名, {メトリクス名: 位置}) を返す。

    判別できなければ `(None, None, None)`。位置は**行全体での添字**である。
    """
    if len(columns) <= len(FIXED_HEADER):
        return None, None, None
    body = columns[len(FIXED_HEADER):]
    normalized = [normalize(c) for c in body]
    first_raw = str(body[0]).strip()
    for activity, device_col, feature in ACTIVITY_RULES:
        if feature not in normalized:
            continue
        if device_col is not None and first_raw != device_col:
            continue
        if device_col is None and first_raw in DEVICE_COLUMN_NAMES:
            continue
        offset = len(FIXED_HEADER)
        index = {}
        for i, name in enumerate(normalized):
            index.setdefault(name, offset + i)
        return activity, device_col, index
    return None, None, None


def parse_timestamp(token):
    """`2026-06-01 00:10:00 UTC` を (datetime, タイムゾーン表記 or None) にする。

    解釈できなければ `(None, None)`。**表記が無いのは正常**である
    (sysstat の版によっては付かない。DS-SAR-05)。
    """
    m = _TS_RE.match(str(token).strip())
    if m is None:
        return None, None
    try:
        ts = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                      int(m.group(4)), int(m.group(5)), int(m.group(6)))
    except ValueError:
        return None, None
    return ts, m.group(7)


def load(con, logfile, progress, cfg=None) -> LoadResult:
    """`sa-{host}-{yyyymmdd}.csv` を読む。"""
    result = LoadResult()
    text = open_text(logfile.path)
    if text is None:
        result.add_error(REASON_HEADER)
        progress.warning(
            "S4",
            "UTF-8 でも CP932 でもデコードできません: {0}".format(logfile.path),
        )
        return result

    wanted_activities = cfg.sar_activities if cfg is not None else None
    expected_tz = cfg.source_timezone(TABLE) if cfg is not None else None
    host = logfile.host or ""

    inserter = BatchInserter(con, TABLE, cfg=cfg)
    activity = device_col = index = None
    skip_block = False
    warned_headers = set()
    tz_warned = False
    saw_header = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("#"):
            saw_header = True
            columns = line[1:].split(";")
            activity, device_col, index = classify_header(columns)
            if activity is None:
                # 未知の活動種別。**ブロックごと読み飛ばす** (DS-SAR-01)。
                # **同じ見出しにつき 1 回だけ警告する** (1 日分で 288 回現れる)。
                skip_block = True
                if line not in warned_headers:
                    warned_headers.add(line)
                    progress.warning(
                        "S4",
                        "sar の未知の活動種別を読み飛ばしました: {0} ({1})".format(
                            line[:120], logfile.path),
                    )
                continue
            # ※CR-010 設定で絞られている活動は、見出しの時点で捨てる
            # (データ行を解析してから捨てない。DS-SAR-07)。
            skip_block = (wanted_activities is not None
                          and activity not in wanted_activities)
            continue

        if skip_block or index is None:
            continue

        row = line.split(";")
        if len(row) <= len(FIXED_HEADER):
            result.add_error(REASON_COLUMNS)
            continue

        ts, tz_name = parse_timestamp(row[2])
        if ts is None:
            result.add_error(REASON_DATE)
            continue

        # ※CR-010 タイムゾーン表記の検証 (P001 FR-147 / DS-SAR-05)。
        # **設定値を正とし、表記では変換しない。** 表記が無ければ何もしない。
        if (not tz_warned and tz_name and expected_tz
                and tz_name != expected_tz):
            tz_warned = True
            progress.warning(
                "S4",
                "sar の時刻のタイムゾーン表記 '{0}' が設定 [timezone] sar = '{1}' "
                "と異なります。設定値を正として扱います: {2}".format(
                    tz_name, expected_tz, logfile.path),
            )

        if device_col is None:
            device = NO_DEVICE
        else:
            device = row[len(FIXED_HEADER)].strip() or NO_DEVICE

        emitted = 0
        bad_number = False
        for name in WANTED[activity]:
            position = index.get(name)
            if position is None or position >= len(row):
                # 見出しに無い列 (版差)。**警告は出さない** (DS-SAR-03)。
                continue
            ok, value = parse_number(row[position])
            if not ok:
                bad_number = True
                break
            inserter.add({
                "ts": ts, "host": host, "activity": activity,
                "device": device, "metric": name, "value": value,
            })
            emitted += 1
        if bad_number:
            result.add_error(REASON_NUMBER)
            continue
        if emitted:
            result.ok_rows += 1

    inserter.flush()
    if not saw_header:
        # `#` 見出しが 1 つも無い = 列構成を判別できない (DS-LD-05)
        result.add_error(REASON_HEADER)
        progress.warning(
            "S4",
            "sar の見出し行 (# で始まる行) がありません: {0}".format(logfile.path),
        )
    return result
