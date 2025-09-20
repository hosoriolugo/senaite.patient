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
# ------------------------------------------------------------------------
# Subscribers for Analysis Requests
# - Ensure MRN/Patient fields are always reindexed on create/modify
# ------------------------------------------------------------------------

from __future__ import absolute_import

from bika.lims.logger import logger
from senaite.patient import check_installed
from zope.component import adapter
from zope.lifecycleevent.interfaces import IObjectAddedEvent, IObjectModifiedEvent


def _safe_reindex(obj):
    """Reindex patient-related indexes in AR"""
    try:
        obj.reindexObject(idxs=[
            "getMedicalRecordNumberValue",
            "getPatientFullName",
            "getPatientUID",
        ])
    except Exception:
        try:
            obj.reindexObject()
        except Exception as e:
            logger.warning("[senaite.patient] Reindex fallback failed: %r", e)


@check_installed(None)
@adapter(IObjectAddedEvent)
def ar_added_reindex(event):
    """Triggered when an AnalysisRequest is created"""
    obj = getattr(event, "object", None)
    if not obj or getattr(obj, "portal_type", "") != "AnalysisRequest":
        return
    _safe_reindex(obj)


@check_installed(None)
@adapter(IObjectModifiedEvent)
def ar_modified_reindex(obj, event=None):
    """Triggered when an AnalysisRequest is modified"""
    ar = obj if getattr(obj, "portal_type", "") == "AnalysisRequest" else getattr(event, "object", None)
    if not ar or getattr(ar, "portal_type", "") != "AnalysisRequest":
        return

    # Detect changed attributes if available
    try:
        changed = [d.get("attribute", "") for d in getattr(event, "descriptions", [])]
    except Exception:
        changed = []

    # Patient-related fields to watch
    watched = {
        "Patient", "patient", "Subject", "title",
        "firstname", "middlename", "lastname", "maternallastname",
        "MedicalRecordNumber", "medical_record_number", "mrn",
    }

    if not changed or any(attr in watched for attr in changed):
        _safe_reindex(ar)
