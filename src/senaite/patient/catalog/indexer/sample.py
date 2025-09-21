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
from senaite.core.interfaces.analysis import IRequestAnalysis  # <-- soporte 2.6+
from senaite.patient.interfaces import ISenaitePatientLayer
from zope.component import adapter
from zope.interface import implementer

try:
    basestring
except NameError:
    basestring = str


def _s(v):
    try:
        return api.safe_unicode(v) if v is not None else u""
    except Exception:
        return u""


def _get_attr(obj, name):
    if not obj:
        return None
    v = getattr(obj, name, None)
    return v() if callable(v) else v


def _get_patient(ar):
    """Usa SOLO el método/campo actual para obtener el paciente desde el AR."""
    return _get_attr(ar, "getPatient")


@indexer(IAnalysisRequest)
def is_temporary_mrn(instance):
    """Index booleano: nuevo campo del AR."""
    return bool(getattr(instance, "is_temporary_mrn", False))


# ---------------------------------------------------------------------------
# Index legacy 'medical_record_number' (KeywordIndex) que ya tenías
# ---------------------------------------------------------------------------
@indexer(IAnalysisRequest)
def medical_record_number(instance):
    """
    Index 'medical_record_number' (KeywordIndex).
    Prioriza obtener el MRN del paciente vinculado, luego del AR.
    """
    # Primero intentar obtener del paciente vinculado
    patient = _get_patient(instance)
    if patient is not None:
        # Usar el método getMRN del paciente si existe
        if hasattr(patient, "getMRN"):
            mrn = patient.getMRN()
            if mrn:
                return [mrn]
        # Fallback a atributo directo
        mrn = getattr(patient, "mrn", u"")
        if mrn:
            return [mrn]

    # Si no hay paciente, intentar del AR
    mrn = getattr(instance, "medical_record_number", u"")
    if callable(mrn):
        mrn = mrn()
    mrn = _s(mrn).strip()

    return [mrn] if mrn else []


# ---------------------------------------------------------------------------
# NUEVO: getMedicalRecordNumberValue (lo que pinta la columna MRN en listado)
# ---------------------------------------------------------------------------
@indexer(IAnalysisRequest)
def getMedicalRecordNumberValue(instance):
    # 1) MRN guardado en el propio AR (cubre variantes)
    for attr in ("getMedicalRecordNumber", "MedicalRecordNumber", "medical_record_number"):
        v = _get_attr(instance, attr)
        if v:
            return api.safe_unicode(v).strip() or None
    # 2) MRN desde Paciente (cubre variantes)
    patient = _get_patient(instance)
    if patient:
        for attr in ("getMedicalRecordNumber", "MedicalRecordNumber", "mrn", "patient_mrn"):
            v = _get_attr(patient, attr)
            if v:
                return api.safe_unicode(v).strip() or None
    return None


@indexer(IRequestAnalysis)
def getMedicalRecordNumberValue__IRequestAnalysis(instance):
    return getMedicalRecordNumberValue(instance)


# ---------------------------------------------------------------------------
# getPatientFullName (FieldIndex) – tu versión, con mismos comportamientos
# ---------------------------------------------------------------------------
@indexer(IAnalysisRequest)
def getPatientFullName(instance):
    """
    Index 'getPatientFullName' (FieldIndex).
    Prioriza obtener el nombre del paciente vinculado, luego del AR.
    """
    # Primero intentar obtener del paciente vinculado
    patient = _get_patient(instance)
    if patient is not None:
        # Usar el método getFullname del paciente si existe
        if hasattr(patient, "getFullname"):
            name = patient.getFullname()
            if name:
                return _s(name).strip()
        # Fallback a atributo directo
        name = getattr(patient, "patient_fullname", u"")
        if name:
            return _s(name).strip()

    # Si no hay paciente, intentar del AR
    name = getattr(instance, "patient_fullname", u"")
    if callable(name):
        name = name()
    return _s(name).strip()


@indexer(IRequestAnalysis)
def getPatientFullName__IRequestAnalysis(instance):
    return getPatientFullName(instance)


# ---------------------------------------------------------------------------
# NUEVO: getPatientUID (útil para filtros, joins y diagnóstico)
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


@indexer(IRequestAnalysis)
def getPatientUID__IRequestAnalysis(instance):
    return getPatientUID(instance)


# ---------------------------------------------------------------------------
# listing_searchable_text: añade MRN y nombre del paciente a los tokens
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

        # Primero intentar obtener del paciente vinculado
        patient = _get_patient(self.context)

        # MRN: priorizar paciente vinculado
        mrn = u""
        if patient is not None:
            # Usar el método getMRN del paciente si existe
            if hasattr(patient, "getMRN"):
                mrn = patient.getMRN()
            # Fallback a atributo directo
            if not mrn:
                mrn = getattr(patient, "mrn", u"")

        # Si no hay MRN del paciente, intentar del AR
        if not mrn:
            mrn = getattr(self.context, "medical_record_number", u"")
            if callable(mrn):
                mrn = mrn()

        mrn = _s(mrn).strip()
        if mrn:
            tokens.append(mrn)

        # Nombre completo: priorizar paciente vinculado
        name = u""
        if patient is not None:
            # Usar el método getFullname del paciente si existe
            if hasattr(patient, "getFullname"):
                name = patient.getFullname()
            # Fallback a atributo directo
            if not name:
                name = getattr(patient, "patient_fullname", u"")

        # Si no hay nombre del paciente, intentar del AR
        if not name:
            name = getattr(self.context, "patient_fullname", u"")
            if callable(name):
                name = name()

        name = _s(name).strip()
        if name:
            tokens.append(name)

        return tokens
