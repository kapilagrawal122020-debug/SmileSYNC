import secrets

from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.hashers import check_password, make_password
from rest_framework import viewsets
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from messaging.whatsapp import send_template
from patients.models import Patient

from .access import OWNER, STAFF, ClinicScopedMixin
from .models import Otp, User
from .serializers import LoginSerializer, OtpVerifySerializer, PhoneSerializer, UserSerializer


def token_response(user):
    token, _ = Token.objects.get_or_create(user=user)
    return Response({"token": token.key, "user": UserSerializer(user).data})


class LoginView(APIView):
    """Staff login: phone + password. Send the token as `Authorization: Token <token>`."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        data = LoginSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user = authenticate(request, username=data.validated_data["phone"], password=data.validated_data["password"])
        if user is None:
            return Response({"detail": "Wrong phone number or password."}, status=401)
        return token_response(user)


class LogoutView(APIView):
    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        return Response(status=204)


class MeView(APIView):
    def get(self, request):
        return Response(UserSerializer(request.user).data)


class OtpRequestView(APIView):
    """Patient login step 1: ask for a code on WhatsApp."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp"

    def post(self, request):
        data = PhoneSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        phone = data.validated_data["phone"]
        response = {"detail": "If this number is registered, a code has been sent on WhatsApp."}

        patient = Patient.objects.filter(phone=phone).first()
        if patient:
            code = f"{secrets.randbelow(10**6):06d}"
            Otp.objects.filter(phone=phone).delete()  # one live code per phone
            Otp.objects.create(phone=phone, code_hash=make_password(code))
            send_template(phone, "login_otp", [code], patient.language)
            if settings.DEBUG:
                response["dev_otp"] = code  # so you can test without WhatsApp
        return Response(response)


class OtpVerifyView(APIView):
    """Patient login step 2: send the code, get a token. Links the phone to all its patient records."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp"

    def post(self, request):
        data = OtpVerifySerializer(data=request.data)
        data.is_valid(raise_exception=True)
        phone, code = data.validated_data["phone"], data.validated_data["code"]

        otp = Otp.objects.filter(phone=phone).first()
        if otp is None or otp.is_expired() or otp.attempts >= 5:
            return Response({"detail": "Invalid or expired code."}, status=400)
        if not check_password(code, otp.code_hash):
            otp.attempts += 1
            otp.save(update_fields=["attempts"])
            return Response({"detail": "Invalid or expired code."}, status=400)
        otp.delete()

        user = User.objects.filter(phone=phone).first()
        if user is None:
            first = Patient.objects.filter(phone=phone).first()
            user = User.objects.create_user(phone=phone, role=User.PATIENT, name=first.name if first else "")
        elif user.role != User.PATIENT:
            return Response({"detail": "Staff must log in with a password."}, status=400)
        Patient.objects.filter(phone=phone, user__isnull=True).update(user=user)
        return token_response(user)


class UserViewSet(ClinicScopedMixin, viewsets.ModelViewSet):
    """Staff of my clinic. Everyone on staff can list them; only the doctor can add or change."""

    queryset = User.objects.all()
    serializer_class = UserSerializer
    clinic_lookup = "clinic"
    read_roles = STAFF
    write_roles = OWNER

    def perform_create(self, serializer):
        serializer.save(clinic=self.request.user.clinic)
