from features.auth.password import check_password_strength, hash_password, verify_password


def test_real_hashing_accepts_twenty_character_ascii_password():
    password = "Aa1!" + "x" * 16
    assert len(password) == 20
    assert len(password.encode("utf-8")) == 20
    assert check_password_strength(password) == (True, [])

    hashed_password = hash_password(password)

    assert hashed_password != password
    assert hashed_password.startswith("$2b$12$")
    assert verify_password(password, hashed_password)
    assert not verify_password(password + "y", hashed_password)
