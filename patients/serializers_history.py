from rest_framework import serializers

from masterdata.models import ListValue
from masterdata.services import active_codes

from .history import LISTS
from .models import MedicalHistory


class MedicalHistorySerializer(serializers.ModelSerializer):
    """A review of the checklist. Every code must be an active value of the matching master list."""

    class Meta:
        model = MedicalHistory
        fields = ["conditions", "allergies", "medicines", "habits", "notes"]

    def validate(self, attrs):
        clinic_id = self.context["patient"].clinic_id
        for part, key in LISTS.items():
            codes = attrs.get(part, [])
            if not isinstance(codes, list) or any(not isinstance(c, str) for c in codes):
                raise serializers.ValidationError({part: "Send a list of codes."})
            unknown = sorted(set(codes) - active_codes(ListValue, key, clinic_id))
            if unknown:
                raise serializers.ValidationError({part: f"Not in the list: {', '.join(unknown)}."})
            attrs[part] = list(dict.fromkeys(codes))  # drop repeats, keep the order
        return attrs
