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

import re
from datetime import datetime

from bika.lims import api
from bika.lims import deprecated
from dateutil.relativedelta import relativedelta
from senaite.core.api import dtime
from senaite.patient.config import PATIENT_CATALOG
from senaite.patient.permissions import AddPatient
from six import string_types

CLIENT_TYPE = "Client"
PATIENT_TYPE = "Patient"
CLIENT_VIEW_ID = "patients"
CLIENT_VIEW_ACTION = {
    "id": CLIENT_VIEW_ID,
    "name": "Patients",
    "action": "string:${object_url}/patients",
    "permission": "View",
    "category": "object",
    "visible": True,
    "icon_expr": "",
    "link_target": "",
    "condition": "",
}

# Safe multi-line regex literal (Py2.7-friendly)
YMD_REGEX = (
    r'^((?P<y>(\d+))y){0,1}\s*'
    r'((?P<m>(\d+))m){0,1}\s*'
    r'((?P<d>(\d+))d){0,1}\s*'
)

_marker = object()


def is_patient_required():
    """Checks if the patient is required"""
    required = api.get_registry_record("senaite.patient.require_patient")
    if not required:
        return False
    return True


# -----------------------------
# Name assembly configuration
# -----------------------------

def get_patient_name_entry_mode():
    """Returns the entry mode for patient name.
    ...
    """
    entry_mode = api.get_registry_record("senaite.patient.patient_entry_mode")
    if not entry_mode:
        return u"parts"
    key = api.safe_unicode(entry_mode).strip().lower()

    aliases = {
        u"name_surnames": u"first_last",
        u"first_surnames": u"first_last",
        u"name_middle_surnames": u"first_middle_last",
        u"first_middle_surnames": u"first_middle_last",
        u"first_lastname": u"first_last",
    }
    key = aliases.get(key, key)
    if key not in {u"parts", u"first_last", u"first_middle_last", u"fullname"}:
        key = u"parts"
    return key


def get_patient_address_format():
    """Returns the address format"""
    return api.get_registry_record("senaite.patient.address_format")


def is_gender_visible():
    """Checks whether the gender is visible"""
    key = "senaite.patient.gender_visible"
    return api.get_registry_record(key, default=True)


def is_future_birthdate_allowed():
    """Returns whether the introduction of a birth date in future is allowed"""
    key = "senaite.patient.future_birthdate"
    return api.get_registry_record(key, default=False)


def is_age_supported():
    """Returns whether the introduction of age is supported"""
    key = "senaite.patient.age_supported"
    return api.get_registry_record(key, default=True)


def is_age_in_years():
    """Returns whether months/days should be omitted when age > 1 year"""
    key = "senaite.patient.age_years"
    return api.get_registry_record(key, default=True)


def _normalize_mrn(mrn):
    """Always return MRN as safe unicode string."""
    try:
        if isinstance(mrn, dict):
            for k in ('mrn', 'MRN', 'value', 'text', 'label', 'title', 'Title'):
                v = mrn.get(k)
                if isinstance(v, string_types) and v.strip():
                    return api.safe_unicode(v).strip()
            return u""
        if isinstance(mrn, string_types):
            return api.safe_unicode(mrn).strip()
    except Exception:
        return u""
    return u""


def get_patient_by_mrn(mrn, full_object=True, include_inactive=False):
    """Get a patient by Medical Record Number"""
    query = {
        "portal_type": "Patient",
        "patient_mrn": _normalize_mrn(mrn).encode("utf8"),
        "is_active": True,
    }
    if include_inactive:
        query.pop("is_active", None)
    results = patient_search(query)
    count = len(results)
    if count == 0:
        return None
    elif count > 1:
        raise ValueError("Found {} Patients for MRN {}".format(count, mrn))
    if not full_object:
        return results[0]
    return api.get_object(results[0])


def get_patient_catalog():
    return api.get_tool(PATIENT_CATALOG)


def patient_search(query):
    catalog = get_patient_catalog()
    return catalog(query)


