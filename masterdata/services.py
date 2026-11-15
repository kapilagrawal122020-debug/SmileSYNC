"""Reading, exporting and importing the master lists. The seed command, the data migration and the
platform admin's import screen all go through import_lists(), so they follow the same rules."""
import csv
import io
import json
import re
from pathlib import Path

from django.db import transaction
from django.db.models import Q

CODE_RE = re.compile(r"^[A-Za-z0-9._+\-]{1,60}$")
SEED_FILE = Path(__file__).resolve().parent / "seed" / "master_lists.json"


def active_values(ListValue, key, clinic_id=None):
    """Active values of one list for a clinic: global values plus that clinic's own."""
    scope = Q(clinic__isnull=True) | (Q(clinic_id=clinic_id) if clinic_id else Q(pk__in=[]))
    return ListValue.objects.filter(scope, list__key=key, is_active=True).order_by("sort_order", "id")


def active_codes(ListValue, key, clinic_id=None):
    return set(active_values(ListValue, key, clinic_id).values_list("code", flat=True))


def public_lists(ListValue, clinic_id=None, keys=None):
    """What the apps load once after login: {list key: [active values]}."""
    scope = Q(clinic__isnull=True) | (Q(clinic_id=clinic_id) if clinic_id else Q(pk__in=[]))
    qs = ListValue.objects.filter(scope, is_active=True).select_related("list").order_by("list__sort_order", "sort_order", "id")
    if keys:
        qs = qs.filter(list__key__in=keys)
    out = {}
    for v in qs:
        out.setdefault(v.list.key, []).append({
            "code": v.code, "label": v.label, "labels": v.labels, "description": v.description,
            "group": v.group, "metadata": v.metadata,
        })
    return out


def export_list(definition):
    """The same shape the importer reads, so an export can be edited and imported back."""
    values = definition.values.filter(clinic__isnull=True).order_by("sort_order", "id")
    return {
        "list": definition.key, "title": definition.title, "group": definition.group, "kind": definition.kind,
        "used_in": definition.used_in, "stored_today": definition.stored_today, "scope": definition.scope,
        "about": definition.about, "admin_note": definition.admin_note,
        "values": [{
            "code": v.code, "label": v.label, "labels": v.labels, "description": v.description, "group": v.group,
            "sort_order": v.sort_order, "is_active": v.is_active, "is_system": v.is_system, "metadata": v.metadata,
        } for v in values],
    }


def export_csv(definition):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["code", "label", "description", "group", "sort_order", "is_active", "is_system"])
    for v in definition.values.filter(clinic__isnull=True).order_by("sort_order", "id"):
        w.writerow([v.code, v.label, v.description, v.group, v.sort_order, "yes" if v.is_active else "no", "yes" if v.is_system else "no"])
    return out.getvalue()


def validate_payload(payload):
    """Returns a list of problems. Nothing is written when there are any."""
    problems = []
    lists = payload.get("lists") if isinstance(payload, dict) and "lists" in payload else [payload]
    if not isinstance(lists, list) or not lists:
        return ["Nothing to import: expected a list object or {\"lists\": [...]}."]
    for item in lists:
        if not isinstance(item, dict):
            problems.append("Each list must be an object.")
            continue
        key = item.get("key") or item.get("list")
        if not key or not re.match(r"^[a-z0-9\-]{1,60}$", str(key)):
            problems.append(f"List key '{key}' is missing or not a lowercase slug.")
            continue
        seen = set()
        for i, v in enumerate(item.get("values", []), 1):
            code = str(v.get("code", "")).strip()
            if not CODE_RE.match(code):
                problems.append(f"{key}, value {i}: code '{code}' is invalid (letters, digits, . _ + - up to 60 characters).")
            elif code in seen:
                problems.append(f"{key}: code '{code}' appears twice.")
            seen.add(code)
            if not str(v.get("label", "")).strip():
                problems.append(f"{key}, value '{code}': label is required.")
    return problems


@transaction.atomic
def import_lists(payload, ListDefinition, ListValue, overwrite=True, dry_run=False):
    """Create or update lists and global values by code.

    overwrite=False (used by the seed) only adds what is missing and never touches an edited label.
    A code is never renamed and is_system is only set when a value is first created.
    """
    problems = validate_payload(payload)
    if problems:
        return {"ok": False, "problems": problems}
    lists = payload.get("lists") if "lists" in payload else [payload]
    summary = {"ok": True, "lists_created": 0, "lists_updated": 0, "values_created": 0, "values_updated": 0}
    for order, item in enumerate(lists):
        key = item.get("key") or item.get("list")
        fields = {
            "title": item.get("title") or key, "group": item.get("group", ""), "kind": item.get("kind", "clinic"),
            "used_in": item.get("used_in", ""), "stored_today": item.get("stored_today", ""), "scope": item.get("scope", ""),
            "about": item.get("about", ""), "admin_note": item.get("admin_note", ""),
        }
        definition = ListDefinition.objects.filter(key=key).first()
        if definition is None:
            summary["lists_created"] += 1
            if not dry_run:
                definition = ListDefinition.objects.create(key=key, sort_order=item.get("sort_order", order), **fields)
        elif overwrite:
            summary["lists_updated"] += 1
            if not dry_run:
                for k, val in fields.items():
                    setattr(definition, k, val)
                definition.save()
        if definition is None:  # dry run of a new list: count its values as new
            summary["values_created"] += len(item.get("values", []))
            continue
        existing = {v.code: v for v in ListValue.objects.filter(list=definition, clinic__isnull=True)}
        for i, v in enumerate(item.get("values", []), 1):
            code = str(v["code"]).strip()
            data = {
                "label": str(v["label"]).strip(), "labels": v.get("labels") or {}, "description": v.get("description", ""),
                "group": v.get("group", ""), "sort_order": v.get("sort_order", i), "metadata": v.get("metadata") or {},
            }
            current = existing.get(code)
            if current is None:
                summary["values_created"] += 1
                if not dry_run:
                    ListValue.objects.create(list=definition, code=code, is_active=v.get("is_active", True), is_system=bool(v.get("is_system")), **data)
            elif overwrite:
                summary["values_updated"] += 1
                if not dry_run:
                    for k, val in data.items():
                        setattr(current, k, val)
                    if "is_active" in v and (not current.is_system or v["is_active"]):
                        current.is_active = bool(v["is_active"])
                    current.save()
    return summary


def load_seed(ListDefinition, ListValue):
    payload = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    return import_lists(payload, ListDefinition, ListValue, overwrite=False)
