"""A013 — チャート出力の確認 (※CR-009)。

**アプリを外側から起動し、生成物として確かめる。**

| # | 確かめること |
| --- | --- |
| 1 | `charts/` 配下に SVG が出力される |
| 2 | レポートの画像リンクと、実在するファイルが**1 対 1 で対応する** |
| 3 | **リンクは相対パスである**(レポートは別の場所へ移されうる) |
| 4 | **重ね合わせのチャートが実際に出る**(設計上ここは一度取りこぼした) |
| 5 | **同じ入力なら SVG もバイト単位で一致する**(NFR-009) |
| 6 | `enabled = false` で一切出力しない |
| 7 | **サマリは変わらない**(4K トークンの目標を守る。CR-004) |
"""

import os
import re
import tempfile
import unittest

from tests.acceptance import _harness as H

#: Markdown の画像リンク。
IMAGE_LINK = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")

CHART_SETTINGS = (
    "[chart]\nenabled = {enabled}\nmin_severity = {severity}\n"
    "max_per_host = {per_host}\nmax_overlay_per_host = {overlay}\n"
)


def run_with(tmp, label, settings, logs):
    entry = H.make_dist(os.path.join(tmp, "dist-" + label),
                        settings=settings, with_tests=False)
    work = os.path.join(tmp, "work-" + label)
    os.makedirs(work)
    proc = H.run_app(logs, work, entry=entry)
    return proc, work


def chart_files(work):
    directory = os.path.join(work, "charts")
    if not os.path.isdir(directory):
        return []
    return sorted(n for n in os.listdir(directory) if n.endswith(".svg"))


def links_in(work):
    """全ホスト別レポートの画像リンクを集める。"""
    found = []
    for path in H.host_report_paths(work):
        with open(path, "r", encoding="utf-8") as handle:
            found.extend(IMAGE_LINK.findall(handle.read()))
    return sorted(found)


