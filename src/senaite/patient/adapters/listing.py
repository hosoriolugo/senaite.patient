# -*- coding: utf-8 -*-
#
# This file is part of SENAITE.PATIENT.
#
# SENAITE.PATIENT is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, version 2.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
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
    """Adapter para mostrar MRN y Paciente en listados de muestras
       Forma nativa: siempre lee desde el Paciente vinculado.
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
        """Pinta MRN y nombre desde el Paciente vinculado.
        Valida consistencia entre muestra y paciente.
        """
        # 1. Icono de MRN temporal
        if self.show_icon_temp_mrn and getattr(obj, "isMedicalRecordTemporary", False):
            after_icons = item["after"].get("getId", "")
            kwargs = {"width": 16, "title": _("Temporary MRN")}
            after_icons += self.icon_tag("id-card-red", **kwargs)
            item["after"].update({"getId": after_icons})

        # 2. Intentar obtener el paciente relacionado
        patient = getattr(obj, "getPatient", lambda: None)()
        if not patient:
            # fallback por MRN en el sample
            sample_mrn = getattr(obj, "getMedicalRecordNumberValue", lambda: "")()
            if sample_mrn:
                patient = get_patient_by_mrn(sample_mrn)

        if not patient:
            item["MRN"] = ""
            item["Patient"] = ""
            return

        # 3. Obtener datos directamente desde el catálogo del paciente
        catalog = api.get_tool("portal_catalog")
        brains = catalog(UID=api.get_uid(patient))
        if brains:
            brain = brains[0]
            patient_mrn = getattr(brain, "patient_mrn", "")
            patient_fullname = getattr(brain, "patient_fullname", "")
        else:
            # fallback a métodos del objeto
            patient_mrn = getattr(patient, "getMRN", lambda: "")()
            patient_fullname = getattr(patient, "getFullname", lambda: "")()

        # 4. Mostrar en columnas con link al paciente
        item["MRN"] = patient_mrn
        patient_url = "{}/@@view".format(api.get_url(patient))
        item["Patient"] = get_link(patient_url, patient_fullname)

        # 5. Validaciones de consistencia
        sample_mrn = getattr(obj, "getMedicalRecordNumberValue", lambda: "")()
        sample_name = getattr(obj, "getPatientFullName", lambda: "")()

        if sample_mrn and sample_mrn != patient_mrn:
            msg = _("Sample MRN does not match patient MRN: %s")
            val = api.safe_unicode(patient_mrn) or _("<no value>")
            icon_args = {"width": 16, "title": api.to_utf8(msg % val)}
            item["after"]["MRN"] = self.icon_tag("info", **icon_args)

        if sample_name and sample_name != patient_fullname:
            msg = _("Sample patient name does not match: %s")
            val = api.safe_unicode(patient_fullname) or _("<no value>")
            icon_args = {"width": 16, "title": api.to_utf8(msg % val)}
            item["after"]["Patient"] = self.icon_tag("info", **icon_args)

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
