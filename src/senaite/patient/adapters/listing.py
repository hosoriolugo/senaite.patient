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
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
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
from senaite.patient.api import get_patient
from zope.component import adapts
from zope.component import getMultiAdapter
from zope.interface import implements


# Columna adicional para MRN
ADD_COLUMNS = [
    {
        "id": "MRN",
        "title": _("MRN"),
        "index": "patient_mrn",
        "sortable": True,
    },
    {
        "id": "Patient",
        "title": _("Patient"),
        "index": "patient_fullname",
        "sortable": True,
    },
]


class SamplesListingAdapter(object):
    """Adapter para inyectar MRN y nombre de paciente real en el listado de muestras
    """

    implements(IListingViewAdapter)
    adapts(IListingView)

    def __init__(self, context):
        self.context = context
        self.request = context.request
        self.view = context

    @viewcache
    def is_installed(self):
        return check_installed()

    @viewcache
    def is_samples_view(self):
        return self.view.view_name in ("samples", "ajax_samples")

    @memoize
    def update_listing(self, listing):
        if not self.is_installed():
            return
        if not self.is_samples_view():
            return

        # Agregar columnas MRN y Paciente
        for col in ADD_COLUMNS:
            add_column(listing, col)

    def before_render(self, listing):
        if not self.is_installed():
            return
        if not self.is_samples_view():
            return
        # nada adicional por ahora

    def folder_item(self, obj, item, index):
        """Aquí resolvemos MRN y Paciente reales desde el objeto Patient vinculado
        """
        if not self.is_installed() or not self.is_samples_view():
            return item

        patient = get_patient(obj)

        if patient:
            try:
                item["MRN"] = patient.getMRN()
            except Exception:
                item["MRN"] = ""

            try:
                # enlace clickeable al paciente
                fullname = patient.getFullname()
                item["Patient"] = get_link(patient, value=fullname)
            except Exception:
                item["Patient"] = ""
        else:
            item["MRN"] = ""
            item["Patient"] = ""

        return item
