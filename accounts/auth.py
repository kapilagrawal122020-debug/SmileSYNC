from rest_framework.authentication import TokenAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .models import AuthToken


class ClinicTokenAuthentication(TokenAuthentication):
    """`Authorization: Token <key>`. The key belongs to one person signed in for one clinic.
    The clinic of the session is put on the user as `session_clinic_id` for the rest of the request."""

    def authenticate_credentials(self, key):
        try:
            token = AuthToken.objects.select_related("user").get(key=key)
        except AuthToken.DoesNotExist:
            raise AuthenticationFailed("Invalid token.")
        if not token.user.is_active:
            raise AuthenticationFailed("User inactive or deleted.")
        token.user.session_clinic_id = token.clinic_id
        return (token.user, token)


def issue_token(user, clinic=None):
    """The session key for this person at this clinic (the platform admin has no clinic). Reused if one exists."""
    clinic_id = getattr(clinic, "pk", clinic)
    token = AuthToken.objects.filter(user=user, clinic_id=clinic_id).first()
    return token or AuthToken.objects.create(user=user, clinic_id=clinic_id)


def sign_out_everywhere(user):
    AuthToken.objects.filter(user=user).delete()
