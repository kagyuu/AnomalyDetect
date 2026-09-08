#!/usr/bin/env python3
"""テストデータ生成ツール (P001 FR-110〜FR-112 / P002 8章)。

既知の位置・既知の大きさで異常を埋め込み、正解を expected.json に出力する。
乱数シードを固定し、同じ引数で常にバイト単位で同一のファイル群を生成する。

usage: gen_testdata.py <出力先dir> [--broken]
"""

import json
import math
import os
import random
import sys
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# 生成の定数 (実行日に依存させない。P006 TP-19)
# ---------------------------------------------------------------------------
SEED = 20260601
PERIOD_FROM = datetime(2026, 6, 1, 0, 0, 0)
PERIOD_TO = datetime(2026, 6, 14, 23, 55, 0)
INTERVAL_MINUTES = 5
DAYS = 14
POINTS_PER_DAY = 24 * 60 // INTERVAL_MINUTES  # 288

DB_HOSTS = ["host01", "host02"]
DB_PORT = 7003
DB_SOURCES = ["OraclePool_1", "OraclePool_2", "OraclePool_3"]

# ② は 2 組のみ (全組み合わせではない。P002 8.2)
JVM_PAIRS = [("app01", "host01", "gcutil"), ("app02", "host02", "gc")]

LSF_HOST = "lsfhost01"
LSF_QUEUES = ["long", "normal", "short"]

# ④ sar (※CR-010)。**既存 3 種別と同じホスト名・同じ時刻軸で出す** (P001 FR-150)。
# 揃えないと「同時に発生したアノマリー」が成立せず、sar 対応の主目的
# (アプリ層の異常と OS 資源の異常の突き合わせ) をテストで確認できない。
SAR_HOSTS = ["host01", "host02", LSF_HOST]
SAR_DEVICES = ["sda", "sdb"]
SAR_IFACES = ["eth0", "eth1"]

# ※CR-011 NFS サーバ役のホスト。**`nfsd` はこのホストだけに出す。**
# 実環境では sadc が採取していれば全ホストで nfsd のブロックが出る
# (サーバでなければ全て 0) が、**全て 0 の系列を増やしても情報が無い**。
# その経路は単体テストで確認する (P001 FR-142c)。
SAR_NFS_SERVER = "host02"

# ※CR-011 取り込まれるメトリクスの数 (A006 の規模計算が参照する)。
# `loaders.sar.WANTED` と一致させる。**生成ツールはアプリを import しない**
# ため (配布資産の独立性)、ここに数え上げを置く。
SAR_WANTED_NFS = ("call_s", "retrans_s", "read_s", "write_s",
                  "access_s", "getatt_s")
SAR_WANTED_NFSD = ("scall_s", "badcall_s", "hit_s", "miss_s",
                   "sread_s", "swrite_s", "saccess_s", "sgetatt_s")

# -gc 形式の容量 (固定値)
GC_CAPACITY = {
    "s0c": 1024.0, "s1c": 1024.0, "ec": 8192.0, "oc": 20480.0,
    "mc": 4096.0, "ccsc": 512.0,
}


def timestamps():
    out = []
    t = PERIOD_FROM
    while t <= PERIOD_TO:
        out.append(t)
        t += timedelta(minutes=INTERVAL_MINUTES)
    return out


TIMESTAMPS = timestamps()
TOTAL_POINTS = len(TIMESTAMPS)


def _weekday_factor(ts):
    """土日は水準を下げる。"""
    return 0.55 if ts.weekday() >= 5 else 1.0


def _daily_wave(ts):
    """日内の周期 (0.0〜1.0)。"""
    minutes = ts.hour * 60 + ts.minute
    return 0.5 * (1.0 + math.sin(2.0 * math.pi * (minutes / 1440.0) - math.pi / 2.0))


def baseline_series(rng, base, amplitude, sigma, floor=0.0):
    """周期性を持つ平常系の系列を作る。"""
    out = []
    for ts in TIMESTAMPS:
        value = base + amplitude * _daily_wave(ts) * _weekday_factor(ts)
        value += rng.gauss(0.0, sigma)
        out.append(max(floor, value))
    return out


