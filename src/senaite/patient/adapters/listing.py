# -*- coding: utf-8 -*-
#
# This file is part of SENAITE.PATIENT.
#
# SENAITE.PATIENT is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, version 2.
#
# SENAITE.PATIENT is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License
# for more details.
#
# ------------------------------------------------------------------------
# Adjusted: robust MRN/Patient resolution so listings never show empty values
# ------------------------------------------------------------------------

# -*- coding: utf-8 -*-
from bika.lims import api
from bika.lims.utils import get_link
from plone.memoize.instance import memoize
from plone.memoize.view import memoize as viewcache
from senaite.app.listing.interfaces import IListingView, IListingViewAdapter
from senaite.app.listing.utils import add_column, add_review_state
from senaite.patient import check_installed
from senaite.patient import messageFactory as _
from senaite.patient.api import get_patient_by_mrn
from zope.component import adapts, getMultiAdapter
from zope.interface import implements

try:
    basestring
except NameError:
    basestring = str


def _normalize_value(value):
    if isinstance(value, dict):
        for key in ("mrn", "MRN", "value", "text", "label", "title", "Title"):
            v = value.get(key)
            if isinstance(v, basestring) and v.strip():
                return api.safe_unicode(v.strip())
        return u""
    if isinstance(value, basestring):
        return api.safe_unicode(value.strip())
    if value is None:
        return u""
    return api.safe_unicode(str(value))


def _get_mrn_from_obj(obj):
    """Lee MRN desde el campo 'MedicalRecordNumber' del AR."""
    if not hasattr(obj, "getField"):
        return u""
    field = obj.getField("MedicalRecordNumber")
    if not field:
        return u""
    try:
        raw = field.get(obj)
    except Exception:
        return u""
    return _normalize_value(raw)


# Estado extra (opcional)
ADD_STATUSES = [{
    "id": "temp_mrn",
    "title": _("Temporary MRN"),
    "contentFilter": {
        "is_temporary_mrn": True,  # BooleanIndex del catálogo
        "sort_on": "created",
        "sort_order": "descending",
    },
    "before": "to_be_verified",
    "transitions": [],
    "custom_transitions": [],
}]

# Columnas extra
ADD_COLUMNS = [
    ("Patient", {
        "title": _("Patient"),
        "sortable": False,
        "after": "getId",
    }),
    ("MRN", {
        "title": _("MRN"),
        "sortable": False,
        "index": "medical_record_number",
        "after": "getId",
    }),
]


class SamplesListingAdapter(object):
    """Adapter para listings de muestras con MRN + Patient 4 campos."""
    adapts(IListingView)
    implements(IListingViewAdapter)

    priority_order = 99999

    def __init__(self, listing, context):
        self.listing = listing
        self.context = context

    @property
    @memoize
    def senaite_theme(self):
        return getMultiAdapter(
            (self.context, self.listing.request),
            name="senaite_theme")

    def icon_tag(self, name, **kwargs):
        return self.senaite_theme.icon_tag(name, **kwargs)

    @property
    @memoize
    def show_icon_temp_mrn(self):
        return api.get_registry_record("senaite.patient.show_icon_temp_mrn")

    @check_installed(None)
    def folder_item(self, obj, item, index):
        """Inyecta MRN y nombre del paciente a cada fila del listing."""
        if self.show_icon_temp_mrn and getattr(obj, "isMedicalRecordTemporary", False):
            after_icons = item["after"].get("getId", "")
            kwargs = {"width": 16, "title": _("Temporary MRN")}
            after_icons += self.icon_tag("id-card-red", **kwargs)
            item["after"].update({"getId": after_icons})

        item["MRN"] = ""
        item["Patient"] = _("No patient")

        # 1) MRN desde el campo (NO getMedicalRecordNumberValue)
        mrn = _get_mrn_from_obj(obj)

        # 2) Paciente desde el AR
        patient = getattr(obj, "getPatient", lambda: None)()

        # 3) Si no hay paciente pero hay MRN, buscar paciente por MRN
        if not patient and mrn:
            patient = self.get_patient_by_mrn(mrn)

        # 4) Si no hay MRN pero hay paciente, obtener MRN del paciente
        if not mrn and patient:
            mrn = _normalize_value(
                getattr(patient, "mrn", None) or
                getattr(patient, "MedicalRecordNumber", None)
            )

        item["MRN"] = mrn

        # 5) Nombre completo del Paciente
        fullname = u""
        if patient:
            for key in ("getFullName", "getPatientFullName", "Title"):
                acc = getattr(patient, key, None)
                if callable(acc):
                    try:
                        fullname = _normalize_value(acc())
                        break
                    except Exception:
                        continue
                elif isinstance(acc, basestring):
                    fullname = _normalize_value(acc)
                    break

            if not fullname:
                # Construcción por partes
                parts = []
                for fld in ("firstname", "middlename", "lastname", "maternal_lastname"):
                    parts.append(_normalize_value(getattr(patient, fld, None)))
                fullname = u" ".join([p for p in parts if p])

        item["Patient"] = fullname if fullname else _("No patient")

        # 6) Enlaces si existe paciente y MRN
        if patient and mrn:
            patient_url = api.get_url(patient)
            item["replace"]["MRN"] = get_link(patient_url, mrn)
            item["replace"]["Patient"] = get_link(patient_url, item["Patient"])

        return item

    @viewcache
    def get_patient_by_mrn(self, mrn):
        if not mrn:
            return None
        if self.is_patient_context():
            return self.context
        return get_patient_by_mrn(mrn)

    @check_installed(None)
    def before_render(self):
        rv_keys = map(lambda r: r["id"], self.listing.review_states)

        for column_id, column_values in ADD_COLUMNS:
            if column_id == "MRN" and self.is_patient_context():
                continue
            add_column(
                listing=self.listing,
                column_id=column_id,
                column_values=column_values,
                after=column_values.get("after", None),
                review_states=rv_keys)

        for status in ADD_STATUSES:
            sid = status.get("id")
            if sid == "temp_mrn" and self.is_patient_context():
                continue
            after = status.get("after", None)
            before = status.get("before", None)
            if not status.get("columns"):
                status.update({"columns": self.listing.columns.keys()})
            add_review_state(self.listing, status, after=after, before=before)

    def is_patient_context(self):
        return api.get_portal_type(self.context) == "Patient"
