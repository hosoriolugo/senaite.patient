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
    Lee el nuevo campo del AR; si está vacío, intenta desde el paciente (nuevo campo).
    """
    mrn = getattr(instance, "medical_record_number", u"")
    if callable(mrn):
        mrn = mrn()
    mrn = _s(mrn).strip()

    if not mrn:
        patient = _get_patient(instance)
        if patient is not None:
            pmrn = getattr(patient, "patient_mrn", u"")
            if callable(pmrn):
                pmrn = pmrn()
            mrn = _s(pmrn).strip()

    # Para KeywordIndex, devolver lista de tokens (0/1)
    return [mrn] if mrn else []


@indexer(IAnalysisRequest)
def getPatientFullName(instance):
    """
    Index 'getPatientFullName' (FieldIndex).
    Se toma SOLO del objeto paciente (nuevo campo).
    """
    patient = _get_patient(instance)
    if patient is None:
        return u""
    name = getattr(patient, "patient_fullname", u"")
    if callable(name):
        name = name()
    return _s(name).strip()


@adapter(IAnalysisRequest, ISenaitePatientLayer, ISampleCatalog)
@implementer(IListingSearchableTextProvider)
class ListingSearchableTextProvider(object):
    """Añade MRN y nombre del paciente a listing_searchable_text (solo nuevos campos)."""

    def __init__(self, context, request, catalog):
        self.context = context
        self.request = request
        self.catalog = catalog

    def __call__(self):
        tokens = []

        # MRN del AR (nuevo) o, si falta, del paciente (nuevo)
        mrn = getattr(self.context, "medical_record_number", u"")
        if callable(mrn):
            mrn = mrn()
        mrn = _s(mrn).strip()

        if not mrn:
            patient = _get_patient(self.context)
            if patient is not None:
                pmrn = getattr(patient, "patient_mrn", u"")
                if callable(pmrn):
                    pmrn = pmrn()
                mrn = _s(pmrn).strip()

        if mrn:
            tokens.append(mrn)

        # Nombre completo del paciente (nuevo)
        patient = _get_patient(self.context)
        if patient is not None:
            name = getattr(patient, "patient_fullname", u"")
            if callable(name):
                name = name()
            name = _s(name).strip()
            if name:
                tokens.append(name)

        return tokens
