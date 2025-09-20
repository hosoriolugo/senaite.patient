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

from bika.lims import api
from bika.lims.utils import get_link
from plone.memoize.instance import memoize
from plone.memoize.view import memoize as viewcache
from senaite.app.listing.interfaces import IListingView
from senaite.app.listing.interfaces import IListingViewAdapter
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
    """Ensure value is a plain string, never a dict or None."""
    if isinstance(value, dict):
        for key in ("mrn", "MRN", "value", "text", "label", "title", "Title"):
            v = value.get(key)
            if isinstance(v, basestring) and v.strip():
                return api.safe_unicode(v.strip())
        return u""
    if isinstance(value, basestring):
        return api.safe_unicode(value.strip())
    return u""


# Statuses to add
ADD_STATUSES = [{
    "id": "temp_mrn",
    "title": _("Temporary MRN"),
    "contentFilter": {
        "is_temporary_mrn": True,
        "sort_on": "created",
        "sort_order": "descending",
    },
    "before": "to_be_verified",
    "transitions": [],
    "custom_transitions": [],
}]

# Columns to add
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
    """Generic adapter for sample listings (MRN + Patient 4-field schema)"""
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
        """Inject MRN and Patient fullname into the listing rows"""
        if self.show_icon_temp_mrn and getattr(obj, "isMedicalRecordTemporary", False):
            after_icons = item["after"].get("getId", "")
            kwargs = {"width": 16, "title": _("Temporary MRN")}
            after_icons += self.icon_tag("id-card-red", **kwargs)
            item["after"].update({"getId": after_icons})

        # Defaults
        item["MRN"] = ""
        item["Patient"] = _("No patient")

        # Resolve patient
        patient = getattr(obj, "getPatient", lambda: None)()
        if not patient:
            return item

        # --- MRN ---
        mrn = _normalize_value(getattr(patient, "mrn", None))
        if not mrn:
            mrn = _normalize_value(getattr(patient, "MedicalRecordNumber", None))
        item["MRN"] = mrn

        # --- Full name (4 fields) ---
        parts = []
        for fld in ("firstname", "middlename", "lastname", "maternal_lastname"):
            val = getattr(patient, fld, None)
            val = _normalize_value(val)
            if val:
                parts.append(val)
        fullname = u" ".join(parts)
        item["Patient"] = fullname if fullname else _("No patient")

        # Link MRN and Patient
        if mrn:
            patient_url = api.get_url(patient)
            item["replace"]["MRN"] = get_link(patient_url, mrn)

            patient_view_url = "{}/@@view".format(patient_url)
            item["Patient"] = get_link(patient_view_url, item["Patient"])

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
