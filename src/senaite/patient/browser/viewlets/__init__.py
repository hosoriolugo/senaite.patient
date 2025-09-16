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

from plone.app.layout.viewlets import ViewletBase
from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile
from bika.lims.interfaces import IAnalysisRequest
from senaite.patient import api
from senaite.patient.interfaces import IPatient


def get_patient_from_context(context):
    """Devuelve el paciente desde un AR o directamente.
    Wrapper de compatibilidad porque senaite.patient.api no define get_patient().
    """
    # Si es un AnalysisRequest, intenta con PatientID
    if IAnalysisRequest.providedBy(context):
        mrn = getattr(context, "PatientID", None)
        if mrn:
            return api.get_patient_by_mrn(mrn)
        return None

    # Si ya es un Patient
    if IPatient.providedBy(context):
        return context

    return None


class TemporaryMRNViewlet(ViewletBase):
    """Muestra un aviso cuando el MRN asignado a la muestra es temporal."""

    index = ViewPageTemplateFile("templates/temporary_mrn_viewlet.pt")

    def __init__(self, context, request, view, manager=None):
        super(TemporaryMRNViewlet, self).__init__(
            context, request, view, manager=manager
        )
        self.context = context
        self.request = request
        self.view = view

    def is_visible(self):
        """Determina si el viewlet debe mostrarse o no."""

        # Caso 1: contexto directo
        patient = get_patient_from_context(self.context)
        if patient and hasattr(patient, "getTemporary"):
            return patient.getTemporary()

        # Caso 2: contexto desde la vista
        try:
            if hasattr(self.view, "context"):
                patient = get_patient_from_context(self.view.context)
                if patient and hasattr(patient, "getTemporary"):
                    return patient.getTemporary()
        except Exception:
            pass

        return False
