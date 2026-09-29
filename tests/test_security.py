import sqlite3

from app.database import Database
from app.security import CredentialVault


def test_credentials_are_encrypted_at_rest(tmp_path):
    path = tmp_path / "credentials.db"
    database = Database(str(path))
    database.initialize()
    vault = CredentialVault("test-secret")
    secret = {"access_token": "highly-sensitive-token"}
    database.save_broker_credentials("zerodha", vault.encrypt(secret))
    database.close()

    connection = sqlite3.connect(path)
    stored = connection.execute(
        "SELECT encrypted_credentials FROM broker_connections WHERE broker='zerodha'"
    ).fetchone()[0]
    connection.close()
    assert "highly-sensitive-token" not in stored
    assert vault.decrypt(stored) == secret

