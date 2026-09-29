import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

logger = logging.getLogger(__name__)


class EmailBackend(ModelBackend):
    """Signs users in by email.

    Follows ModelBackend's contract: any failure returns None. It used to
    raise ValidationError, which put "no such user" vs "incorrect
    password" into the login form's errors, where any form that showed
    them (Django admin's did) revealed which emails are registered. It
    also returned instantly for an unknown email while a known one paid
    for a password hash, so response time alone told the two apart. The
    hasher now runs either way.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None or password is None:
            return None
        UserModel = get_user_model()
        try:
            user = UserModel.objects.get(email=username)
        except UserModel.DoesNotExist:
            UserModel().set_password(password)  # same cost as a real check
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            logger.info("User %s logged in", user.email)
            return user
        return None
