import unittest

from engine.svg_chart import svg_growth_chart, svg_stat_thumbnail


class TestSvgGrowthChart(unittest.TestCase):
    def test_renders_one_circle_per_value(self):
        svg = svg_growth_chart(
            "w", [10, 20, 30], ["M1", "M2", "M3"],
            lambda v: str(int(v)), "test chart", "test subcaption",
        )
        self.assertEqual(svg.count("<circle"), 3)

    def test_includes_the_viewbox(self):
        svg = svg_growth_chart(
            "w", [10, 20], ["M1", "M2"], lambda v: str(int(v)), "cap", "sub",
        )
        self.assertIn('viewBox="0 0 700 260"', svg)

    def test_first_and_last_x_labels_always_present(self):
        svg = svg_growth_chart(
            "w", [1, 2, 3, 4, 5], ["M1", "M2", "M3", "M4", "M5"],
            lambda v: str(int(v)), "cap", "sub",
        )
        self.assertIn(">M1<", svg)
        self.assertIn(">M5<", svg)

    def test_custom_viewbox_height(self):
        svg = svg_growth_chart(
            "t", [1, 2], ["M1", "M2"], lambda v: str(int(v)), "cap", "sub", viewbox_h=200,
        )
        self.assertIn('viewBox="0 0 700 200"', svg)

    def test_declares_the_svg_namespace(self):
        # Without xmlns="http://www.w3.org/2000/svg" on the root <svg>,
        # this still parses as well-formed XML and still serves with
        # the right Content-Type when committed as a standalone .svg
        # file, but a browser loading it via <img src> can refuse to
        # rasterise it at all, showing a broken-image icon instead.
        # Embedding it inline inside an HTML5 page (as the dashboard
        # does) doesn't strictly need this, but every SVG this module
        # produces declares it anyway, so the two never silently drift
        # apart depending on where the output happens to be used.
        svg = svg_growth_chart(
            "w", [10, 20], ["M1", "M2"], lambda v: str(int(v)), "cap", "sub",
        )
        self.assertIn('xmlns="http://www.w3.org/2000/svg"', svg)


class TestSvgStatThumbnail(unittest.TestCase):
    def test_includes_the_big_value_and_label_text(self):
        svg = svg_stat_thumbnail("65.1M", "Tokens consumed", [1, 5, 3, 9])
        self.assertIn(">65.1M<", svg)
        self.assertIn(">Tokens consumed<", svg)

    def test_renders_a_polyline_with_one_point_per_value(self):
        svg = svg_stat_thumbnail("$1.00", "Cost recorded", [0.1, 0.4, 0.9])
        points = svg.split('points="')[1].split('"')[0]
        self.assertEqual(len(points.split(" ")), 3)  # one "x,y" pair per value

    def test_a_single_value_still_renders_without_dividing_by_zero(self):
        svg = svg_stat_thumbnail("3,085", "Words published", [42])
        self.assertIn("<polyline", svg)
        self.assertIn("<circle", svg)

    def test_no_values_renders_the_card_without_a_sparkline(self):
        svg = svg_stat_thumbnail("0", "Words published", [])
        self.assertNotIn("<polyline", svg)
        self.assertIn("<rect", svg)

    def test_includes_the_default_viewbox(self):
        svg = svg_stat_thumbnail("1", "Label", [1, 2])
        self.assertIn('viewBox="0 0 320 120"', svg)

    def test_declares_the_svg_namespace(self):
        # This one matters most of the four: svg_stat_thumbnail is the
        # only function here whose output is always committed as a
        # standalone .svg file (a README thumbnail), never inlined
        # into an HTML5 page that would supply the namespace itself.
        svg = svg_stat_thumbnail("1", "Label", [1, 2])
        self.assertIn('xmlns="http://www.w3.org/2000/svg"', svg)


if __name__ == "__main__":
    unittest.main()
