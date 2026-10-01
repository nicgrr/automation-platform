from automation_control.ui import page


def test_authenticated_page_chrome_has_refresh_button():
    html = page("Test", "<p>Body</p>")
    assert "data-page-refresh" in html
    assert "aria-label='Refresh page'" in html
    assert "location.reload()" in html


def test_login_style_page_does_not_show_refresh_button():
    html = page("Login", "<p>Login</p>", show_nav=False)
    assert "data-page-refresh" not in html
