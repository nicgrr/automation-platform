from automation_control.charts import bar_list, donut_chart, donut_legend, gauge_chart


def test_donut_chart_draws_one_arc_per_segment_plus_the_background_track():
    svg = donut_chart([("Pokemon", 80), ("One Piece", 20)])
    assert svg.count("<circle") == 3  # background track + 2 segments


def test_donut_legend_shows_correct_percentages():
    legend = donut_legend([("Pokemon", 75), ("One Piece", 25)])
    assert "75%" in legend
    assert "25%" in legend
    assert "Pokemon" in legend


def test_gauge_chart_clamps_out_of_range_percentages():
    assert "100%" in gauge_chart(150)
    assert "0%" in gauge_chart(-20)
    assert "42%" in gauge_chart(42.3)


def test_gauge_chart_includes_its_label():
    svg = gauge_chart(60, label="Monthly goal")
    assert "Monthly goal" in svg


def test_bar_list_scales_relative_to_the_largest_value():
    html = bar_list([("Whatnot", 100), ("eBay", 50)])
    assert "width:100%" in html
    assert "width:50%" in html
    assert "$100.00" in html
    assert "$50.00" in html


def test_bar_list_handles_empty_input():
    assert "No data yet" in bar_list([])


def test_bar_list_non_money_mode_formats_as_plain_numbers():
    html = bar_list([("Available", 10), ("Listed", 3)], money=False)
    assert "10" in html
    assert "$" not in html
