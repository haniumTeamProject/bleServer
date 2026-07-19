import bcrypt


# Java의 BCryptPasswordEncoder.encode()에 대응
def hash_password(raw_password: str) -> str:
    hashed = bcrypt.hashpw(raw_password.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


# Java의 BCryptPasswordEncoder.matches()에 대응
def verify_password(raw_password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(raw_password.encode("utf-8"), password_hash.encode("utf-8"))