class TestA013Charts(H.BaselineCase):
    @classmethod
    def setUpClass(cls):
        super(TestA013Charts, cls).setUpClass()
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = cls._tmp.name
        on = CHART_SETTINGS.format(enabled="true", severity="FATAL",
                                   per_host=10, overlay=10)
        cls.proc, cls.work = run_with(tmp, "on", on, cls.normal_logs)
        # 再現性の確認用にもう 1 回
        cls.proc2, cls.work2 = run_with(tmp, "on2", on, cls.normal_logs)
        # 無効化した場合
        off = CHART_SETTINGS.format(enabled="false", severity="FATAL",
                                    per_host=10, overlay=10)
        cls.off_proc, cls.off_work = run_with(tmp, "off", off, cls.normal_logs)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    # 1
    def test_01_svg_files_are_written(self):
        self.assertEqual(self.proc.returncode, 0, H.err(self.proc)[-2000:])
        files = chart_files(self.work)
        self.assertTrue(files, "charts/ に SVG が無い")
        self.assertIn("チャートを", H.out(self.proc))

    # 2
    def test_02_links_and_files_correspond_one_to_one(self):
        """**リンク切れも、参照されない孤児ファイルも無いこと。**

        「チャートなし」画像も両方に現れるため、集合比較はそのまま成り立つ。
        """
        linked = set(os.path.basename(p) for p in links_in(self.work))
        present = set(chart_files(self.work))
        self.assertEqual(linked, present,
                         "リンクとファイルが一致しない: 片方にしか無い {0}".format(
                             sorted(linked ^ present)))

    # 3
    def test_03_links_are_relative(self):
        """**絶対パスにしてはならない。** レポートは別の場所へ移されうる。"""
        for link in links_in(self.work):
            self.assertFalse(link.startswith("/"), link)
            self.assertFalse(link.startswith("\\"), link)
            self.assertNotIn(":", link, link)
            self.assertTrue(link.startswith("charts/"), link)

    def test_04_linked_files_actually_exist(self):
        for link in links_in(self.work):
            self.assertTrue(os.path.isfile(os.path.join(self.work, link)), link)

    # 4
    def test_05_overlay_charts_are_actually_produced(self):
        """**重ね合わせが 1 枚も出ないという事態を防ぐ。**

        `SEVERE` は定義上「観点2 の検知」であり、観点2 のイベントには
        同時アノマリーが付かない(CR-002)。脅威度順だけで対象を選ぶと
        **重ね合わせが構造的に一度も描かれない。** 実際に一度そうなった。
        """
        overlays = 0
        for name in chart_files(self.work):
            with open(os.path.join(self.work, "charts", name),
                      "r", encoding="utf-8") as handle:
                if "同時に発生したアノマリー" in handle.read():
                    overlays += 1
        self.assertGreater(overlays, 0, "重ね合わせのチャートが 1 枚も無い")

    def test_06_svg_is_wellformed(self):
        """**折れ線を持つのはイベントのチャートだけ。**

        「チャートなし」画像(`no-chart.svg`)は文字だけであり、
        `<polyline>` を持たない。
        """
        for name in chart_files(self.work):
            with open(os.path.join(self.work, "charts", name),
                      "r", encoding="utf-8") as handle:
                text = handle.read()
            self.assertTrue(text.startswith("<svg "), name)
            self.assertTrue(text.rstrip().endswith("</svg>"), name)
            if name == "no-chart.svg":
                self.assertIn("[NO CHART]", text)
            else:
                self.assertIn("<polyline", text, name)

    # 5
    def test_07_svg_is_reproducible(self):
        """**同じ入力なら SVG もバイト単位で一致する**(NFR-009)。"""
        self.assertEqual(chart_files(self.work), chart_files(self.work2))
        for name in chart_files(self.work):
            with open(os.path.join(self.work, "charts", name), "rb") as a:
                with open(os.path.join(self.work2, "charts", name), "rb") as b:
                    self.assertEqual(a.read(), b.read(), name)

    # 6
    def test_08_disabled_writes_nothing(self):
        self.assertEqual(self.off_proc.returncode, 0,
                         H.err(self.off_proc)[-2000:])
        self.assertEqual(chart_files(self.off_work), [])
        self.assertEqual(links_in(self.off_work), [])
        self.assertIn("チャートの出力は無効です", H.out(self.off_proc))

    # 7
    def test_09_summary_is_unchanged_by_charts(self):
        """**サマリにチャートは入らない。** 4K トークンの目標を守る(CR-004)。"""
        with_charts = H.strip_volatile(H.read_summary(self.work))
        without = H.strip_volatile(H.read_summary(self.off_work))
        self.assertEqual(with_charts, without)
        self.assertNotIn("![", with_charts)

    def test_10_summary_still_fits_4k(self):
        for path in H.summary_paths(self.work):
            self.assertLessEqual(os.path.getsize(path), 4000, path)

    def test_11_charts_do_not_bloat_the_host_reports(self):
        """本文に入るのは 1 行のリンクだけであること。

        **図そのものは別ファイルである。** レポート本文が肥大すると、
        大きなコンテキストのモデルでも読めなくなる。
        """
        added = (len(H.read_report(self.work))
                 - len(H.read_report(self.off_work)))
        links = len(links_in(self.work))
        self.assertGreater(links, 0)
        # 1 リンクあたり 200 バイトも増えていないこと
        self.assertLess(added, links * 200,
                        "本文が {0} バイト増えた (リンク {1} 本)".format(added, links))


    # ※CR-009(2026-09-06 の指示)
    def test_12_every_event_has_an_image(self):
        """**チャートが無いイベントにも画像を出す。**

        図が無いと「ツールの不備」に見えるため。
        **イベント数と画像リンク数が一致すること。**
        """
        text = H.read_report(self.work)
        events = text.count("\n# イベントID(")
        images = len(IMAGE_LINK.findall(text))
        self.assertGreater(events, 0)
        self.assertEqual(images, events,
                         "イベント {0} 件に対し画像 {1} 件".format(events, images))

    def test_13_placeholder_is_a_single_shared_file(self):
        """**「チャートなし」画像は 1 つだけ作り、全イベントが同じものを参照する。**"""
        directory = os.path.join(self.work, "charts")
        self.assertTrue(os.path.isfile(os.path.join(directory, "no-chart.svg")))
        refs = [p for p in links_in(self.work) if p.endswith("no-chart.svg")]
        self.assertTrue(refs, "「チャートなし」画像が参照されていない")
        # 参照先はすべて同一のパスであること
        self.assertEqual(set(refs), set(["charts/no-chart.svg"]))

    def test_14_placeholder_does_not_explain_the_reason(self):
        """理由は書かない(分量に効くため)。"""
        with open(os.path.join(self.work, "charts", "no-chart.svg"),
                  "r", encoding="utf-8") as handle:
            svg = handle.read()
        self.assertIn("[NO CHART]", svg)
        for word in ("脅威度", "上限", "点数"):
            self.assertNotIn(word, svg, word)

    def test_15_disabled_shows_no_placeholder_either(self):
        """**無効なら画像を一切出さない。** 誰も図を期待しないため。"""
        self.assertEqual(links_in(self.off_work), [])
        self.assertFalse(os.path.isdir(os.path.join(self.off_work, "charts")))


if __name__ == "__main__":
    unittest.main()
