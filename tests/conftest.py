import pytest


@pytest.fixture
def authenticated_client():
    def make_client(app):
        client = app.test_client()
        with client.session_transaction() as session:
            session["logged_in"] = True
        return client
    return make_client
