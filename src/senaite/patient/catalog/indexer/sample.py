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
from bika.lims.interfaces import IAnalysisRequest
from bika.lims.interfaces import IListingSearchableTextProvider
from plone.indexer import indexer
from senaite.core.interfaces import ISampleCatalog
from senaite.patient.interfaces import ISenaitePatientLayer
from zope.component import adapter
from zope.interface import implementer

try:
    basestring
except NameError:
    basestring = str


def _s(v):
    """Unicode seguro desde cualquier tipo “raro”.

    - None -> u""
    - dict -> u"" (evita .strip sobre dicts durante el rebuild)
    - list/tuple/set -> join seguro de sus items
    - demás -> api.safe_unicode(v) o repr(v) como último recurso
    """
    if v is None:
        return u""
    if isinstance(v, dict):
        return u""
    if isinstance(v, (list, tuple, set)):
        try:
            return u" ".join([_s(x) for x in v if x is not None])
        except Exception:
            return u""
    try:
        return api.safe_unicode(v)
    except Exception:
        try:
            return api.safe_unicode(repr(v))
        except Exception:
            return u""


def _get_attr(obj, name):
    if not obj:
        return None
    v = getattr(obj, name, None)
    return v() if callable(v) else v


def _get_patient(ar):
    """Obtiene el paciente desde el AR si existe el método estándar."""
    return _get_attr(ar, "getPatient")


@indexer(IAnalysisRequest)
def is_temporary_mrn(instance):
    """Index booleano: nuevo campo del AR."""
    return bool(getattr(instance, "is_temporary_mrn", False))


# ---------------------------------------------------------------------------
# medical_record_number (KeywordIndex) — para búsquedas/filtrado
# ---------------------------------------------------------------------------
@indexer(IAnalysisRequest)
def medical_record_number(instance):
    """MRN priorizando paciente vinculado; si no, variantes en el propio AR."""
    # 1) Intentar desde Paciente (DX)
    patient = _get_patient(instance)
    if patient is not None:
        for attr in ("getMRN", "mrn", "getMedicalRecordNumber", "MedicalRecordNumber"):
            mrn = _s(_get_attr(patient, attr)).strip()
            if mrn:
                return [mrn]

    # 2) Intentar desde el AR (AT/DX/legacy)
    for attr in ("getMedicalRecordNumber", "MedicalRecordNumber", "medical_record_number"):
        mrn = _s(_get_attr(instance, attr)).strip()
        if mrn:
            return [mrn]

    return []


# ---------------------------------------------------------------------------
# getMedicalRecordNumberValue — lo que muestra la columna "MRN" del listado
# ---------------------------------------------------------------------------
@indexer(IAnalysisRequest)
def getMedicalRecordNumberValue(instance):
    # 1) MRN guardado en el propio AR (cubre variantes)
    for attr in ("getMedicalRecordNumber", "MedicalRecordNumber", "medical_record_number"):
        v = _s(_get_attr(instance, attr)).strip()
        if v:
            return v or None
    # 2) MRN desde el Paciente (cubre variantes)
    patient = _get_patient(instance)
    if patient:
        for attr in ("getMedicalRecordNumber", "MedicalRecordNumber", "mrn", "patient_mrn"):
            v = _s(_get_attr(patient, attr)).strip()
            if v:
                return v or None
    return None


# ---------------------------------------------------------------------------
# getPatientFullName (FieldIndex) — mostrar/ordenar por paciente
# ---------------------------------------------------------------------------
@indexer(IAnalysisRequest)
def getPatientFullName(instance):
    """Nombre completo desde Patient (si hay) o variantes en el AR."""
    # 1) Desde Paciente
    patient = _get_patient(instance)
    if patient is not None:
        for attr in ("getFullname", "getPatientFullName", "PatientFullName", "patient_fullname"):
            name = _s(_get_attr(patient, attr)).strip()
            if name:
                return name

    # 2) Desde el AR (AT/DX/legacy)
    for attr in ("getPatientFullName", "PatientFullName", "patient_fullname"):
        name = _s(_get_attr(instance, attr)).strip()
        if name:
            return name

    return u""


# ---------------------------------------------------------------------------
# getPatientUID — útil para filtros/diagnóstico (opcional)
# ---------------------------------------------------------------------------
@indexer(IAnalysisRequest)
def getPatientUID(instance):
    patient = _get_patient(instance)
    if not patient:
        return None
    if hasattr(patient, "UID"):
        return patient.UID()
    # a veces getPatient devuelve un UID (string)
    if isinstance(patient, basestring) and len(patient) >= 32:
        return patient
    return None


# ---------------------------------------------------------------------------
# listing_searchable_text — añade MRN y nombre a los tokens de búsqueda
# ---------------------------------------------------------------------------
@adapter(IAnalysisRequest, ISenaitePatientLayer, ISampleCatalog)
@implementer(IListingSearchableTextProvider)
class ListingSearchableTextProvider(object):
    """Añade MRN y nombre del paciente a listing_searchable_text."""

    def __init__(self, context, request, catalog):
        self.context = context
        self.request = request
        self.catalog = catalog

    def __call__(self):
        tokens = []

        patient = _get_patient(self.context)

        # MRN (Patient primero; luego variantes en AR)
        mrn_val = None
        if patient is not None:
            for attr in ("getMRN", "mrn", "getMedicalRecordNumber", "MedicalRecordNumber"):
                mrn_val = _get_attr(patient, attr)
                if mrn_val:
                    break
        if not mrn_val:
            for attr in ("getMedicalRecordNumber", "MedicalRecordNumber", "medical_record_number"):
                mrn_val = _get_attr(self.context, attr)
                if mrn_val:
                    break
        mrn = _s(mrn_val).strip()
        if mrn:
            tokens.append(mrn)

        # Nombre (Patient primero; luego variantes en AR)
        name_val = None
        if patient is not None:
            for attr in ("getFullname", "getPatientFullName", "PatientFullName", "patient_fullname"):
                name_val = _get_attr(patient, attr)
                if name_val:
                    break
        if not name_val:
            for attr in ("getPatientFullName", "PatientFullName", "patient_fullname"):
                name_val = _get_attr(self.context, attr)
                if name_val:
                    break
        name = _s(name_val).strip()
        if name:
            tokens.append(name)

        return tokens
