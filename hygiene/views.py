from datetime import timedelta

from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.access import OWNER, ClinicScopedMixin, file_response
from clinic.models import Chair

from .models import AFTER_PATIENT, AFTER_PATIENT_AREAS, HygieneCheck
from .serializers import HygieneCheckSerializer


def unlock_chair_if_complete(visit):
    """The chair stays Locked until all 4 after-patient photos are in (redo-requested ones don't count)."""
    if not visit.chair_id:
        return
    done = visit.hygiene_checks.exclude(status=HygieneCheck.REDO).values("area").distinct().count()
    if done >= len(AFTER_PATIENT_AREAS):
        Chair.objects.filter(pk=visit.chair_id, status=Chair.LOCKED).update(status=Chair.READY)


class HygieneCheckViewSet(
    ClinicScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """Photo-verified hygiene checks (multipart upload). Assistants upload, the doctor reviews.

    Filter with ?kind=after_patient&status=pending&visit=5
    """

    queryset = HygieneCheck.objects.all()
    serializer_class = HygieneCheckSerializer
    sets_clinic = True
    read_roles = {"doctor", "assistant"}
    write_roles = {"doctor", "assistant"}
    action_roles = {"review": OWNER, "score": OWNER}
    filter_params = {"kind": "kind", "status": "status", "visit": "visit"}

    def perform_create(self, serializer):
        check = serializer.save(clinic=self.request.user.clinic, taken_by=self.request.user)
        if check.kind == AFTER_PATIENT:
            unlock_chair_if_complete(check.visit)

    @extend_schema(request=inline_serializer("HygieneReview", {
        "status": serializers.ChoiceField(choices=["approved", "redo"]),
        "note": serializers.CharField(required=False),
    }), responses=HygieneCheckSerializer)
    @action(detail=True, methods=["post"])
    def review(self, request, pk=None):
        """Doctor approves a photo or asks for a redo: {"status": "approved" | "redo", "note": "..."}."""
        check = self.get_object()
        status = request.data.get("status")
        if status not in (HygieneCheck.APPROVED, HygieneCheck.REDO):
            raise serializers.ValidationError({"status": "Use 'approved' or 'redo'."})
        check.status = status
        check.review_note = str(request.data.get("note", ""))[:200]
        check.reviewed_by = request.user
        check.reviewed_at = timezone.now()
        check.save()
        return Response(self.get_serializer(check).data)

    @extend_schema(
        parameters=[OpenApiParameter("days", int, description="Look-back period, default 30")],
        responses=inline_serializer("HygieneScore", {
            "days": serializers.IntegerField(),
            "pending": serializers.IntegerField(),
            "approved": serializers.IntegerField(),
            "redo": serializers.IntegerField(),
            "total": serializers.IntegerField(),
            "score": serializers.IntegerField(allow_null=True),
        }),
    )
    @action(detail=False)
    def score(self, request):
        """Compliance score: share of submitted photos the doctor approved (default last 30 days)."""
        try:
            days = int(request.query_params.get("days", 30))
        except ValueError:
            raise serializers.ValidationError({"days": "Must be a number."})
        checks = self.get_queryset().filter(taken_at__gte=timezone.now() - timedelta(days=days))
        counts = {s: checks.filter(status=s).count() for s in (HygieneCheck.PENDING, HygieneCheck.APPROVED, HygieneCheck.REDO)}
        total = sum(counts.values())
        return Response({
            "days": days, **counts, "total": total,
            "score": round(100 * counts[HygieneCheck.APPROVED] / total) if total else None,
        })

    @extend_schema(responses={(200, "application/octet-stream"): OpenApiTypes.BINARY})
    @action(detail=True)
    def photo(self, request, pk=None):
        return file_response(self.get_object().photo)
