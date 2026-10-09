"""Test-run settings that only make the suite faster.

Django's default password hasher (PBKDF2, 870,000 iterations) takes ~0.3s
per hash by design; tests create users, log in and issue hashed OTP codes
hundreds of times, so it dominated the run. MD5 is what Django's docs
recommend for tests. Never use it outside tests.
"""


def pytest_configure(config):
    from django.conf import settings

    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
