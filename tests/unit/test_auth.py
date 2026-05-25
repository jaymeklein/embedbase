"""Auth unit tests — full implementation in Delivery 2; scaffold here for CI."""
import secrets


def test_key_prefix_format():
    raw_key = "eb_" + secrets.token_urlsafe(32)
    assert raw_key.startswith("eb_")
    key_prefix = raw_key[3:11]
    assert len(key_prefix) == 8


def test_bcrypt_hash_and_verify():
    import bcrypt
    raw_key = "eb_" + secrets.token_urlsafe(32)
    key_hash = bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt(rounds=12))
    assert bcrypt.checkpw(raw_key.encode(), key_hash)
    assert not bcrypt.checkpw(b"wrong_key", key_hash)


def test_master_key_constant_time_compare():
    master = secrets.token_urlsafe(32)
    assert secrets.compare_digest(master, master)
    assert not secrets.compare_digest(master, "not_the_key")