def index_of(ts):
    delta = ts - PERIOD_FROM
    return int(delta.total_seconds() // (INTERVAL_MINUTES * 60))


def progress_ratio(i):
    return i / float(TOTAL_POINTS - 1)


# ---------------------------------------------------------------------------
# 埋め込む異常 (P001 FR-111)
# ---------------------------------------------------------------------------
def inject_spike(values, at, multiplier):
    i = index_of(at)
    base = sum(values[max(0, i - 12):i]) / max(1, len(values[max(0, i - 12):i]))
    values[i] = max(values[i], base * multiplier)
    return i


def inject_sustained(values, start, end, sigma_mult, sigma):
    i0, i1 = index_of(start), index_of(end)
    for i in range(i0, i1 + 1):
        values[i] += sigma_mult * sigma
    return i0, i1


def inject_weekly(values, weekday, hour_from, hour_to, multiplier):
    """特定の曜日・時刻帯だけ水準を上げる。"""
    touched = []
    for i, ts in enumerate(TIMESTAMPS):
        if ts.weekday() == weekday and hour_from <= ts.hour < hour_to:
            values[i] = values[i] * multiplier
            touched.append(i)
    return touched


def inject_level_shift(values, at, delta):
    i0 = index_of(at)
    for i in range(i0, len(values)):
        values[i] += delta
    return i0


def leaking_sawtooth(rng, floor_start, floor_end, saw_amplitude, gc_period_points):
    """鋸歯状のまま下限が単調増加する系列 (メモリリーク模擬)。

    6 時間バケットの最小値が単調増加し、ALG-B3 が検知できる形にする。
    """
    out = []
    for i in range(TOTAL_POINTS):
        floor = floor_start + (floor_end - floor_start) * progress_ratio(i)
        phase = (i % gc_period_points) / float(gc_period_points)
        value = floor + saw_amplitude * phase + rng.gauss(0.0, 0.3)
        out.append(min(99.5, max(0.0, value)))
    return out


def monotonic_ramp(rng, start, end, sigma):
    out = []
    for i in range(TOTAL_POINTS):
        value = start + (end - start) * progress_ratio(i) + rng.gauss(0.0, sigma)
        out.append(max(0.0, value))
    return out


# ---------------------------------------------------------------------------
# 書き出し
# ---------------------------------------------------------------------------
def write_lines(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for line in lines:
            handle.write(line)
            handle.write("\n")


def day_slice(index):
    """日ごとの (開始index, 終了index) を返す。"""
    return index * POINTS_PER_DAY, min((index + 1) * POINTS_PER_DAY, TOTAL_POINTS)


def write_dbconnection(logs_dir, series):
    """series: {(host, ds): [値]}"""
    header = "日付,ホスト名,ポート番号,データソース,activeConnectionsCurrentCount"
    for d in range(DAYS):
        lo, hi = day_slice(d)
        if lo >= TOTAL_POINTS:
            break
        date = TIMESTAMPS[lo].strftime("%Y%m%d")
        lines = [header]
        for i in range(lo, hi):
            ts = TIMESTAMPS[i].strftime("%Y/%m/%d %H:%M:%S")
            for host in DB_HOSTS:
                for ds in DB_SOURCES:
                    value = int(round(series[(host, ds)][i]))
                    lines.append("{0},{1},{2},{3},{4}".format(
                        ts, host, DB_PORT, ds, value))
        write_lines(os.path.join(logs_dir, "DBConnection_{0}.csv".format(date)), lines)


GCUTIL_HEADER = "time Timestamp S0 S1 E O M CCS YGC YGCT FGC FGCT GCT"
GC_HEADER = (
    "time Timestamp S0C S1C S0U S1U EC EU OC OU MC MU CCSC CCSU "
    "YGC YGCT FGC FGCT GCT"
)


def write_jstat(logs_dir, container, host, fmt, metrics):
    """metrics: {"ou_pct","eu_pct","mu_pct","ygc","ygct","fgc","fgct","gct"}"""
    header = GCUTIL_HEADER if fmt == "gcutil" else GC_HEADER
    for d in range(DAYS):
        lo, hi = day_slice(d)
        if lo >= TOTAL_POINTS:
            break
        date = TIMESTAMPS[lo].strftime("%Y%m%d")
        lines = [header]
        for i in range(lo, hi):
            ts = TIMESTAMPS[i].strftime("%Y/%m/%d %H:%M:%S")
            uptime = 1000.0 + i * INTERVAL_MINUTES * 60
            ou = metrics["ou_pct"][i]
            eu = metrics["eu_pct"][i]
            mu = metrics["mu_pct"][i]
            s0 = metrics["s0_pct"][i]
            s1 = metrics["s1_pct"][i]
            ccs = metrics["ccs_pct"][i]
            common = "{0:.3f} {1} {2:.3f} {3} {4:.3f}".format(
                metrics["ygct"][i], metrics["fgc"][i], metrics["fgct"][i],
                metrics["ygc"][i], metrics["gct"][i],
            )
            # 並びは YGC YGCT FGC FGCT GCT
            tail = "{0} {1:.3f} {2} {3:.3f} {4:.3f}".format(
                metrics["ygc"][i], metrics["ygct"][i],
                metrics["fgc"][i], metrics["fgct"][i], metrics["gct"][i],
            )
            del common
            if fmt == "gcutil":
                lines.append(
                    "{0} {1:.1f} {2:.2f} {3:.2f} {4:.2f} {5:.2f} {6:.2f} {7:.2f} {8}".format(
                        ts, uptime, s0, s1, eu, ou, mu, ccs, tail)
                )
            else:
                lines.append(
                    "{0} {1:.1f} {2:.1f} {3:.1f} {4:.1f} {5:.1f} {6:.1f} {7:.1f} "
                    "{8:.1f} {9:.1f} {10:.1f} {11:.1f} {12:.1f} {13:.1f} {14}".format(
                        ts, uptime,
                        GC_CAPACITY["s0c"], GC_CAPACITY["s1c"],
                        GC_CAPACITY["s0c"] * s0 / 100.0, GC_CAPACITY["s1c"] * s1 / 100.0,
                        GC_CAPACITY["ec"], GC_CAPACITY["ec"] * eu / 100.0,
                        GC_CAPACITY["oc"], GC_CAPACITY["oc"] * ou / 100.0,
                        GC_CAPACITY["mc"], GC_CAPACITY["mc"] * mu / 100.0,
                        GC_CAPACITY["ccsc"], GC_CAPACITY["ccsc"] * ccs / 100.0,
                        tail)
                )
        write_lines(
            os.path.join(logs_dir, "{0}_gc_{1}_{2}.txt".format(container, host, date)),
            lines,
        )


def write_bqueues(logs_dir, series):
    """series: {queue: {"njobs":[], "pend":[], "run":[], "susp":[]}}"""
    cols = ["time"]
    for q in LSF_QUEUES:
        for f in ("NJOBS", "PEND", "RUN", "SUSP"):
            cols.append("{0}_{1}".format(q, f))
    header = ",".join(cols)
    for d in range(DAYS):
        lo, hi = day_slice(d)
        if lo >= TOTAL_POINTS:
            break
        date = TIMESTAMPS[lo].strftime("%Y%m%d")
        lines = [header]
        for i in range(lo, hi):
            # 実データの形状(1/100 秒付き)を再現する
            ts = TIMESTAMPS[i].strftime("%Y-%m-%d %H:%M:%S") + ":02"
            row = [ts]
            for q in LSF_QUEUES:
                for f in ("njobs", "pend", "run", "susp"):
                    row.append(str(int(round(series[q][f][i]))))
            lines.append(",".join(row))
        write_lines(
            os.path.join(logs_dir, "bqueues_{0}_{1}.txt".format(LSF_HOST, date)), lines
        )


# ---------------------------------------------------------------------------
# ④ sar (sysstat) — `sadf -d` の出力形式 (※CR-010)
# ---------------------------------------------------------------------------
#: `sadf -d` の見出し行。**実際の出力と同じ列を並べる**(取り込まない列も含む)。
#: 取り込む列だけを並べると、「余分な列を読み飛ばす」経路がテストされない。
SAR_HEADERS = {
    "cpu": "CPU;%user;%nice;%system;%iowait;%steal;%idle",
    "memory": ("kbmemfree;kbavail;kbmemused;%memused;kbbuffers;kbcached;"
               "kbcommit;%commit"),
    "swap_space": "kbswpfree;kbswpused;%swpused;kbswpcad;%swpcad",
    "io": "tps;rtps;wtps;bread/s;bwrtn/s",
    "load": "runq-sz;plist-sz;ldavg-1;ldavg-5;ldavg-15;blocked",
    "swapping": "pswpin/s;pswpout/s",
    "disk": "DEV;tps;rd_sec/s;wr_sec/s;avgrq-sz;avgqu-sz;await;svctm;%util",
    "network": ("IFACE;rxpck/s;txpck/s;rxkB/s;txkB/s;rxcmp/s;txcmp/s;"
                "rxmcst/s;%ifutil"),
    # ※CR-011 NFS。取り込まない列 (packet/s, udp/s, tcp/s) も**実際の出力どおり**
    # に並べる。並べないと「取り込まない列を飛ばす」経路が試されない。
    "nfs": "call/s;retrans/s;read/s;write/s;access/s;getatt/s",
    "nfsd": ("scall/s;badcall/s;packet/s;udp/s;tcp/s;hit/s;miss/s;"
             "sread/s;swrite/s;saccess/s;sgetatt/s"),
}

#: ブロックを出す順序。**固定する**(NFR-009 の再現性)。
SAR_ORDER = ["cpu", "memory", "swap_space", "io", "load", "swapping",
             "disk", "network", "nfs", "nfsd"]   # ※CR-011

#: `sadf` が出す採取間隔 (秒)。
SAR_INTERVAL_SEC = INTERVAL_MINUTES * 60


def _f(value, digits=2):
    return "{0:.{1}f}".format(float(value), digits)


def build_sar_series(rng, host):
    """1 ホスト分の sar の系列を作る。

    **CPU は内部で辻褄を合わせる** (`%idle` = 100 - その他)。合わせないと
    `pct_idle` が独立した乱数になり、「夜間は遊休が続く」という実データの
    形が出ない。**ALG-C1 の除外 (FR-148) を確かめるには、この形が要る**
    (`pct_idle` が 90% 以上で数時間続く区間が実際にできる)。
    """
    user = baseline_series(rng, base=12.0, amplitude=22.0, sigma=2.0, floor=0.5)
    system = baseline_series(rng, base=4.0, amplitude=6.0, sigma=0.8, floor=0.2)
    iowait = baseline_series(rng, base=1.5, amplitude=2.5, sigma=0.4, floor=0.0)
    nice = [0.0] * TOTAL_POINTS
    steal = [0.0] * TOTAL_POINTS
    idle = [max(0.0, 100.0 - (user[i] + system[i] + iowait[i]))
            for i in range(TOTAL_POINTS)]

    memused = baseline_series(rng, base=52.0, amplitude=12.0, sigma=1.5, floor=5.0)
    commit = [min(99.0, v * 0.9 + 3.0) for v in memused]
    memfree = [max(1024.0, (100.0 - v) * 320.0) for v in memused]
    swpused = baseline_series(rng, base=2.0, amplitude=1.5, sigma=0.3, floor=0.0)

    tps = baseline_series(rng, base=18.0, amplitude=24.0, sigma=2.5, floor=0.1)
    bread = baseline_series(rng, base=420.0, amplitude=600.0, sigma=60.0, floor=1.0)
    bwrtn = baseline_series(rng, base=310.0, amplitude=420.0, sigma=45.0, floor=1.0)

    runq = baseline_series(rng, base=2.0, amplitude=4.0, sigma=0.6, floor=0.0)
    ldavg1 = baseline_series(rng, base=1.4, amplitude=2.2, sigma=0.3, floor=0.0)
    ldavg5 = [v * 0.9 for v in ldavg1]
    blocked = baseline_series(rng, base=0.3, amplitude=0.6, sigma=0.15, floor=0.0)

    pswpin = baseline_series(rng, base=0.2, amplitude=0.4, sigma=0.1, floor=0.0)
    pswpout = baseline_series(rng, base=0.1, amplitude=0.3, sigma=0.08, floor=0.0)

    disks = {}
    for dev in SAR_DEVICES:
        disks[dev] = {
            "tps": baseline_series(rng, 14.0, 20.0, 2.0, floor=0.1),
            "await": baseline_series(rng, 5.0, 6.0, 0.9, floor=0.1),
            "util": baseline_series(rng, 18.0, 26.0, 3.0, floor=0.1),
        }
    ifaces = {}
    for iface in SAR_IFACES:
        ifaces[iface] = {
            "rxkb": baseline_series(rng, 260.0, 380.0, 30.0, floor=1.0),
            "txkb": baseline_series(rng, 180.0, 260.0, 22.0, floor=1.0),
            "ifutil": baseline_series(rng, 9.0, 14.0, 1.5, floor=0.1),
        }

    # ※CR-011 NFS クライアント。全ホストが NFS を使う。
    nfs_call = baseline_series(rng, 180.0, 240.0, 22.0, floor=1.0)
    nfs = {
        "call": nfs_call,
        # **再送は平常時ほぼ 0 である。** 増えたら不調のしるし。
        "retrans": baseline_series(rng, 0.05, 0.06, 0.02, floor=0.0),
        "read": [v * 0.35 for v in nfs_call],
        "write": [v * 0.20 for v in nfs_call],
        "access": [v * 0.18 for v in nfs_call],
        "getatt": [v * 0.22 for v in nfs_call],
    }

    # ※CR-011 NFS サーバ。**サーバ役のホストだけ値を持つ。**
    if host == SAR_NFS_SERVER:
        s_call = baseline_series(rng, 320.0, 420.0, 38.0, floor=1.0)
        nfsd = {
            "scall": s_call,
            "badcall": baseline_series(rng, 0.02, 0.04, 0.01, floor=0.0),
            "hit": [v * 0.88 for v in s_call],
            "miss": [v * 0.12 for v in s_call],
            "sread": [v * 0.33 for v in s_call],
            "swrite": [v * 0.19 for v in s_call],
            "saccess": [v * 0.17 for v in s_call],
            "sgetatt": [v * 0.21 for v in s_call],
        }
    else:
        nfsd = None

    return {
        "cpu": {"user": user, "nice": nice, "system": system,
                "iowait": iowait, "steal": steal, "idle": idle},
        "nfs": nfs,
        "nfsd": nfsd,
        "memory": {"memfree": memfree, "memused": memused, "commit": commit},
        "swap_space": {"swpused": swpused},
        "io": {"tps": tps, "bread": bread, "bwrtn": bwrtn},
        "load": {"runq": runq, "ldavg1": ldavg1, "ldavg5": ldavg5,
                 "blocked": blocked},
        "swapping": {"pswpin": pswpin, "pswpout": pswpout},
        "disk": disks,
        "network": ifaces,
    }


def _sar_block_rows(activity, series, host, ts_text, i):
    """1 時刻・1 活動分のデータ行を返す (デバイス単位で複数行になる)。"""
    prefix = "{0};{1};{2};".format(host, SAR_INTERVAL_SEC, ts_text)
    rows = []
    if activity == "cpu":
        c = series["cpu"]
        rows.append(prefix + ";".join([
            "-1", _f(c["user"][i]), _f(c["nice"][i]), _f(c["system"][i]),
            _f(c["iowait"][i]), _f(c["steal"][i]), _f(c["idle"][i])]))
    elif activity == "memory":
        m = series["memory"]
        used_kb = 32768000.0 * m["memused"][i] / 100.0
        rows.append(prefix + ";".join([
            _f(m["memfree"][i], 0), _f(m["memfree"][i] * 1.1, 0),
            _f(used_kb, 0), _f(m["memused"][i]),
            "204800", "1048576", _f(used_kb * 0.8, 0), _f(m["commit"][i])]))
    elif activity == "swap_space":
        w = series["swap_space"]
        used_kb = 8388608.0 * w["swpused"][i] / 100.0
        rows.append(prefix + ";".join([
            _f(8388608.0 - used_kb, 0), _f(used_kb, 0), _f(w["swpused"][i]),
            "0", "0.00"]))
    elif activity == "io":
        b = series["io"]
        rows.append(prefix + ";".join([
            _f(b["tps"][i]), _f(b["tps"][i] * 0.6), _f(b["tps"][i] * 0.4),
            _f(b["bread"][i]), _f(b["bwrtn"][i])]))
    elif activity == "load":
        q = series["load"]
        rows.append(prefix + ";".join([
            _f(q["runq"][i], 0), "180", _f(q["ldavg1"][i]), _f(q["ldavg5"][i]),
            _f(q["ldavg5"][i] * 0.95), _f(q["blocked"][i], 0)]))
    elif activity == "swapping":
        p = series["swapping"]
        rows.append(prefix + ";".join([_f(p["pswpin"][i]), _f(p["pswpout"][i])]))
    elif activity == "disk":
        for dev in SAR_DEVICES:
            d = series["disk"][dev]
            rows.append(prefix + ";".join([
                dev, _f(d["tps"][i]), _f(d["tps"][i] * 32.0),
                _f(d["tps"][i] * 18.0), "46.83", "0.09",
                _f(d["await"][i]), "0.61", _f(d["util"][i])]))
    elif activity == "nfs":                       # ※CR-011
        n = series["nfs"]
        rows.append(prefix + ";".join([
            _f(n["call"][i]), _f(n["retrans"][i]), _f(n["read"][i]),
            _f(n["write"][i]), _f(n["access"][i]), _f(n["getatt"][i])]))
    elif activity == "nfsd":                      # ※CR-011
        d = series["nfsd"]
        if d is None:
            # **NFS サーバでないホストにはブロックを出さない。**
            return []
        rows.append(prefix + ";".join([
            _f(d["scall"][i]), _f(d["badcall"][i]),
            # packet/s, udp/s, tcp/s は**取り込まない列**だが、実際の出力に
            # 現れるため並べる (飛ばす経路を試すため。P001 FR-142b)
            _f(d["scall"][i] * 1.05), _f(d["scall"][i] * 0.02),
            _f(d["scall"][i] * 0.98),
            _f(d["hit"][i]), _f(d["miss"][i]), _f(d["sread"][i]),
            _f(d["swrite"][i]), _f(d["saccess"][i]), _f(d["sgetatt"][i])]))
    elif activity == "network":
        for iface in SAR_IFACES:
            n = series["network"][iface]
            rows.append(prefix + ";".join([
                iface, _f(n["rxkb"][i] * 2.4), _f(n["txkb"][i] * 2.1),
                _f(n["rxkb"][i]), _f(n["txkb"][i]), "0.00", "0.00", "0.00",
                _f(n["ifutil"][i])]))
    return rows


def write_sar(logs_dir, all_series):
    """`sa-{host}-{yyyymmdd}.csv` を書く (※CR-010)。

    **活動種別ごとのブロックを、時刻ごとに並べる。** `sadf` を活動ごとに
    呼んで連結したファイル (README 付録A の変換スクリプト) と同じ形にするなら
    ブロックごとにまとめるほうが近いが、**見出しが何度も現れる形のほうが
    ローダの状態遷移を厳しく試せる**ため、時刻ごとに切り替える。
    """
    for host, series in sorted(all_series.items()):
        for d in range(DAYS):
            lo, hi = day_slice(d)
            if lo >= TOTAL_POINTS:
                break
            date = TIMESTAMPS[lo].strftime("%Y%m%d")
            lines = []
            for i in range(lo, hi):
                ts_text = TIMESTAMPS[i].strftime("%Y-%m-%d %H:%M:%S") + " UTC"
                for activity in SAR_ORDER:
                    block = _sar_block_rows(activity, series, host, ts_text, i)
                    # ※CR-011 出す行が無い活動は**見出しごと出さない**
                    # (NFS サーバでないホストの nfsd)。
                    if not block:
                        continue
                    lines.append("# hostname;interval;timestamp;"
                                 + SAR_HEADERS[activity])
                    lines.extend(block)
            write_lines(
                os.path.join(logs_dir, "sa-{0}-{1}.csv".format(host, date)),
                lines)


def cumulative(rng, per_point_mean, per_point_sigma, scale=1.0, start=0.0):
    """単調増加する累積値を作る。"""
    out = []
    acc = start
    for _ in range(TOTAL_POINTS):
        acc += max(0.0, rng.gauss(per_point_mean, per_point_sigma)) * scale
        out.append(acc)
    return out


def cumulative_from_rate(rates):
    out = []
    acc = 0.0
    for r in rates:
        acc += max(0.0, r)
        out.append(acc)
    return out


# ---------------------------------------------------------------------------
# 正常系 + 異常の生成
# ---------------------------------------------------------------------------
def generate_normal(out_dir):
    rng = random.Random(SEED)
    logs_dir = os.path.join(out_dir, "logs")
    injected = []

    # --- ① DBConnection --------------------------------------------------
    db_series = {}
    for host in DB_HOSTS:
        for ds in DB_SOURCES:
            db_series[(host, ds)] = baseline_series(
                rng, base=8.0, amplitude=14.0, sigma=1.2
            )

    # INJ-001: 単発スパイク (平常の 10 倍)
    at = datetime(2026, 6, 5, 14, 0, 0)
    inject_spike(db_series[("host01", "OraclePool_1")], at, 10.0)
    injected.append({
        "id": "INJ-001", "kind": "spike",
        "series_id": "db_connection/host01:7003/OraclePool_1",
        "metric": "active_connections",
        "from": at.isoformat(), "to": at.isoformat(),
        "expect_algorithms": ["ALG-A1", "ALG-A2", "ALG-A3"],
        "expect_min_severity": "WARN",
        "description": "平常値の10倍のスパイクを1点だけ埋め込む",
    })

    # INJ-002: 連続する中程度の逸脱
    start, end = datetime(2026, 6, 7, 9, 0, 0), datetime(2026, 6, 7, 11, 0, 0)
    inject_sustained(db_series[("host01", "OraclePool_2")],
                     start, end, sigma_mult=8.0, sigma=1.2)
    injected.append({
        "id": "INJ-002", "kind": "sustained",
        "series_id": "db_connection/host01:7003/OraclePool_2",
        "metric": "active_connections",
        "from": start.isoformat(), "to": end.isoformat(),
        "expect_algorithms": ["ALG-A1", "ALG-A2", "ALG-A4"],
        "expect_min_severity": "WARN",
        "description": "連続する複数点を平常+8σだけ持ち上げる",
    })

    # INJ-003: 水曜 03:00-04:00 だけ平常の 4 倍
    inject_weekly(db_series[("host02", "OraclePool_3")],
                  weekday=2, hour_from=3, hour_to=4, multiplier=4.0)
    injected.append({
        "id": "INJ-003", "kind": "seasonal",
        "series_id": "db_connection/host02:7003/OraclePool_3",
        "metric": "active_connections",
        "from": datetime(2026, 6, 3, 3, 0, 0).isoformat(),
        "to": datetime(2026, 6, 10, 4, 0, 0).isoformat(),
        "expect_algorithms": ["ALG-A1", "ALG-A2", "ALG-A3", "ALG-A5"],
        "expect_min_severity": "WARN",
        "description": "水曜03-04時だけ平常の4倍にする",
    })
    write_dbconnection(logs_dir, db_series)

    # --- ② jstat ---------------------------------------------------------
    # app01: メモリリーク模擬 (INJ-004) と Metaspace の緩やかな増加 (INJ-007)
    ou_leak = leaking_sawtooth(rng, floor_start=40.0, floor_end=92.0,
                               saw_amplitude=6.0, gc_period_points=24)
    eu_app01 = baseline_series(rng, base=30.0, amplitude=40.0, sigma=3.0)
    mu_app01 = monotonic_ramp(rng, start=55.0, end=88.0, sigma=0.4)
    # 後半で Full GC の頻度を上げる
    fgc_rate = []
    for i in range(TOTAL_POINTS):
        r = progress_ratio(i)
        fgc_rate.append(0.02 + 0.55 * (r ** 2))
    fgc_app01 = [int(v) for v in cumulative_from_rate(fgc_rate)]
    fgct_app01 = cumulative_from_rate([r * 0.9 for r in fgc_rate])
    ygc_app01 = [int(v) for v in cumulative_from_rate(
        [1.0 + rng.random() * 0.2 for _ in range(TOTAL_POINTS)])]
    ygct_app01 = cumulative_from_rate(
        [0.03 + rng.random() * 0.01 for _ in range(TOTAL_POINTS)])
    gct_app01 = [ygct_app01[i] + fgct_app01[i] for i in range(TOTAL_POINTS)]

    write_jstat(logs_dir, "app01", "host01", "gcutil", {
        "ou_pct": ou_leak, "eu_pct": eu_app01, "mu_pct": mu_app01,
        "s0_pct": baseline_series(rng, 20.0, 40.0, 4.0),
        "s1_pct": baseline_series(rng, 20.0, 40.0, 4.0),
        "ccs_pct": baseline_series(rng, 70.0, 5.0, 0.5),
        "ygc": ygc_app01, "ygct": ygct_app01,
        "fgc": fgc_app01, "fgct": fgct_app01, "gct": gct_app01,
    })
    injected.append({
        "id": "INJ-004", "kind": "leak",
        "series_id": "jvm_gc/app01@host01", "metric": "ou",
        "from": PERIOD_FROM.isoformat(), "to": PERIOD_TO.isoformat(),
        "expect_algorithms": ["ALG-B1", "ALG-B2", "ALG-B3", "ALG-B5"],
        "expect_min_severity": "FATAL",
        "description": "鋸歯状のまま下限が40%→92%へ単調増加(メモリリーク相当)",
    })
    injected.append({
        "id": "INJ-007", "kind": "trend",
        "series_id": "jvm_gc/app01@host01", "metric": "mu",
        "from": PERIOD_FROM.isoformat(), "to": PERIOD_TO.isoformat(),
        "expect_algorithms": ["ALG-B1", "ALG-B2", "ALG-B5"],
        "expect_min_severity": "WARN",
        "description": "Metaspace使用率が55%→88%へ緩やかに単調増加",
    })

    # app02: 水準シフト (INJ-005)
    ou_app02 = baseline_series(rng, base=45.0, amplitude=10.0, sigma=1.5)
    eu_app02 = baseline_series(rng, base=30.0, amplitude=20.0, sigma=2.0)
    shift_at = datetime(2026, 6, 8, 12, 0, 0)
    inject_level_shift(eu_app02, shift_at, delta=3.0 * 2.0 * 2.0)
    mu_app02 = baseline_series(rng, base=60.0, amplitude=2.0, sigma=0.5)
    fgc_app02 = [int(v) for v in cumulative_from_rate(
        [0.05 for _ in range(TOTAL_POINTS)])]
    fgct_app02 = cumulative_from_rate([0.04 for _ in range(TOTAL_POINTS)])
    ygc_app02 = [int(v) for v in cumulative_from_rate(
        [1.0 for _ in range(TOTAL_POINTS)])]
    ygct_app02 = cumulative_from_rate(
        [0.03 + rng.random() * 0.005 for _ in range(TOTAL_POINTS)])
    gct_app02 = [ygct_app02[i] + fgct_app02[i] for i in range(TOTAL_POINTS)]

    write_jstat(logs_dir, "app02", "host02", "gc", {
        "ou_pct": ou_app02, "eu_pct": eu_app02, "mu_pct": mu_app02,
        "s0_pct": baseline_series(rng, 20.0, 30.0, 3.0),
        "s1_pct": baseline_series(rng, 20.0, 30.0, 3.0),
        "ccs_pct": baseline_series(rng, 70.0, 4.0, 0.4),
        "ygc": ygc_app02, "ygct": ygct_app02,
        "fgc": fgc_app02, "fgct": fgct_app02, "gct": gct_app02,
    })
    injected.append({
        "id": "INJ-005", "kind": "level_shift",
        "series_id": "jvm_gc/app02@host02", "metric": "eu",
        "from": shift_at.isoformat(), "to": PERIOD_TO.isoformat(),
        "expect_algorithms": ["ALG-B4", "ALG-A4", "ALG-B1"],
        "expect_min_severity": "WARN",
        "description": "2026-06-08 12:00 を境に水準が階段状に上がり元に戻らない",
    })

    # --- ③ bqueues -------------------------------------------------------
    lsf = {}
    for q in LSF_QUEUES:
        lsf[q] = {
            "njobs": baseline_series(rng, 20.0, 30.0, 2.5),
            "pend": baseline_series(rng, 2.0, 4.0, 0.8),
            "run": baseline_series(rng, 15.0, 20.0, 2.0),
            "susp": baseline_series(rng, 0.4, 0.6, 0.15),
        }
    # INJ-006: long キューの pend が単調増加、run は横ばい
    lsf["long"]["pend"] = monotonic_ramp(rng, start=1.0, end=200.0, sigma=2.0)
    lsf["long"]["run"] = baseline_series(rng, 15.0, 3.0, 1.0)
    lsf["long"]["njobs"] = [
        lsf["long"]["pend"][i] + lsf["long"]["run"][i] for i in range(TOTAL_POINTS)
    ]
    write_bqueues(logs_dir, lsf)
    injected.append({
        "id": "INJ-006", "kind": "trend",
        "series_id": "lsf_queue/lsfhost01/long", "metric": "pend",
        "from": PERIOD_FROM.isoformat(), "to": PERIOD_TO.isoformat(),
        "expect_algorithms": ["ALG-B1", "ALG-B2", "ALG-B5"],
        "expect_min_severity": "FATAL",
        "description": "pend が 1→200 へ単調増加し run は横ばい(ジョブ滞留相当)",
    })

    # --- ④ sar (※CR-010) -------------------------------------------------
    sar_series = {}
    for host in SAR_HOSTS:
        sar_series[host] = build_sar_series(rng, host)

    # ★sar の `cpu` のデバイス列は CPU 番号であり、`-1` が「全 CPU」を意味する。
    #   したがって系列キーは `sar/{host}/cpu/-1` になる (`-` ではない)。
    #   `-` は「デバイス列を持たない活動」の占位子である (P001 FR-144)。
    # INJ-010: host01 の %iowait に単発スパイク。
    # **INJ-001 (同じ host01 の DB コネクション数のスパイク) と同時刻にする。**
    # 狙いは「アプリ層の異常と OS 資源の異常が、同時に発生したアノマリーとして
    # 相互に現れること」の確認である (P001 FR-150)。どちらも観点1 であり、
    # 観点2 を含むイベントでは欄ごと出ない (CR-002) 制約に触れない。
    sar_spike_at = datetime(2026, 6, 5, 14, 0, 0)
    inject_spike(sar_series["host01"]["cpu"]["iowait"], sar_spike_at, 10.0)
    injected.append({
        "id": "INJ-010", "kind": "spike",
        "series_id": "sar/host01/cpu/-1", "metric": "pct_iowait",
        "from": sar_spike_at.isoformat(), "to": sar_spike_at.isoformat(),
        "expect_algorithms": ["ALG-A1", "ALG-A2"],
        "expect_min_severity": "WARN",
        "description": "%iowait が平常の 10 倍に跳ねた (INJ-001 と同一ホスト・同時刻)",
    })

    # INJ-011: host02 の %memused が 6/8 12:00 以降 93〜97% に張り付く。
    # **ALG-C1 が「推定上限」ではなく上限 100 で判定すること**の確認を兼ねる
    # (P001 FR-148)。sar の比率は上限が自明であり ADR-016 の推定を通らない。
    mem_at = datetime(2026, 6, 8, 12, 0, 0)
    mem_from = index_of(mem_at)
    memused = sar_series["host02"]["memory"]["memused"]
    for i in range(mem_from, TOTAL_POINTS):
        memused[i] = 93.0 + rng.random() * 4.0
    # 派生列の辻褄を合わせる (使用率が上がれば空きは減る)
    sar_series["host02"]["memory"]["commit"] = [
        min(99.0, v * 0.9 + 3.0) for v in memused]
    sar_series["host02"]["memory"]["memfree"] = [
        max(1024.0, (100.0 - v) * 320.0) for v in memused]
    injected.append({
        "id": "INJ-011", "kind": "saturation",
        "series_id": "sar/host02/memory/-", "metric": "pct_memused",
        "from": mem_at.isoformat(), "to": PERIOD_TO.isoformat(),
        "expect_algorithms": ["ALG-C1"],
        "expect_min_severity": "WARN",
        "description": "%memused が 93〜97% に張り付いたまま戻らない (上限 100 は自明)",
    })

    # INJ-012: host02 のスワップアウトが 6/10 12:00 以降に急増する。
    # **INJ-011 (同じ host02 のメモリ張り付き) の帰結として自然な形にする。**
    # 併せて、8 活動すべてがイベントとして現れる状態にする (A014 #5)。
    # ★ベースラインが 0.1 前後と小さいため、スパイクでは `effective_sigma` の
    #   下限 (sigma_floor_abs=0.5) に埋もれて検知されない。水準そのものを
    #   上げる形にする。★
    swap_at = datetime(2026, 6, 10, 12, 0, 0)
    swap_from = index_of(swap_at)
    swp = sar_series["host02"]["swapping"]
    for i in range(swap_from, TOTAL_POINTS):
        swp["pswpout"][i] = 30.0 + rng.random() * 40.0
        swp["pswpin"][i] = 20.0 + rng.random() * 30.0
    injected.append({
        "id": "INJ-012", "kind": "level_shift",
        "series_id": "sar/host02/swapping/-", "metric": "pswpout_s",
        "from": swap_at.isoformat(), "to": PERIOD_TO.isoformat(),
        "expect_algorithms": ["ALG-B4"],
        "expect_min_severity": "WARN",
        "description": "スワップアウトが 0.1 前後から 30〜70 へ跳ね上がり戻らない",
    })

    # INJ-013: lsfhost01 の NFS 再送が 6/9 12:00 以降に急増する。
    # **lsfhost01 は LSF のジョブ滞留 (INJ-006) と同じホストである。**
    # 依頼者が指摘した「LSF 計算グリッドが NFS でファイルを共有するため、
    # NFS の詰まりがジョブ滞留の原因になりうる」という因果を、
    # **レポート上で突き合わせられる形**にする (P001 FR-142a)。
    # ★ベースラインが 0.05 前後と小さいため、スパイクでは effective_sigma の
    #   下限 (ADR-003) に埋もれる。水準そのものを上げる (INJ-012 と同じ理由)。★
    nfs_at = datetime(2026, 6, 9, 12, 0, 0)
    nfs_from = index_of(nfs_at)
    retrans = sar_series[LSF_HOST]["nfs"]["retrans"]
    for i in range(nfs_from, TOTAL_POINTS):
        retrans[i] = 8.0 + rng.random() * 7.0
    injected.append({
        "id": "INJ-013", "kind": "level_shift",
        "series_id": "sar/{0}/nfs/-".format(LSF_HOST), "metric": "retrans_s",
        "from": nfs_at.isoformat(), "to": PERIOD_TO.isoformat(),
        "expect_algorithms": ["ALG-B4"],
        "expect_min_severity": "WARN",
        "description": "NFS の再送が 0.05 前後から 8〜15 へ跳ね上がり戻らない"
                       "(LSF のジョブ滞留 INJ-006 と同一ホスト)",
    })

    # INJ-014: NFS サーバ (host02) の応答キャッシュ失敗が 6/11 以降に上昇する。
    # **nfsd からもイベントが出ることを保証する。**
    nfsd_at = datetime(2026, 6, 11, 0, 0, 0)
    nfsd_from = index_of(nfsd_at)
    miss = sar_series[SAR_NFS_SERVER]["nfsd"]["miss"]
    for i in range(nfsd_from, TOTAL_POINTS):
        miss[i] = miss[i] * 4.0 + 120.0
    injected.append({
        "id": "INJ-014", "kind": "level_shift",
        "series_id": "sar/{0}/nfsd/-".format(SAR_NFS_SERVER),
        "metric": "miss_s",
        "from": nfsd_at.isoformat(), "to": PERIOD_TO.isoformat(),
        "expect_algorithms": ["ALG-B4"],
        "expect_min_severity": "WARN",
        "description": "NFS サーバの応答キャッシュ失敗が段階的に上昇した",
    })

    # **%idle は他の CPU 時間の残りである。** 注入のあとに計算し直さないと
    # 合計が 100 を超え、実データにならない (ALG-C1 の除外の確認に効く)。
    for host in SAR_HOSTS:
        c = sar_series[host]["cpu"]
        c["idle"] = [
            max(0.0, 100.0 - (c["user"][i] + c["nice"][i] + c["system"][i]
                              + c["iowait"][i] + c["steal"][i]))
            for i in range(TOTAL_POINTS)
        ]

    write_sar(logs_dir, sar_series)

    injected.sort(key=lambda x: x["id"])
    expected = {
        "generated_at": "2026-06-01T00:00:00",
        "seed": SEED,
        "period": {"from": PERIOD_FROM.isoformat(), "to": PERIOD_TO.isoformat()},
        "injected": injected,
        "clean_series": ["lsf_queue/lsfhost01/short"],
    }
    path = os.path.join(out_dir, "expected.json")
    os.makedirs(out_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(expected, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return expected


# ---------------------------------------------------------------------------
# 破損データ (FR-112)
# ---------------------------------------------------------------------------
DB_HEADER = "日付,ホスト名,ポート番号,データソース,activeConnectionsCurrentCount"


def _db_rows(day, count, start_minute=0):
    rows = []
    for i in range(count):
        ts = datetime(2026, 6, day, 0, start_minute + i, 0)
        rows.append("{0},h1,7003,ds1,{1}".format(ts.strftime("%Y/%m/%d %H:%M:%S"), i))
    return rows


#: 破損データの期待値。テストがこの定数と実際の解析結果を突き合わせる。
BROKEN_EXPECTATIONS = {
    "DBConnection_20260601.csv": {
        "ok_rows": 5,
        "errors": {"列数不一致": 2, "日付書式不正": 2, "数値変換不能": 2},
    },
    "DBConnection_20260602.csv": {"ok_rows": 0, "errors": {}},
    "DBConnection_20260603.csv": {"ok_rows": 0, "errors": {}},
    "DBConnection_20260604.csv": {"ok_rows": 5, "errors": {}},
    "DBConnection_20260605.csv": {"ok_rows": 5, "errors": {}},
    "broken01_gc_host1_20260601.txt": {"ok_rows": 0, "errors": {"見出し不明": 1}},
    "broken02_gc_host1_20260601.txt": {"ok_rows": 5, "errors": {"列数不一致": 2}},
    "bqueues_host1_20260601.txt": {"ok_rows": 0, "errors": {"見出し不明": 1}},
    "bqueues_host2_20260601.txt": {"ok_rows": 5, "errors": {"日付書式不正": 2}},
}


def generate_broken(out_dir):
    logs_dir = os.path.join(out_dir, "logs")
    os.makedirs(logs_dir, exist_ok=True)

    # 正常 5 行 + 列数不足 2 + 日付不正 2 + 数値不正 2
    lines = [DB_HEADER] + _db_rows(1, 5)
    lines += ["2026/06/01 00:10:00,h1,7003", "2026/06/01 00:11:00,h1"]
    lines += ["2026-06-01 00:12:00,h1,7003,ds1,1", "20260601 001300,h1,7003,ds1,2"]
    lines += ["2026/06/01 00:14:00,h1,abc,ds1,3", "2026/06/01 00:15:00,h1,7003,ds1,xyz"]
    write_lines(os.path.join(logs_dir, "DBConnection_20260601.csv"), lines)

    # 空ファイル
    with open(os.path.join(logs_dir, "DBConnection_20260602.csv"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write("")

    # 見出しのみ
    write_lines(os.path.join(logs_dir, "DBConnection_20260603.csv"), [DB_HEADER])

    # BOM 付き UTF-8 の正常 5 行
    with open(os.path.join(logs_dir, "DBConnection_20260604.csv"), "w",
              encoding="utf-8-sig", newline="\n") as handle:
        handle.write("\n".join([DB_HEADER] + _db_rows(4, 5)) + "\n")

    # CRLF の正常 5 行
    with open(os.path.join(logs_dir, "DBConnection_20260605.csv"), "w",
              encoding="utf-8", newline="\r\n") as handle:
        handle.write("\n".join([DB_HEADER] + _db_rows(5, 5)) + "\n")

    # jstat: 判別不能
    write_lines(os.path.join(logs_dir, "broken01_gc_host1_20260601.txt"),
                ["a b c d e", "1 2 3 4 5", "6 7 8 9 10"])

    # jstat: 正常な -gcutil 5 行 + 列数の違う行 2 行
    jlines = [GCUTIL_HEADER]
    for i in range(5):
        ts = datetime(2026, 6, 1, 0, i * 5, 0).strftime("%Y/%m/%d %H:%M:%S")
        jlines.append(
            "{0} {1}.0 10.00 11.00 12.00 13.00 14.00 15.00 {2} 1.500 2 3.500 5.000".format(
                ts, 1000 + i * 300, 100 + i)
        )
    jlines.append("2026/06/01 00:30:00 2500.0 10.00 11.00")
    jlines.append("2026/06/01 00:35:00 2800.0 10.00 11.00 12.00 13.00")
    write_lines(os.path.join(logs_dir, "broken02_gc_host1_20260601.txt"), jlines)

    # bqueues: 見出しに _NJOBS 等が無い
    write_lines(os.path.join(logs_dir, "bqueues_host1_20260601.txt"),
                ["time,a,b,c", "2026-06-01 00:00:00,1,2,3"])

    # bqueues: 正常 5 行 + 時刻書式不正 2 行
    blines = ["time,q1_NJOBS,q1_PEND,q1_RUN,q1_SUSP"]
    for i in range(5):
        ts = datetime(2026, 6, 1, 0, i * 5, 0).strftime("%Y-%m-%d %H:%M:%S") + ":02"
        blines.append("{0},{1},0,{1},0".format(ts, i + 1))
    blines.append("20260601 003000,6,0,6,0")
    blines.append("June 1 2026,7,0,7,0")
    write_lines(os.path.join(logs_dir, "bqueues_host2_20260601.txt"), blines)


# ---------------------------------------------------------------------------
def main(argv):
    broken = False
    args = []
    for a in argv:
        if a == "--broken":
            broken = True
        else:
            args.append(a)
    if len(args) != 1:
        sys.stderr.write("usage: gen_testdata.py <出力先dir> [--broken]\n")
        return 1
    out_dir = args[0]
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as exc:
        sys.stderr.write("error: 出力先を作成できません: {0} ({1})\n".format(out_dir, exc))
        return 1
    if not os.path.isdir(out_dir):
        sys.stderr.write("error: 出力先がディレクトリではありません: {0}\n".format(out_dir))
        return 1

    if broken:
        generate_broken(out_dir)
        sys.stdout.write("破損テストデータを生成しました: {0}\n".format(out_dir))
    else:
        expected = generate_normal(out_dir)
        sys.stdout.write(
            "テストデータを生成しました: {0} (異常 {1} 件)\n".format(
                out_dir, len(expected["injected"]))
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