def update_patient(patient, **values):
    """Update an existing patient with explicit values and reindex"""
    raw_mrn = values.get("mrn", api.get_id(patient))
    norm_mrn = _normalize_mrn(raw_mrn)
    if not norm_mrn and raw_mrn and not isinstance(raw_mrn, string_types):
        import logging
        logger = logging.getLogger("senaite.patient")
        logger.warning("[update_patient] MRN value was not a string/dict: %r (type=%s)", raw_mrn, type(raw_mrn))
    patient.setMRN(norm_mrn)

    patient.setFirstname(values.get("firstname", ""))
    patient.setMiddlename(values.get("middlename", ""))
    patient.setLastname(values.get("lastname", ""))
    if hasattr(patient, "setMaternalLastname"):
        patient.setMaternalLastname(values.get("maternal_lastname", ""))
    patient.setSex(values.get("sex", ""))
    patient.setGender(values.get("gender", ""))
    patient.setBirthdate(values.get("birthdate"))
    patient.setEstimatedBirthdate(values.get("estimated_birthdate", False))
    patient.setAddress(values.get("address"))
    patient.reindexObject()


@deprecated("Use senaite.core.api.dtime.to_dt instead")
def to_datetime(date_value, default=None, tzinfo=None):
    if isinstance(date_value, datetime):
        return date_value
    date_value = dtime.to_DT(date_value)
    if not date_value:
        if default is None:
            return None
        return to_datetime(default, tzinfo=tzinfo)
    date_value = date_value.asdatetime()
    return date_value.replace(tzinfo=tzinfo)


def to_ymd(period, default=_marker):
    try:
        ymd_values = get_years_months_days(period)
    except (TypeError, ValueError) as e:
        if default is _marker:
            raise e
        return default
    ymd_values = map(str, ymd_values)
    ymd = filter(lambda it: int(it[0]), zip(ymd_values, "ymd"))
    ymd = " ".join(map("".join, ymd))
    return ymd or "0d"


def is_ymd(ymd):
    if not isinstance(ymd, string_types):
        return False
    try:
        get_years_months_days(ymd)
    except (TypeError, ValueError):
        return False
    return True


def get_years_months_days(period):
    if isinstance(period, relativedelta):
        return period.years, period.months, period.days
    if isinstance(period, (tuple, list)):
        years = api.to_int(period[0], default=0)
        months = api.to_int(period[1] if len(period) > 1 else 0, default=0)
        days = api.to_int(period[2] if len(period) > 2 else 0, default=0)
        return years, months, days
    if not isinstance(period, string_types):
        raise TypeError("{} is not supported".format(repr(period)))
    raw_ymd = period.lower().strip()
    matches = re.search(YMD_REGEX, raw_ymd)
    values = [matches.group(v) for v in "ymd"]
    if all(value is None for value in values):
        raise ValueError("Not a valid ymd: {}".format(repr(period)))
    values = [api.to_int(value, 0) for value in values]
    delta = relativedelta(years=values[0], months=values[1], days=values[2])
    return get_years_months_days(delta)


def get_birth_date(period, on_date=None, default=_marker):
    try:
        years, months, days = get_years_months_days(period)
    except (TypeError, ValueError) as e:
        if default is _marker:
            raise e
        return dtime.to_dt(default)
    on_date = dtime.to_dt(on_date)
    if not on_date:
        on_date = datetime.now()
        tz = dtime.get_os_timezone()
        on_date = dtime.to_zone(on_date, tz)
    delta = relativedelta(years=years, months=months, days=days)
    return on_date - delta


def get_age_ymd(birth_date, on_date=None):
    try:
        delta = dtime.get_relative_delta(birth_date, on_date)
        return to_ymd(delta)
    except (ValueError, TypeError):
        return None


@deprecated("Use senaite.core.api.dtime.get_relative_delta instead")
def get_relative_delta(from_date, to_date=None):
    return dtime.get_relative_delta(from_date, to_date)


