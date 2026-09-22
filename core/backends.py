from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import ValidationError

class EmailBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        try:
            user = UserModel.objects.get(email=username,is_active=True)
        except UserModel.DoesNotExist:
            raise ValidationError("User with this email does not exist (or) active status.")
        else:
            if user.check_password(password):
                return user
            else:
                raise ValidationError("Incorrect password.")
        return None