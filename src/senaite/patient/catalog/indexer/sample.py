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
except NameError:  # Py3
    basestring = str


# ------------------------------
# Helpers (robustos y sin legacy)
# ------------------------------

def _safe_unicode(value):
    try:
        return api.safe_unicode(value or u"")
    except Exception:
        try:
            return api.safe_unicode(u"%s" % value)
        except Exception:
            return u""


def _normalize_mrn(value):
    """Normaliza MRN a string; acepta dicts/strings/None."""
    if isinstance(value, dict):
        for key in ("mrn", "MRN", "value", "text", "label", "title", "Title"):
            v = value.get(key)
            if isinstance(v, basestring) and v.strip():
                return v.strip()
        return u""
    if isinstance(value, basestring):
        return value.strip()
    return u""


def _get_patient(obj):
    """Intenta resolver el paciente desde el AR y por MRN como fallback."""
    patient = None

    # Getter directo del AR
    acc = getattr(obj, "getPatient", None)
    if callable(acc):
        try:
            patient = acc()
        except Exception:
            patient = None

    # Fallback por MRN si no hay paciente
    if not patient:
        for key in (
            "medical_record_number",
            "getMedicalRecordNumber",  # por si existe
            "mrn",
        ):
            val = getattr(obj, key, None)
            mrn = None
            if callable(val):
                try:
                    mrn = val()
                except Exception:
                    mrn = None
            else:
                mrn = val
            mrn = _normalize_mrn(mrn)
            if mrn:
                try:
                    from senaite.patient.api import get_patient_by_mrn
                    patient = get_patient_by_mrn(mrn)
                except Exception:
                    patient = None
                break

    return patient


def _patient_fullname(patient):
    """Construye nombre completo robusto del paciente."""
    if not patient:
        return u""

    # Getters habituales si existen
    for key in ("getFullName", "getPatientFullName", "Title"):
        acc = getattr(patient, key, None)
        if callable(acc):
            try:
                return _safe_unicode(acc())
            except Exception:
                pass
        elif isinstance(acc, basestring):
            return _safe_unicode(acc)

    # Construcción desde 4 campos
    parts = []
    for fld in ("firstname", "middlename", "lastname", "maternal_lastname"):
        v = getattr(patient, fld, None)
        if isinstance(v, dict):
            # por si alguien guardó un dict en algún punto
            v = _normalize_mrn(v)
        if v:
            parts.append(_safe_unicode(v))
    if parts:
        return u" ".join(parts)

    # Fallback al id
    try:
        return _safe_unicode(patient.getId())
    except Exception:
        return u""


def _patient_mrn(patient):
    """Obtiene MRN desde el objeto paciente, priorizando atributo moderno."""
    if not patient:
        return u""
    # Métodos si existieran
    for key in ("getMedicalRecordNumber", "getMRN"):
        acc = getattr(patient, key, None)
        if callable(acc):
            try:
                return _safe_unicode(acc())
            except Exception:
                pass
    # Atributos habituales
    v = getattr(patient, "mrn", None) or getattr(patient, "MedicalRecordNumber", None) \
        or getattr(patient, "medical_record_number", None)
    return _normalize_mrn(v)


def _mrn_from_ar(obj):
    """Obtiene MRN desde el AR o, si no, desde el paciente."""
    # Primero, campos/props modernos del AR
    for key in ("medical_record_number", "mrn"):
        acc = getattr(obj, key, None)
        v = None
        if callable(acc):
            try:
                v = acc()
            except Exception:
                v = None
        else:
            v = acc
        mrn = _normalize_mrn(v)
        if mrn:
            return mrn

    # Fallback: paciente
    p = _get_patient(obj)
    mrn = _patient_mrn(p)
    if mrn:
        return mrn

    # No hay MRN
    return u""


# -------------
# Indexers
# -------------

@indexer(IAnalysisRequest)
def is_temporary_mrn(instance):
    """True si el MRN del AR es temporal."""
    acc = getattr(instance, "isMedicalRecordTemporary", None)
    if callable(acc):
        try:
            return bool(acc())
        except Exception:
            return False
    return bool(getattr(instance, "is_temporary_mrn", False))


@indexer(IAnalysisRequest)
def medical_record_number(instance):
    """Devuelve la lista con 0/1 tokens MRN para KeywordIndex."""
    mrn = _mrn_from_ar(instance)
    # Para KeywordIndex es mejor devolver [] si vacío (no [None])
    return [mrn] if mrn else []


@indexer(IAnalysisRequest)
def getPatientFullName(instance):
    """Devuelve el nombre completo del paciente para FieldIndex."""
    patient = _get_patient(instance)
    return _patient_fullname(patient) if patient else u""


# -------------------------------------------
# listing_searchable_text: tokens adicionales
# -------------------------------------------

@adapter(IAnalysisRequest, ISenaitePatientLayer, ISampleCatalog)
@implementer(IListingSearchableTextProvider)
class ListingSearchableTextProvider(object):
    """Amplía listing_searchable_text con tokens relacionados al paciente."""
    def __init__(self, context, request, catalog):
        self.context = context
        self.request = request
        self.catalog = catalog

    def __call__(self):
        tokens = [
            _mrn_from_ar(self.context),
            getPatientFullName(self.context),  # ya retorna unicode
        ]
        # devolver lista limpia de strings no vacíos
        return [t for t in tokens if t]
