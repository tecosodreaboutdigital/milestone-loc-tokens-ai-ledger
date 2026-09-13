import unittest

from engine.svg_chart import svg_growth_chart


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


if __name__ == "__main__":
    unittest.main()
