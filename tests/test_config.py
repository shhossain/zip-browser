"""
Unit tests for src/config.py
"""
from types import SimpleNamespace

from src.config import Config


class TestConfig:
    def test_from_args_multiuser(self):
        args = SimpleNamespace(
            zip_paths=["/tmp/a.zip"],
            host="127.0.0.1",
            port=8080,
            debug=True,
            username=None,
            password=None,
        )
        cfg = Config.from_args(args)
        assert cfg.multiuser is True
        assert cfg.host == "127.0.0.1"
        assert cfg.port == 8080
        assert cfg.debug is True
        assert cfg.zip_paths == ["/tmp/a.zip"]

    def test_from_args_legacy_single_user(self):
        args = SimpleNamespace(
            zip_paths=["/tmp/a.zip"],
            host="0.0.0.0",
            port=5000,
            debug=False,
            username="admin",
            password="pass",
        )
        cfg = Config.from_args(args)
        assert cfg.multiuser is False
        assert cfg.username == "admin"
        assert cfg.password == "pass"

    def test_zip_path_backward_compat(self):
        cfg = Config(zip_paths=["/a.zip", "/b.zip"])
        assert cfg.zip_path == "/a.zip"

    def test_zip_path_empty(self):
        cfg = Config(zip_paths=[])
        assert cfg.zip_path is None

    def test_secret_key_stable(self):
        cfg = Config(zip_paths=[])
        k1 = cfg.secret_key
        k2 = cfg.secret_key
        assert k1 == k2
        assert len(k1) > 16

    def test_secret_key_from_env(self, monkeypatch):
        monkeypatch.setenv("ZIP_VIEWER_SECRET_KEY", "my-secret")
        cfg = Config(zip_paths=[])
        assert cfg.secret_key == "my-secret"
