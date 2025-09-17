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
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
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
from senaite.patient.api import get_patient_by_mrn, _normalize_mrn, _extract_fullname
from zope.component import adapts
from zope.component import getMultiAdapter
from zope.interface import implements


# Estados adicionales
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

# Columnas adicionales
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
    """Adapter para listados de muestras con columnas MRN y Paciente
       con normalización defensiva de valores.
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

    @check_installed(None)
    def folder_item(self, obj, item, index):
        """Inserta valores de MRN y Paciente en la fila del listado
           usando siempre valores normalizados.
        """
        # Inicializar estructuras para evitar KeyError
        item.setdefault("replace", {})
        item.setdefault("after", {})

        # Icono de MRN temporal
        if self.show_icon_temp_mrn and getattr(obj, "isMedicalRecordTemporary", False):
            after_icons = item["after"].get("getId", "")
            kwargs = {"width": 16, "title": _("Temporary MRN")}
            after_icons += self.icon_tag("id-card-red", **kwargs)
            item["after"].update({"getId": after_icons})

        # ── Normalizar MRN y nombre ────────────────────────────────
        try:
            raw_mrn = getattr(obj, "getMedicalRecordNumberValue", lambda: None)()
        except Exception:
            raw_mrn = None
        sample_patient_mrn = _normalize_mrn(raw_mrn)

        try:
            raw_name = getattr(obj, "getPatientFullName", lambda: None)()
        except Exception:
            raw_name = None
        sample_patient_fullname = _extract_fullname(raw_name) if raw_name else u""

        # Mostrar valores planos
        item["MRN"] = sample_patient_mrn
        item["Patient"] = sample_patient_fullname

        # Intentar recuperar el paciente real por MRN
        patient = None
        try:
            patient = self.get_patient_by_mrn(sample_patient_mrn)
        except Exception as e:
            api.get_logger("senaite.patient").warn(
                "[listing] get_patient_by_mrn fallo para MRN %r: %s",
                sample_patient_mrn, e)

        if not (patient and hasattr(patient, "absolute_url")):
            return  # dejamos valores planos

        patient_url = api.get_url(patient)

        # MRN con link
        if sample_patient_mrn:
            item["replace"]["MRN"] = get_link(patient_url, sample_patient_mrn)

        # Validación MRN
        try:
            patient_mrn = _normalize_mrn(getattr(patient, "getMRN", lambda: u"")())
        except Exception:
            patient_mrn = u""

        if sample_patient_mrn and sample_patient_mrn != patient_mrn:
            msg = _("Patient MRN of sample is not equal to %s")
            val = patient_mrn or _("<no value>")
            icon_args = {"width": 16, "title": api.to_utf8(msg % val)}
            item["after"]["MRN"] = self.icon_tag("info", **icon_args)

        # Validación nombre
        try:
            patient_fullname = _extract_fullname({
                "firstname": getattr(patient, "getFirstname", lambda: u"")(),
                "middlename": getattr(patient, "getMiddlename", lambda: u"")(),
                "lastname": getattr(patient, "getLastname", lambda: u"")(),
                "maternal_lastname": getattr(patient, "getMaternalLastname", lambda: u"")(),
            })
        except Exception:
            patient_fullname = u""

        if sample_patient_fullname and sample_patient_fullname != patient_fullname:
            msg = _("Patient fullname of sample is not equal to %s")
            val = patient_fullname or _("<no value>")
            icon_args = {"width": 16, "title": api.to_utf8(msg % val)}
            item["after"]["Patient"] = self.icon_tag("info", **icon_args)
        else:
            patient_view_url = "{}/@@view".format(patient_url)
            item["Patient"] = get_link(patient_view_url, sample_patient_fullname)

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
