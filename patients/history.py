"""The medical checklist: which master list each part uses, and the alerts shown on every patient screen."""
from django.db.models import Q

from masterdata.models import ListValue

LISTS = {
    "conditions": "medical-conditions", "allergies": "allergies", "medicines": "flagged-medications", "habits": "habits",
}
# Conditions that change how treatment is planned, so they appear in the red banner.
# Infections such as hepatitis and HIV are deliberately left out: standard precautions apply to everyone and status is confidential.
ALERT_CONDITIONS = {
    "bleeding-disorder", "valve-disease", "pacemaker", "pregnancy", "diabetes", "hypertension", "heart-disease",
    "epilepsy", "asthma", "cancer-chemo", "immunosuppressed", "joint-replacement",
}


def labels_for(clinic_id):
    """{list key: {code: label}} for the lists the checklist uses."""
    rows = ListValue.objects.filter(
        Q(clinic__isnull=True) | Q(clinic_id=clinic_id), list__key__in=LISTS.values(),
    ).values_list("list__key", "code", "label")
    out = {key: {} for key in LISTS.values()}
    for key, code, label in rows:
        out[key][code] = label
    return out


def latest(patient, request=None):
    row = patient.history_versions.select_related("reviewed_by").first()
    empty = {"exists": False, "id": None, "conditions": [], "allergies": [], "medicines": [], "habits": [], "notes": "",
             "reviewed_by_name": "", "reviewed_at": None, "versions": 0, "alerts": []}
    if row is None:
        return empty
    labels = labels_for(patient.clinic_id)
    name = lambda part, code: labels[LISTS[part]].get(code, code)
    alerts = (
        [{"kind": "allergy", "code": c, "label": name("allergies", c)} for c in row.allergies]
        + [{"kind": "medicine", "code": c, "label": name("medicines", c)} for c in row.medicines]
        + [{"kind": "condition", "code": c, "label": name("conditions", c)} for c in row.conditions if c in ALERT_CONDITIONS]
    )
    return {
        "exists": True, "id": row.id, "conditions": row.conditions, "allergies": row.allergies, "medicines": row.medicines,
        "habits": row.habits, "notes": row.notes,
        "reviewed_by_name": (row.reviewed_by.name or row.reviewed_by.phone) if row.reviewed_by else "",
        "reviewed_at": row.created_at, "versions": patient.history_versions.count(), "alerts": alerts,
    }
