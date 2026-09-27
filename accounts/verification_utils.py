import hashlib
import secrets


def generate_secure_token(length=32):
    return secrets.token_urlsafe(length)


def generate_otp():
    return str(secrets.randbelow(900000) + 100000)


def hash_otp(otp):
    return hashlib.sha256(str(otp).encode('utf-8')).hexdigest()
