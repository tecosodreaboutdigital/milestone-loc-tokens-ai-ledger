"""Renders one growth-line chart as a static, self-contained SVG
string. Ported unchanged in behaviour from harness-medir's
build/build_logbook.py, already proven across dozens of published
milestone charts there, including the label-thinning logic that
avoids overlapping numbers once a series has many points."""


def svg_growth_chart(marker_prefix, values, x_labels, y_fmt, caption, subcaption, viewbox_h=260):
    width, height = 700, viewbox_h
    left, right, top, bottom = 78, 652, 34, height - 66
    n = len(values)
    max_v = max(values) * 1.12 if max(values) > 0 else 1
    xs = [left + i * (right - left) / (n - 1) if n > 1 else left for i in range(n)]
    ys = [bottom - (v / max_v) * (bottom - top) for v in values]
    points = " ".join("%.1f,%.1f" % (x, y) for x, y in zip(xs, ys))

    parts = []
    parts.append('<svg viewBox="0 0 %d %d" role="img" aria-label="%s">' % (width, height, caption))
    parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#c9c7bf" stroke-width=".7"/>' % (left, bottom, right, bottom))
    parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#c9c7bf" stroke-width=".5" stroke-dasharray="2,3"/>' % (left, top, right, top))
    parts.append('<text class="svg-sub" x="%d" y="%d" text-anchor="end">%s</text>' % (left - 6, top + 4, y_fmt(max_v)))
    parts.append('<text class="svg-sub" x="%d" y="%d" text-anchor="end">0</text>' % (left - 6, bottom + 4))
    parts.append('<polyline points="%s" fill="none" stroke="#1b1b19" stroke-width="1"/>' % points)

    # Value labels collide once neighbouring points sit closer together
    # than the label text is wide. Space labels out instead of dropping
    # one on every point, always keeping the first and last so the
    # series' start and current state are never the ones omitted.
    max_labels = max(2, int((right - left) / 70))
    step = max(1, round((n - 1) / (max_labels - 1))) if n > 1 else 1
    labelled = set(range(0, n, step))
    labelled.add(0)
    labelled = {i for i in labelled if i == n - 1 or (n - 1 - i) >= max(2, step // 2)}
    labelled.add(n - 1)

    x_max_labels = max(2, int((right - left) / 26))
    x_step = max(1, round((n - 1) / (x_max_labels - 1))) if n > 1 else 1
    x_labelled = set(range(0, n, x_step))
    x_labelled.add(0)
    x_labelled.add(n - 1)

    for i, (x, y, v) in enumerate(zip(xs, ys, values)):
        parts.append('<circle cx="%.1f" cy="%.1f" r="3" fill="#1b1b19"/>' % (x, y))
        if i in labelled:
            label_y = y - 10 if y > top + 16 else y + 16
            parts.append('<text class="svg-sub" x="%.1f" y="%.1f" text-anchor="middle">%s</text>' % (x, label_y, y_fmt(v)))
        if i in x_labelled:
            parts.append('<text class="svg-sub" x="%.1f" y="%d" text-anchor="middle">%s</text>' % (x, height - 40, x_labels[i]))

    parts.append('<text class="svg-cap" x="%d" y="%d">%s</text>' % (left, height - 16, subcaption))
    parts.append("</svg>")
    return "\n".join(parts)


def svg_stat_thumbnail(big_value_text, label_text, values, width=320, height=120):
    """A small, self-contained "stat card with a sparkline" SVG: one
    big headline number, its label, and a minimal trend line beneath
    it. Meant to stand on its own outside the dashboard (e.g. embedded
    as a README image), so it carries its own card background and
    never depends on a surrounding stylesheet, unlike svg_growth_chart
    whose <text> elements lean on the dashboard page's own CSS."""
    pad_x, spark_top, spark_bottom = 16, 62, height - 16
    spark_left, spark_right = pad_x, width - pad_x
    n = len(values)
    max_v = max(values) if values else 0
    min_v = min(values) if values else 0
    span = (max_v - min_v) or 1

    def sx(i):
        return spark_left + i * (spark_right - spark_left) / (n - 1) if n > 1 else spark_left

    def sy(v):
        return spark_bottom - (v - min_v) / span * (spark_bottom - spark_top)

    points = " ".join("%.1f,%.1f" % (sx(i), sy(v)) for i, v in enumerate(values))
    last_x, last_y = (sx(n - 1), sy(values[-1])) if values else (spark_left, spark_bottom)

    parts = []
    parts.append(
        '<svg width="%d" height="%d" viewBox="0 0 %d %d" role="img" aria-label="%s">'
        % (width, height, width, height, label_text)
    )
    parts.append(
        '<rect x="0.5" y="0.5" width="%d" height="%d" rx="8" fill="#faf9f6" stroke="#e2e0d8"/>'
        % (width - 1, height - 1)
    )
    parts.append(
        '<text x="%d" y="34" font-family="Arial,sans-serif" font-size="22" font-weight="bold" fill="#1b1b19">%s</text>'
        % (pad_x, big_value_text)
    )
    parts.append(
        '<text x="%d" y="52" font-family="Arial,sans-serif" font-size="11" fill="#8a887f">%s</text>'
        % (pad_x, label_text)
    )
    if points:
        parts.append('<polyline points="%s" fill="none" stroke="#1b1b19" stroke-width="1.5"/>' % points)
        parts.append('<circle cx="%.1f" cy="%.1f" r="2.5" fill="#1b1b19"/>' % (last_x, last_y))
    parts.append("</svg>")
    return "\n".join(parts)
