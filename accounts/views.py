import secrets

from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

import audit.services as audit
from clinic.models import Clinic
from messaging.whatsapp import send_template
from patients.models import Patient

from .access import OWNER, STAFF, ClinicScopedMixin
from .auth import issue_token, sign_out_everywhere
from .models import AuthToken, Otp, User
from .serializers import (
    DetailSerializer, LoginSerializer, OtpRequestSerializer, OtpVerifySerializer, PendingSignupSerializer,
    RegisterSerializer, TokenResponseSerializer, UserSerializer,
)

WRONG_LOGIN = "Wrong phone number, password or clinic."


def token_response(user, clinic=None):
    """The session key for this person at this clinic, plus who they are."""
    user.session_clinic_id = clinic.pk if clinic else None
    if hasattr(user, "_clinic_obj"):
        del user._clinic_obj
    token = issue_token(user, clinic)
    return Response({"token": token.key, "user": UserSerializer(user).data})


class LoginView(APIView):
    """Staff login: phone + password for ONE clinic. The platform admin signs in without a clinic.
    Send the token as `Authorization: Token <token>`."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    @extend_schema(request=LoginSerializer, responses={200: TokenResponseSerializer, 401: DetailSerializer})
    def post(self, request):
        data = LoginSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        phone, clinic_id = data.validated_data["phone"], data.validated_data.get("clinic")
        user = authenticate(request, username=phone, password=data.validated_data["password"])
        if user is None:
            waiting = User.objects.filter(phone=phone, is_active=False, signup_pending=True).first()
            if waiting and waiting.check_password(data.validated_data["password"]) and waiting.clinic_id == clinic_id:
                # only said after the right password and clinic, so it does not reveal who has an account
                return Response({"detail": "Your account is waiting for approval. Your clinic admin will switch it on, then you can log in."}, status=403)
            audit.log("login.failed", request, label=phone)
            return Response({"detail": WRONG_LOGIN}, status=401)
        if user.is_platform_admin:
            clinic = None
        else:
            if clinic_id is None:
                raise ValidationError({"clinic": "Choose your clinic."})
            if user.role == User.PATIENT or user.clinic_id != clinic_id:
                audit.log("login.failed", request, label=phone, reason="wrong clinic")
                return Response({"detail": WRONG_LOGIN}, status=401)
            clinic = user.clinic
        audit.log("login", request, user=user, clinic=clinic)
        return token_response(user, clinic)


class RegisterView(APIView):
    """Sign up for a clinic from the directory. Staff wait for the clinic admin's approval (no token is returned).
    A patient is added to the clinic and then signs in with a WhatsApp code."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "register"

    @extend_schema(request=RegisterSerializer, responses={201: PendingSignupSerializer})
    def post(self, request):
        data = RegisterSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        with transaction.atomic():
            created = data.save()
        if isinstance(created, Patient):
            audit.log("signup.patient", request, obj=created, clinic=created.clinic)
            return Response({"detail": "Registered. Log in with the code we send on WhatsApp.", "pending": False}, status=201)
        audit.log("signup.request", request, user=created, obj=created, clinic=created.clinic)
        return Response({
            "detail": "Request received. Your clinic admin will switch your account on, then you can log in.",
            "pending": True,
        }, status=201)


class LogoutView(APIView):
    @extend_schema(request=None, responses={204: None})
    def post(self, request):
        if isinstance(request.auth, AuthToken):
            request.auth.delete()  # only this session; other clinics stay signed in
        else:
            sign_out_everywhere(request.user)
        return Response(status=204)


class MeView(APIView):
    @extend_schema(responses=UserSerializer)
    def get(self, request):
        return Response(UserSerializer(request.user).data)


