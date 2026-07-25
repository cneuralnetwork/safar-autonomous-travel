from app.config import Settings


def test_cors_origins_accept_render_env_format(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "*")
    assert Settings().cors_origins == ["*"]


def test_cors_origins_accept_comma_separated_values(monkeypatch):
    monkeypatch.setenv(
        "CORS_ORIGINS",
        "https://safar.app, https://admin.safar.app",
    )
    assert Settings().cors_origins == [
        "https://safar.app",
        "https://admin.safar.app",
    ]
