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
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Copyright 2020-2025
#
# Adapter to enrich Analysis Request listings with MRN and Patient full name.
# Ensures values are shown even when payload comes from custom patient widgets
# (dicts/labels) and when AR stores only Patient reference.
#
from __future__ import absolute_import

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
from zope.interface import implements

try:
    basestring
except NameError:
    basestring = str


def _safe_unicode(value):
    try:
        return api.safe_unicode(value or u"" )
    except Exception:
        try:
            return api.safe_unicode(u"%s" % value)
        except Exception:
            return u""


def _patient_from_ar(ar):
    # Try official API first
    patient = getattr(ar, 'getPatient', None)
    if callable(patient):
        try:
            patient = patient()
        except Exception:
            patient = None
    else:
        patient = None

    # Fallback: lookup by MRN stored on AR schema/annotations
    if not patient:
        mrn = None
        for key in ('getMedicalRecordNumberValue', 'MedicalRecordNumber',
                    'getMedicalRecordNumber', 'medical_record_number',
                    'mrn'):
            getter = getattr(ar, key, None)
            if callable(getter):
                try:
                    mrn = getter()
                except Exception:
                    mrn = None
            elif getter is not None and isinstance(getter, basestring):
                mrn = getter
            if mrn:
                break
        if mrn:
            try:
                patient = get_patient_by_mrn(mrn)
            except Exception:
                patient = None
    return patient


def _fullname_from_patient(patient):
    if not patient:
        return u""
    # Prefer the computed accessor if present
    for key in ('getFullName', 'getPatientFullName', 'Title'):
        acc = getattr(patient, key, None)
        if callable(acc):
            try:
                return _safe_unicode(acc())
            except Exception:
                pass
    # Build from 4-part schema if available
    parts = []
    for fld in ('firstname', 'middlename', 'lastname', 'maternallastname'):
        getter = getattr(patient, 'get_' + fld, None) or getattr(patient, 'get' + fld.capitalize(), None)
        val = getter() if callable(getter) else getattr(patient, fld, u"",)
        if val:
            parts.append(_safe_unicode(val))
    if parts:
        return u" ".join([p for p in parts if p])
    # Fallback to id
    return _safe_unicode(getattr(patient, 'Title', lambda: patient.getId())()) if hasattr(patient, 'Title') else _safe_unicode(patient.getId())


class ARListingAdapter(object):
    """Adds MRN and Patient columns to Analysis Requests listing.
    """
    implements(IListingViewAdapter)
    adapts(IListingView)

    def __init__(self, view):
        self.view = view

    @property
    def portal_type(self):
        return self.view.context.portal_type

    @memoize
    def _is_ar_listing(self):
        # Limit to AR listings
        return getattr(self.view, 'contentFilter', {}).get('portal_type') in ('AnalysisRequest',)

    def before_render(self):
        if not self._is_ar_listing():
            return
        if not check_installed():
            return

        # Add MRN
        add_column(
            self.view,
            after='getId',
            name='getMedicalRecordNumberValue',
            title=_('MRN'),
            index='getMedicalRecordNumberValue',
            toggle=True,
            sortable=True,
            getter=self._col_mrn)

        # Add Patient
        add_column(
            self.view,
            after='getMedicalRecordNumberValue',
            name='getPatientFullName',
            title=_('Patient'),
            index='getPatientFullName',
            toggle=True,
            sortable=True,
            getter=self._col_patient)

    def _col_mrn(self, item, obj, *args):
        # Prefer catalog brain value if available to avoid wakeup
        brain = item.get('brain')
        if brain:
            val = getattr(brain, 'getMedicalRecordNumberValue', None)
            if val:
                return _safe_unicode(val)

        # Compute from object
        patient = _patient_from_ar(obj)
        if patient:
            getter = getattr(patient, 'getMedicalRecordNumber', None) or getattr(patient, 'getMRN', None)
            if callable(getter):
                try:
                    return _safe_unicode(getter())
                except Exception:
                    pass
            mrn_attr = getattr(patient, 'MedicalRecordNumber', None) or getattr(patient, 'mrn', None)
            if isinstance(mrn_attr, basestring):
                return _safe_unicode(mrn_attr)
        # Last chance: attribute on AR
        for key in ('getMedicalRecordNumberValue', 'MedicalRecordNumber', 'medical_record_number', 'mrn'):
            v = getattr(obj, key, None)
            if callable(v):
                try:
                    return _safe_unicode(v())
                except Exception:
                    continue
            elif isinstance(v, basestring):
                return _safe_unicode(v)
        return u""

    def _col_patient(self, item, obj, *args):
        # Prefer catalog brain for performance
        brain = item.get('brain')
        if brain:
            val = getattr(brain, 'getPatientFullName', None)
            if val:
                # Render as link to patient if UID present
                puid = getattr(brain, 'getPatientUID', None)
                if puid:
                    patient = api.get_object_by_uid(puid)
                    if patient:
                        return get_link(patient, text=_safe_unicode(val))
                return _safe_unicode(val)

        # Compute from object
        patient = _patient_from_ar(obj)
        if not patient:
            return _safe_unicode(_(u"No patient"))
        fullname = _fullname_from_patient(patient)
        try:
            return get_link(patient, text=fullname)
        except Exception:
            return fullname