class OtpRequestView(APIView):
    """Patient login step 1: ask for a code on WhatsApp. You must be a patient of the clinic you name."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp"

    @extend_schema(request=OtpRequestSerializer, responses=DetailSerializer)
    def post(self, request):
        data = OtpRequestSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        phone = data.validated_data["phone"]
        response = {"detail": "If this number is registered at this clinic, a code has been sent on WhatsApp."}

        patient = Patient.objects.filter(phone=phone, clinic_id=data.validated_data["clinic"]).first()
        if patient:
            code = f"{secrets.randbelow(10**6):06d}"
            Otp.objects.filter(phone=phone).delete()  # one live code per phone
            Otp.objects.create(phone=phone, code_hash=make_password(code))
            send_template(phone, "login_otp", [code], patient.language)
            if settings.DEBUG:
                response["dev_otp"] = code  # so you can test without WhatsApp
        return Response(response)


class OtpVerifyView(APIView):
    """Patient login step 2: send the code, get a token for that clinic. Links the phone to its patient records."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp"

    @extend_schema(request=OtpVerifySerializer, responses={200: TokenResponseSerializer, 400: DetailSerializer})
    def post(self, request):
        data = OtpVerifySerializer(data=request.data)
        data.is_valid(raise_exception=True)
        phone, code, clinic_id = data.validated_data["phone"], data.validated_data["code"], data.validated_data["clinic"]
        invalid = Response({"detail": "Invalid or expired code."}, status=400)

        otp = Otp.objects.filter(phone=phone).first()
        if otp is None or otp.is_expired() or otp.attempts >= 5:
            return invalid
        if not check_password(code, otp.code_hash):
            otp.attempts += 1
            otp.save(update_fields=["attempts"])
            return invalid
        clinic = Clinic.objects.filter(pk=clinic_id, patients__phone=phone).first()
        if clinic is None:
            return invalid  # a code for one clinic does not open another clinic you do not belong to
        otp.delete()

        user = User.objects.filter(phone=phone).first()
        if user is None:
            first = Patient.objects.filter(phone=phone).first()
            user = User.objects.create_user(phone=phone, role=User.PATIENT, name=first.name if first else "")
        elif user.role != User.PATIENT:
            return Response({"detail": "Staff must log in with a password."}, status=400)
        Patient.objects.filter(phone=phone, user__isnull=True).update(user=user)
        audit.log("login.patient", request, user=user, clinic=clinic)
        return token_response(user, clinic)


class UserViewSet(ClinicScopedMixin, viewsets.ModelViewSet):
    """The team of my clinic. Everyone on staff can list it; only the clinic admin can add, change, approve or remove."""

    queryset = User.objects.select_related("clinic")
    serializer_class = UserSerializer
    clinic_lookup = "clinic"
    read_roles = STAFF
    write_roles = OWNER

    def perform_create(self, serializer):
        user = serializer.save(clinic=self.request.user.clinic)
        audit.log("user.create", self.request, obj=user, clinic=user.clinic, role=user.role)

    def _keep_an_admin(self, user, becoming_admin):
        """A clinic must always keep at least one active clinic admin, or nobody could manage it again."""
        if user.role == User.CLINIC_ADMIN and user.is_active and not becoming_admin:
            others = User.objects.filter(clinic=user.clinic, role=User.CLINIC_ADMIN, is_active=True).exclude(pk=user.pk)
            if not others.exists():
                raise ValidationError({"detail": "This is the clinic's only admin. Make someone else a clinic admin first."})

    def perform_update(self, serializer):
        user, me, data = serializer.instance, self.request.user, serializer.validated_data
        if user.pk == me.pk:
            if data.get("is_active") is False:
                raise ValidationError({"is_active": "You cannot turn off your own account."})
            if "role" in data and data["role"] != user.role:
                raise ValidationError({"role": "You cannot change your own role."})
        will_be_admin = data.get("role", user.role) == User.CLINIC_ADMIN and data.get("is_active", user.is_active)
        self._keep_an_admin(user, will_be_admin)
        user = serializer.save()
        if not user.is_active or data.get("password"):
            if user.pk != me.pk:
                sign_out_everywhere(user)  # a switched-off or re-passworded account must log in again
        audit.log("user.update", self.request, obj=user, clinic=user.clinic, fields=sorted(data), active=user.is_active)

    def perform_destroy(self, instance):
        if instance.pk == self.request.user.pk:
            raise ValidationError({"detail": "You cannot remove your own account."})
        self._keep_an_admin(instance, False)
        audit.log("user.delete", self.request, obj=instance, clinic=instance.clinic, name=instance.name or instance.phone)
        instance.delete()

    @extend_schema(request=None, responses=UserSerializer)
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """Switch on a person who signed up for this clinic. They can log in from now on."""
        user = self.get_object()
        if not (user.signup_pending and not user.is_active):
            raise ValidationError({"detail": "This account is not waiting for approval."})
        user.is_active, user.signup_pending = True, False
        user.save(update_fields=["is_active", "signup_pending"])
        audit.log("user.approve", request, obj=user, clinic=user.clinic)
        return Response(UserSerializer(user).data)
