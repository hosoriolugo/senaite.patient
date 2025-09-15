# -*- coding: utf-8 -*-
#
# This file is part of SENAITE.PATIENT.
#
# SENAITE.PATIENT is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, version 2.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Copyright 2020-2025 by it's authors.
# Some rights reserved, see README and LICENSE.

from bika.lims import api
from bika.lims.utils import get_link
from plone.memoize.instance import memoize
from plone.memoize.view import memoize as viewcache
from senaite.app.listing.interfaces import IListingView
from senaite.app.listing.interfaces import IListingViewAdapter
from senaite.app.listing.utils import add_column
from senaite.app.listing.utils import add_review_state
from senaite.patient import check_installed
from senaite.patient import messageFactory as _
from senaite.patient.api import get_patient_by_mrn
from zope.component import adapts
from zope.component import getMultiAdapter
from zope.interface import implements


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
},
]

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
    """Generic adapter for sample listings
    """
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

    # 🔹 Nuevo: método robusto para MRN
    def getMedicalRecordNumberValue(self, obj, item=None, **kw):
        try:
            real = obj.getObject() if hasattr(obj, "getObject") else obj
        except Exception:
            real = obj

        # fallback si es RequestContainer
        if real.__class__.__name__ == "RequestContainer":
            real = getattr(real, "context", real)

        mrn = u""
        patient = None

        if hasattr(real, "getPatient"):
            try:
                patient = real.getPatient()
            except Exception:
                patient = None
        if not patient and hasattr(real, "getContact"):
            try:
                patient = real.getContact()
            except Exception:
                patient = None

        if patient and hasattr(patient, "getMRN"):
            try:
                mrn = patient.getMRN() or u""
            except Exception:
                mrn = u""

        if not mrn:
            getter = getattr(real, "getMedicalRecordNumber", None)
            if callable(getter):
                try:
                    mrn = getter() or u""
                except Exception:
                    mrn = u""

        return api.to_utf8(mrn) if mrn else u""

    @check_installed(None)
    def folder_item(self, obj, item, index):
        # Icono de MRN temporal
        checker = getattr(obj, "isMedicalRecordTemporary", None)
        is_temp = callable(checker) and checker()
        if self.show_icon_temp_mrn and is_temp:
            after_icons = item["after"].get("getId", "")
            kwargs = {"width": 16, "title": _("Temporary MRN")}
            after_icons += self.icon_tag("id-card-red", **kwargs)
            item["after"]["getId"] = after_icons

        # MRN con método robusto
        sample_patient_mrn = self.getMedicalRecordNumberValue(obj, item=item)
        item["MRN"] = sample_patient_mrn

        # Obtener paciente
        patient = None
        try:
            real = obj.getObject() if hasattr(obj, "getObject") else obj
        except Exception:
            real = obj

        if hasattr(real, "getPatient"):
            try:
                patient = real.getPatient()
            except Exception:
                patient = None

        if not patient and sample_patient_mrn:
            patient = self.get_patient_by_mrn(sample_patient_mrn)

        if not patient:
            item["Patient"] = _("(No patient)")
            return

        # 🔹 Nombre completo desde getFullname (4 campos)
        patient_fullname = patient.getFullname() if hasattr(patient, "getFullname") else api.safe_unicode(patient.Title())
        patient_url = api.get_url(patient)
        patient_view_url = "{}/@@view".format(patient_url)

        item.setdefault("replace", {})
        item["Patient"] = get_link(patient_view_url, patient_fullname)
        if sample_patient_mrn:
            item["replace"]["MRN"] = get_link(patient_url, sample_patient_mrn)

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
