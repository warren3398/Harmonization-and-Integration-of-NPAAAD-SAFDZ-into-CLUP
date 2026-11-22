import app as module
from werkzeug.security import generate_password_hash


def test_harmonizer_requires_login_for_page_and_processing_routes():
    client = module.app.test_client()
    assert client.get("/eo79").status_code == 200
    for path in ("/", "/harmonizer", "/elu-review/example", "/download/example"):
        response = client.get(path)
        assert response.status_code == 302
        assert response.location.startswith("/login?")
    for path in ("/api/gis-status", "/progress/example"):
        assert client.get(path).status_code == 401
    for path in ("/inspect", "/process_start"):
        assert client.post(path).status_code == 401


def test_valid_password_opens_harmonizer_until_logout(monkeypatch):
    monkeypatch.setattr(module, "LOGIN_USERNAME", "test-operator")
    monkeypatch.setattr(module, "LOGIN_PASSWORD_HASH", generate_password_hash("test-password", method="pbkdf2:sha256:1000"))
    client = module.app.test_client()
    response = client.post("/login", data={"username": "test-operator", "password": "incorrect"})
    assert b"Invalid username or password" in response.data
    assert client.get("/harmonizer").status_code == 302
    response = client.post("/login", data={"username": "test-operator", "password": "test-password"})
    assert response.location == "/harmonizer"
    assert client.get("/harmonizer").status_code == 200
    client.get("/logout")
    assert client.get("/harmonizer").status_code == 302


def test_portal_login_returns_to_homepage(monkeypatch):
    monkeypatch.setattr(module, "LOGIN_USERNAME", "test-operator")
    monkeypatch.setattr(module, "LOGIN_PASSWORD_HASH", generate_password_hash("test-password", method="pbkdf2:sha256:1000"))
    client = module.app.test_client()
    page = client.get("/", follow_redirects=True)
    assert b"ALMED GIS Tools Portal" in page.data
    response = client.post("/login", data={"username": "test-operator", "password": "test-password", "next": "/"})
    assert response.location == "/"
    assert client.get("/").status_code == 200
    assert client.get("/harmonizer").status_code == 200
    client.get("/logout")
    assert client.get("/").status_code == 302
