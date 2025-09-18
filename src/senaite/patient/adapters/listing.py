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

# Statuses to add. List of dicts
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
    """Generic adapter for sample listings
    """
    adapts(IListingView)
    implements(IListingViewAdapter)

    # Priority order of this adapter over others
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
        """Returns whether an alert icon has to be displayed next to the sample
        id when the Patient assigned to the sample has a temporary Medical
        Record Number (MRN)
        """
        return api.get_registry_record("senaite.patient.show_icon_temp_mrn")

    @check_installed(None)
    def folder_item(self, obj, item, index):
        # Icono para MRN temporal (mantenemos comportamiento nativo)
        try:
            if self.show_icon_temp_mrn and getattr(obj, "isMedicalRecordTemporary", False):
                after_icons = item["after"].get("getId", "")
                kwargs = {"width": 16, "title": _("Temporary MRN")}
                after_icons += self.icon_tag("id-card-red", **kwargs)
                item["after"].update({"getId": after_icons})
        except Exception:
            # No romper listado si algo raro
            pass

        # --- MRN del AR (defensivo) ---
        sample_patient_mrn = u""
        try:
            sample_patient_mrn = api.to_utf8(
                getattr(obj, "getMedicalRecordNumberValue", None),
                default=u"")
        except Exception:
            try:
                sample_patient_mrn = api.to_utf8(
                    getattr(obj, "getMedicalRecordNumber", None),
                    default=u"")
            except Exception:
                sample_patient_mrn = u""

        # --- Nombre del AR (defensivo) ---
        sample_patient_fullname = u""
        try:
            sample_patient_fullname = api.to_utf8(
                getattr(obj, "getPatientFullName", None),
                default=u"")
        except Exception:
            sample_patient_fullname = u""

        # Poner valores base en la fila (aunque estén vacíos → no romper)
        item["MRN"] = sample_patient_mrn or u""
        item["Patient"] = sample_patient_fullname or u""

        # Intentar enlazar al paciente real por MRN
        patient = None
        if sample_patient_mrn:
            try:
                patient = self.get_patient_by_mrn(sample_patient_mrn)
            except Exception:
                patient = None

        if not patient:
            # Sin paciente (o MRN vacío) → dejamos valores planos
            return

        # Enlace a ficha de paciente (defensivo)
        patient_url = None
        try:
            patient_url = api.get_url(patient)
        except Exception:
            patient_url = None

        if patient_url and sample_patient_mrn:
            try:
                item["replace"]["MRN"] = get_link(patient_url, sample_patient_mrn)
            except Exception:
                # Si falla la creación del link, dejamos el texto plano
                item["replace"].pop("MRN", None)

        # Comparativas (con guards)
        try:
            patient_mrn = u""
            try:
                patient_mrn = api.safe_unicode(patient.getMRN() or u"")
            except Exception:
                patient_mrn = u""

            if (sample_patient_mrn or u"") != (patient_mrn or u""):
                msg = _("Patient MRN of sample is not equal to %s")
                val = api.safe_unicode(patient_mrn) or _("<no value>")
                icon_args = {"width": 16, "title": api.to_utf8(msg % val)}
                item["after"]["MRN"] = self.icon_tag("info", **icon_args)
        except Exception:
            pass

        try:
            patient_fullname = u""
            try:
                patient_fullname = api.safe_unicode(patient.getFullname() or u"")
            except Exception:
                patient_fullname = u""

            if (sample_patient_fullname or u"") != (patient_fullname or u""):
                msg = _("Patient fullname of sample is not equal to %s")
                val = patient_fullname or _("<no value>")
                icon_args = {"width": 16, "title": api.to_utf8(msg % val)}
                item["after"]["Patient"] = self.icon_tag("info", **icon_args)
            else:
                if patient_url and sample_patient_fullname:
                    try:
                        patient_view_url = "{}/@@view".format(patient_url)
                        item["Patient"] = get_link(patient_view_url, sample_patient_fullname)
                    except Exception:
                        # Si no podemos linkar, dejamos el texto tal cual
                        pass
        except Exception:
            pass

    @viewcache
    def get_patient_by_mrn(self, mrn):
        if not mrn:
            return None
        if self.is_patient_context():
            return self.context
        return get_patient_by_mrn(mrn)

    @check_installed(None)
    def before_render(self):
        # Additional columns
        rv_keys = map(lambda r: r["id"], self.listing.review_states)
        for column_id, column_values in ADD_COLUMNS:
            # skip MRN column for patient context
            if column_id == "MRN" and self.is_patient_context():
                continue
            add_column(
                listing=self.listing,
                column_id=column_id,
                column_values=column_values,
                after=column_values.get("after", None),
                review_states=rv_keys)

        # Add review_states
        for status in ADD_STATUSES:
            sid = status.get("id")
            # skip temporary MRN for patient context
            if sid == "temp_mrn" and self.is_patient_context():
                continue
            after = status.get("after", None)
            before = status.get("before", None)
            if not status.get("columns"):
                status.update({"columns": self.listing.columns.keys()})
            add_review_state(self.listing, status, after=after, before=before)

    def is_patient_context(self):
        """Check if the current context is a patient
        """
        return api.get_portal_type(self.context) == "Patient"
