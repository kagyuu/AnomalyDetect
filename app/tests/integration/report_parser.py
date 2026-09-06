"""レポート群を機械的に解析してイベント一覧を返す (P006 TP-15/TP-16)。

※CR-001 により対象は単一の report.md ではなくホスト別ファイル群である。
呼び出し側が全ファイルを連結したテキストを渡す。

**このモジュールは docs/P002-frontend-spec.md 4.2 の項目書式に依存する。
レポート書式を変更した場合、この解析処理も同時に直す必要がある** (TP-16)。
"""

import re
from datetime import datetime

# ※CR-003: ID にホスト名が入る (EVT-{YYYYMMDD}-{host}-{NNN})
_EVENT_HEAD = re.compile(
    r"^# イベントID\(\s*(?P<id>EVT-\d{8}-.+?-\d{3})\s*\)\s*$")
_ITEM = re.compile(r"^\* (?P<key>[^:]+?)\s*:\s*(?P<value>.*)$")
_DATA = re.compile(
    r"^(?P<source>\S+)\s*/\s*(?P<metric>[A-Za-z0-9_]+)(?:\s*\([^)]*\))?\s*/\s*(?P<key>.*)$"
)
_ALG = re.compile(r"(ALG-[AB]\d)")


class ParsedEvent(object):
    def __init__(self, event_id):
        self.event_id = event_id
        self.algorithms = []
        self.severity = ""
        self.source = ""
        self.metric = ""
        self.series_key = ""
        self.values_text = ""
        self.start_ts = None
        self.end_ts = None
        self.phenomenon = ""
        self.explanation = ""
        self.causes_text = ""
        self.correlations = []
        self.items = {}

    def __repr__(self):
        return "ParsedEvent({0} {1}/{2} {3})".format(
            self.event_id, self.source, self.metric, self.severity
        )

    def series_id(self):
        """series_key から series_id を復元する (P002 6.3 の書式)。"""
        pairs = {}
        for part in self.series_key.split(","):
            if "=" in part:
                k, _, v = part.partition("=")
                pairs[k.strip()] = v.strip()
        if self.source == "db_connection":
            return "db_connection/{0}:{1}/{2}".format(
                pairs.get("host", ""), pairs.get("port", ""),
                pairs.get("datasource", ""))
        if self.source == "jvm_gc":
            return "jvm_gc/{0}@{1}".format(
                pairs.get("container", ""), pairs.get("host", ""))
        if self.source == "lsf_queue":
            return "lsf_queue/{0}/{1}".format(
                pairs.get("host", ""), pairs.get("queue", ""))
        return self.series_key


def _parse_ts(text):
    """レポートの時刻欄を読む。

    ※CR-008 で「`2026-06-15 10:23:45 (UTC)`」のようにタイムゾーンが併記される
    ようになった。**併記を落として時刻だけを読む**(比較の対象は時刻であり、
    タイムゾーンが正しく併記されているかは別のテストで確かめる)。
    """
    value = text.strip()
    if value.endswith(")") and " (" in value:
        value = value.rsplit(" (", 1)[0].strip()
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def parse_events(text):
    """レポート本文からイベント一覧を返す。想定外の行は無視する。"""
    events = []
    current = None
    in_correlations = False
    for raw in text.splitlines():
        head = _EVENT_HEAD.match(raw)
        if head:
            current = ParsedEvent(head.group("id"))
            events.append(current)
            in_correlations = False
            continue
        if current is None:
            continue
        if raw.startswith("## "):
            current = None
            continue

        item = _ITEM.match(raw)
        if item:
            key = item.group("key").strip()
            value = item.group("value").strip()
            current.items[key] = value
            in_correlations = False
            if key == "イベント種別":
                current.algorithms = _ALG.findall(value)
            elif key == "脅威度":
                current.severity = value
            elif key == "データ":
                m = _DATA.match(value)
                if m:
                    current.source = m.group("source")
                    current.metric = m.group("metric")
                    current.series_key = m.group("key")
            elif key == "値":
                current.values_text = value
            elif key == "開始":
                current.start_ts = _parse_ts(value)
            elif key == "終了":
                current.end_ts = _parse_ts(value)
            elif key == "現象":
                current.phenomenon = value
            elif key == "説明":
                current.explanation = value
            elif key == "原因候補":
                current.causes_text = value
            elif key == "同時に発生したアノマリー":
                in_correlations = True
            continue

        if in_correlations and raw.strip().startswith("- "):
            current.correlations.append(raw.strip()[2:])
    return events


# ※CR-002: 「同時に発生したアノマリー」は**観点2 を含むイベントでは
# 項目行ごと出ない** (UI-04-02 の唯一の例外)。したがって必須項目から外し、
# 別途 REQUIRED_ITEMS_POINT で観点1 のイベントだけを確認する。
REQUIRED_ITEMS = [
    "イベント種別", "脅威度", "データ", "値", "開始", "終了",
    "現象", "説明", "原因候補",
]

REQUIRED_ITEMS_POINT = REQUIRED_ITEMS + ["同時に発生したアノマリー"]


def missing_items(event):
    """UI-04-02 が要求する項目のうち欠けているものを返す。"""
    return [key for key in REQUIRED_ITEMS if key not in event.items]