def tuplify_identifiers(identifiers):
    out = []
    for identifier in identifiers:
        key = identifier.get("key")
        value = identifier.get("value")
        out.append((key, value,))
    return out


def to_identifier_type_name(identifier_type_key):
    records = api.get_registry_record("senaite.patient.identifiers")
    name = identifier_type_key
    for record in records:
        key = record.get("key")
        if key != identifier_type_key:
            continue
        name = record.get("value")
    return name


def allow_patients_in_clients(allow=True):
    pt = api.get_tool("portal_types")
    ti = pt.getTypeInfo(CLIENT_TYPE)
    fti = pt.get(CLIENT_TYPE)
    allowed_types = set(fti.allowed_content_types)
    action_ids = map(lambda a: a.id, ti._actions)
    if allow:
        allowed_types.add(PATIENT_TYPE)
        if CLIENT_VIEW_ID not in action_ids:
            ti.addAction(**CLIENT_VIEW_ACTION)
            ref_index = action_ids.index("contacts")
            actions = ti._cloneActions()
            action = actions.pop()
            actions.insert(ref_index - 1, action)
            ti._actions = tuple(actions)
    else:
        allowed_types.discard(PATIENT_TYPE)
        if CLIENT_VIEW_ID in action_ids:
            ti.deleteActions([action_ids.index(CLIENT_VIEW_ID)])
    fti.allowed_content_types = tuple(allowed_types)


def is_patient_allowed_in_client():
    return api.get_registry_record("senaite.patient.allow_patients_in_clients", False)


def get_patient_folder():
    portal = api.get_portal()
    return portal.patients


def is_patient_creation_allowed(container):
    return api.security.check_permission(AddPatient, container)


def is_mrn_unique(mrn):
    query = {
        "portal_type": "Patient",
        "patient_mrn": _normalize_mrn(mrn).encode("utf8"),
    }
    brains = api.search(query, PATIENT_CATALOG)
    return len(brains) == 0


# -----------------------------
#   Name helpers (centralized)
# -----------------------------

def _join_clean(parts):
    parts = [api.safe_unicode(p).strip() for p in parts if p]
    text = u" ".join(parts)
    return u" ".join(text.split())


def get_patient_lastname(patient):
    last_ = getattr(patient, 'getLastname', lambda: u"")() or u""
    mat_ = getattr(patient, 'getMaternalLastname', lambda: u"")() or u""
    return _join_clean([last_, mat_])


def get_patient_fullname(patient, mode=None):
    if mode is None:
        mode = get_patient_name_entry_mode()
    first = getattr(patient, 'getFirstname', lambda: u"")() or u""
    middle = getattr(patient, 'getMiddlename', lambda: u"")() or u""
    surnames = get_patient_lastname(patient)
    if mode == u"parts" or mode == u"first_middle_last":
        return _join_clean([first, middle, surnames])
    if mode == u"first_last":
        return _join_clean([first, surnames])
    if mode == u"fullname":
        if hasattr(patient, 'getFullname'):
            try:
                val = patient.getFullname()
                if val:
                    return api.safe_unicode(val).strip()
            except Exception:
                pass
        if hasattr(patient, 'Title'):
            try:
                return api.safe_unicode(patient.Title()).strip()
            except Exception:
                pass
        return _join_clean([first, surnames])
    return _join_clean([first, middle, surnames])


# -----------------------------
#   Payload normalization utils
# -----------------------------

def _extract_fullname(data):
    """Build a full name from dict payload or return string as-is.
    Defensive: supports dicts with firstname/middlename/lastname/maternal_lastname.
    """
    if not data:
        return u""
    try:
        if isinstance(data, dict):
            parts = []
            for key in ("firstname", "middlename", "lastname", "maternal_lastname"):
                val = data.get(key)
                if val:
                    parts.append(api.safe_unicode(val).strip())
            return _join_clean(parts)
        if isinstance(data, string_types):
            return api.safe_unicode(data).strip()
    except Exception:
        return u""
    return u""
