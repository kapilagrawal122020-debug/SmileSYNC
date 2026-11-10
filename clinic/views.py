from datetime import date, timedelta

from django.db.models import Avg, Sum
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.access import OWNER, RolePermission, ClinicScopedMixin, file_response
from hygiene.models import HygieneCheck
from inventory.services import stock_alerts
from patients.models import Feedback, InvoiceItem, Payment, Visit

from .models import Chair, Clinic, ClinicDocument
from .serializers import ChairSerializer, ClinicSerializer, DocumentSerializer


class ClinicViewSet(
    ClinicScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Clinic profile. Staff see their own clinic, patients see the clinics they visit."""

    queryset = Clinic.objects.all()
    serializer_class = ClinicSerializer
    clinic_lookup = "id"
    patient_lookup = "patients__user"
    read_roles = {"doctor", "consultant", "assistant", "patient"}
    write_roles = OWNER


class ChairViewSet(ClinicScopedMixin, viewsets.ModelViewSet):
    queryset = Chair.objects.all()
    serializer_class = ChairSerializer
    sets_clinic = True
    write_roles = OWNER


class DocumentViewSet(ClinicScopedMixin, viewsets.ModelViewSet):
    """Clinic vault: registrations and authorisations. Doctor only."""

    queryset = ClinicDocument.objects.all()
    serializer_class = DocumentSerializer
    sets_clinic = True
    read_roles = OWNER
    write_roles = OWNER

    @action(detail=False)
    def expiring(self, request):
        """Documents that expire within 60 days (or already have)."""
        limit = date.today() + timedelta(days=60)
        docs = self.get_queryset().filter(expiry_date__isnull=False, expiry_date__lte=limit)
        return Response(DocumentSerializer(docs, many=True, context={"request": request}).data)

    @action(detail=True)
    def file(self, request, pk=None):
        return file_response(self.get_object().file)


class DashboardView(APIView):
    """One-call summary for the doctor's home screen."""

    permission_classes = [RolePermission]
    read_roles = OWNER
    write_roles = OWNER

    def get(self, request):
        clinic = request.user.clinic
        today = date.today()
        month_ago = today - timedelta(days=30)
        alerts = stock_alerts(clinic)

        billed = InvoiceItem.objects.filter(invoice__patient__clinic=clinic).aggregate(t=Sum("amount"))["t"] or 0
        paid = Payment.objects.filter(invoice__patient__clinic=clinic).aggregate(t=Sum("amount"))["t"] or 0
        rating = Feedback.objects.filter(clinic=clinic, created_at__date__gte=month_ago).aggregate(a=Avg("rating"))["a"]

        return Response({
            "visits_today": Visit.objects.filter(patient__clinic=clinic, scheduled_at__date=today).count(),
            "low_stock_items": len(alerts["low_stock"]),
            "expiring_stock_items": len(alerts["expiring"]),
            "documents_expiring_in_60_days": ClinicDocument.objects.filter(
                clinic=clinic, expiry_date__isnull=False, expiry_date__lte=today + timedelta(days=60)
            ).count(),
            "hygiene_photos_to_review": HygieneCheck.objects.filter(clinic=clinic, status="pending").count(),
            "locked_chairs": Chair.objects.filter(clinic=clinic, status="locked").count(),
            "dues": billed - paid,
            "average_rating_30_days": round(rating, 1) if rating else None,
        })
