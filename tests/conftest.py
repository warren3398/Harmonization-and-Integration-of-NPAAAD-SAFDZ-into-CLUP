import pytest


@pytest.fixture
def authenticated_client():
    def make_client(app):
        client = app.test_client()
        with client.session_transaction() as session:
            session["logged_in"] = True
        return client
    return make_client


@pytest.fixture
def open_harmonizer(authenticated_client):
    def open_page(page, server, app):
        origin = f"http://127.0.0.1:{server.server_port}"
        name = app.config["SESSION_COOKIE_NAME"]
        cookie = authenticated_client(app).get_cookie(name)
        page.context.add_cookies([{"name": name, "value": cookie.value, "url": origin}])
        page.goto(origin + "/harmonizer")
    return open_page
