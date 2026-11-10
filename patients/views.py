from django.db.models import Q
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.access import ALL, CLINICAL, OWNER, STAFF, ClinicScopedMixin, file_response
from inventory.serializers import UseStockSerializer
from inventory.services import change_stock, deduct_for_treatment
from inventory.models import StockMovement
from messaging.services import alert_doctors

from .models import Feedback, Invoice, Patient, PatientImage, Payment, Treatment, Visit
from .serializers import (
    FeedbackSerializer, InvoiceSerializer, PatientImageSerializer, PatientSerializer,
    PaymentSerializer, TreatmentSerializer, VisitSerializer,
)


class PatientViewSet(ClinicScopedMixin, viewsets.ModelViewSet):
    """Register and manage patients. Search with ?search=name-or-phone or ?phone=9876543210."""

    queryset = Patient.objects.all()
    serializer_class = PatientSerializer
    clinic_lookup = "clinic"
    patient_lookup = "user"
    read_roles = ALL
    write_roles = STAFF
    sets_clinic = True
    filter_params = {"phone": "phone"}

    def get_queryset(self):
        qs = super().get_queryset()
        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(phone__icontains=search))
        return qs

    @action(detail=True)
    def selfie(self, request, pk=None):
        return file_response(self.get_object().selfie)


class VisitViewSet(ClinicScopedMixin, viewsets.ModelViewSet):
    """Appointments. Filter with ?date=2026-11-10&patient=3&status=booked."""

    queryset = Visit.objects.select_related("patient", "chair")
    serializer_class = VisitSerializer
    clinic_lookup = "patient__clinic"
    patient_lookup = "patient__user"
    read_roles = ALL
    write_roles = STAFF
    filter_params = {"date": "scheduled_at__date", "patient": "patient", "status": "status", "doctor": "doctor"}


class TreatmentViewSet(ClinicScopedMixin, viewsets.ModelViewSet):
    """Treatment plan and notes. Assistants can view, doctors and consultants can edit."""

    queryset = Treatment.objects.select_related("patient")
    serializer_class = TreatmentSerializer
    clinic_lookup = "patient__clinic"
    read_roles = STAFF
    write_roles = CLINICAL
    filter_params = {"patient": "patient", "visit": "visit", "status": "status"}

    def perform_create(self, serializer):
        serializer.save(doctor=self.request.user)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        """Mark the treatment done and deduct stock using the clinic's usage rules."""
        treatment = self.get_object()
        treatment.mark_done()
        used = deduct_for_treatment(treatment, request.user)
        return Response({"treatment": self.get_serializer(treatment).data, "stock_used": used})

    @action(detail=True, methods=["post"], url_path="use-stock")
    def use_stock(self, request, pk=None):
        """For variable items: the doctor taps how many alginate trays, gloves or masks were used."""
        treatment = self.get_object()
        data = UseStockSerializer(data=request.data, context=self.get_serializer_context())
        data.is_valid(raise_exception=True)
        item = change_stock(
            data.validated_data["item"], -data.validated_data["quantity"],
            StockMovement.TREATMENT, request.user, treatment,
        )
        return Response({"item": item.name, "left": item.quantity})


class PatientImageViewSet(ClinicScopedMixin, viewsets.ModelViewSet):
    """X-rays and photos (multipart upload). Patients can view their own."""

    queryset = PatientImage.objects.all()
    serializer_class = PatientImageSerializer
    clinic_lookup = "patient__clinic"
    patient_lookup = "patient__user"
    read_roles = ALL
    write_roles = STAFF
    filter_params = {"patient": "patient", "visit": "visit", "kind": "kind", "stage": "stage"}

    def perform_create(self, serializer):
        serializer.save(uploaded_by=self.request.user)

    @action(detail=True)
    def file(self, request, pk=None):
        return file_response(self.get_object().file)


class InvoiceViewSet(ClinicScopedMixin, viewsets.ModelViewSet):
    queryset = Invoice.objects.prefetch_related("items", "payments")
    serializer_class = InvoiceSerializer
    clinic_lookup = "patient__clinic"
    patient_lookup = "patient__user"
    read_roles = ALL
    write_roles = STAFF
    filter_params = {"patient": "patient", "visit": "visit"}


class PaymentViewSet(
    ClinicScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """Payments are recorded and never edited (add a new entry to correct a mistake)."""

    queryset = Payment.objects.all()
    serializer_class = PaymentSerializer
    clinic_lookup = "invoice__patient__clinic"
    patient_lookup = "invoice__patient__user"
    read_roles = ALL
    write_roles = STAFF
    filter_params = {"invoice": "invoice"}

    def perform_create(self, serializer):
        serializer.save(received_by=self.request.user)


class FeedbackViewSet(
    ClinicScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """Patients post ratings. The doctor and consultants read them privately and reply."""

    queryset = Feedback.objects.all()
    serializer_class = FeedbackSerializer
    patient_lookup = "patient__user"
    read_roles = CLINICAL | {"patient"}  # private: assistants do not see ratings
    write_roles = CLINICAL
    action_roles = {"create": ALL, "reply": OWNER}
    sets_clinic = False

    def perform_create(self, serializer):
        user = self.request.user
        patient = serializer.validated_data.get("patient")
        if user.role == "patient":
            if patient is None or patient.user_id != user.id:
                raise serializers.ValidationError({"patient": "Choose one of your own patient records."})
        elif patient is None:
            raise serializers.ValidationError({"patient": "This field is required."})
        visit = serializer.validated_data.get("visit")
        if visit and visit.patient_id != patient.id:
            raise serializers.ValidationError({"visit": "This visit belongs to another patient."})
        feedback = serializer.save(clinic=patient.clinic)
        if feedback.rating <= 3:
            alert_doctors(
                feedback.clinic, "low_rating_alert",
                [patient.name, feedback.rating, feedback.comment[:100] or "-"],
            )

    @action(detail=True, methods=["post"])
    def reply(self, request, pk=None):
        feedback = self.get_object()
        text = str(request.data.get("reply", "")).strip()
        if not text:
            raise serializers.ValidationError({"reply": "This field is required."})
        feedback.reply = text
        feedback.save(update_fields=["reply"])
        return Response(self.get_serializer(feedback).data)
