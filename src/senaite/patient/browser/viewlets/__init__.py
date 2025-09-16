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
    """Obtiene el Patient asociado al contexto de manera segura.
    1) Si es Patient, lo devuelve.
    2) Si es AR/Sample, intenta getPatient() primero (nativo).
    3) Fallback: si hay MRN (PatientID), busca por MRN.
    """
    # Caso: ya es un Patient
    if IPatient.providedBy(context):
        return context

    # Caso: es un AnalysisRequest/Sample
    if IAnalysisRequest.providedBy(context):
        # 1) Intentar accessor nativo getPatient()
        getPatient = getattr(context, "getPatient", None)
        if callable(getPatient):
            try:
                p = getPatient()
                if p and IPatient.providedBy(p):
                    return p
            except Exception:
                # Si falla, seguimos al fallback por MRN
                pass

        # 2) Fallback por MRN (PatientID): accessor o atributo
        mrn = None
        getPatientID = getattr(context, "getPatientID", None)
        if callable(getPatientID):
            try:
                mrn = getPatientID()
            except Exception:
                mrn = None
        if not mrn:
            mrn = getattr(context, "PatientID", None)

        if mrn:
            try:
                # Usa la API existente; permite inactivos por compatibilidad
                return api.get_patient_by_mrn(
                    mrn, full_object=True, include_inactive=True
                )
            except Exception:
                return None

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

    def _is_temp(self, patient):
        """Normaliza el valor de 'Temporary' a booleano."""
        try:
            val = getattr(patient, "getTemporary", lambda: False)()
        except Exception:
            val = False
        return bool(val)

    def is_visible(self):
        """Determina si el viewlet debe mostrarse o no."""
        # Caso 1: contexto directo
        patient = get_patient_from_context(self.context)
        if patient and self._is_temp(patient):
            return True

        # Caso 2: contexto desde la vista (algunos renders pasan por view.context)
        try:
            ctx = getattr(self.view, "context", None)
            if ctx:
                patient = get_patient_from_context(ctx)
                if patient and self._is_temp(patient):
                    return True
        except Exception:
            pass

        return False
