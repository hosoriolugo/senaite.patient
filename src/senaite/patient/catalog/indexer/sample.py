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
    try:
        return api.safe_unicode(v) if v is not None else u""
    except Exception:
        return u""


def _get_patient(ar):
    """Usa SOLO el método/campo actual para obtener el paciente desde el AR."""
    getter = getattr(ar, "getPatient", None)
    return getter() if callable(getter) else None


@indexer(IAnalysisRequest)
def is_temporary_mrn(instance):
    """Index booleano: nuevo campo del AR."""
    return bool(getattr(instance, "is_temporary_mrn", False))


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
        if hasattr(patient, 'getMRN'):
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
        if hasattr(patient, 'getFullname'):
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
            if hasattr(patient, 'getMRN'):
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
            if hasattr(patient, 'getFullname'):
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
