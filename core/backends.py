import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)


class EmailBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        try:
            user = UserModel.objects.get(email=username, is_active=True)
        except UserModel.DoesNotExist:
            logger.warning("Login attempt for unknown/inactive email: %s", username)
            raise ValidationError("User with this email does not exist (or) active status.")
        else:
            if user.check_password(password):
                logger.info("User %s logged in", user.email)
                return user
            else:
                logger.warning("Incorrect password for %s", username)
                raise ValidationError("Incorrect password.")
        return None
