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
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# ------------------------------------------------------------------------
# Subscribers for Analysis Requests
# Adjusted to reindex MRN/Patient safely and tolerate RequestContainer
# ------------------------------------------------------------------------

from __future__ import absolute_import

from bika.lims import api
from senaite.core.behaviors import IClientShareableBehavior
from senaite.patient import api as patient_api
from senaite.patient import check_installed
from senaite.patient import logger


def _safe_reindex(obj):
    """Reindex patient-related indexes in AR"""
    try:
        obj.reindexObject(idxs=[
            "getPatientUID",
            "getPatientFullName",
            "getMedicalRecordNumberValue",
        ])
    except Exception:
        try:
            obj.reindexObject()
        except Exception as e:
            logger.warning("[senaite.patient] Reindex fallback failed: %r", e)


@check_installed(None)
def on_object_created(instance, event):
    """Event handler when a sample was created"""
    patient = update_patient(instance)

    # no patient created when the MRN is temporary
    if not patient:
        return

    # append patient email to sample CC emails
    if patient.getEmailReport():
        email = patient.getEmail()
        add_cc_email(instance, email)

    # share patient with sample's client users if necessary
    reg_key = "senaite.patient.share_patients"
    if api.get_registry_record(reg_key, default=False):
        client_uid = api.get_uid(instance.getClient())
        behavior = IClientShareableBehavior(patient)
        client_uids = behavior.getRawClients() or []
        if client_uid not in client_uids:
            client_uids.append(client_uid)
            behavior.setClients(client_uids)

    # ensure reindex after creation
    _safe_reindex(instance)


@check_installed(None)
def on_object_edited(instance, event):
    """Event handler when a sample was edited"""
    update_patient(instance)
    update_results_ranges(instance)
    # ensure reindex after modification
    _safe_reindex(instance)


def add_cc_email(sample, email):
    """add CC email recipient to sample"""
    emails = sample.getCCEmails().split(",")
    if email in emails:
        return
    emails.append(email)
    emails = map(lambda e: e.strip(), emails)
    sample.setCCEmails(",".join(emails))


def update_patient(instance):
    """Update or create Patient object for a given Analysis Request.

    Tolerant to non-AR objects (e.g. RequestContainer in add form).
    """
    # skip if this is not a real AR (e.g. RequestContainer)
    if not hasattr(instance, "getMedicalRecordNumberValue") or not hasattr(instance, "getField"):
        return None

    # skip if temporary MRN (and method exists)
    if hasattr(instance, "isMedicalRecordTemporary") and instance.isMedicalRecordTemporary():
        return None

    mrn = instance.getMedicalRecordNumberValue()
    if mrn is None:
        return

    # lookup patient by MRN
    patient = patient_api.get_patient_by_mrn(mrn, include_inactive=True)

    # create a new patient if not found
    if patient is None:
        if patient_api.is_patient_allowed_in_client():
            container = instance.getClient()
        else:
            container = patient_api.get_patient_folder()

        if not patient_api.is_patient_creation_allowed(container):
            return None

        logger.info("Creating new Patient in '{}' with MRN: '{}'"
                    .format(api.get_path(container), mrn))
        values = get_patient_fields(instance)
        try:
            patient = api.create(container, "Patient")
            patient_api.update_patient(patient, **values)
        except ValueError as exc:
            logger.error("%s" % exc)
            logger.error("Failed to create patient for values: %r" % values)
            raise exc

    return patient


def get_patient_fields(instance):
    """Extract the patient fields from the sample"""
    mrn = instance.getMedicalRecordNumberValue()
    sex = instance.getField("Sex").get(instance)
    gender = instance.getField("Gender").get(instance)
    dob_field = instance.getField("DateOfBirth")
    birthdate = dob_field.get_date_of_birth(instance)
    estimated = dob_field.get_estimated(instance)
    address = instance.getField("PatientAddress").get(instance)
    field = instance.getField("PatientFullName")
    firstname = field.get_firstname(instance)
    middlename = field.get_middlename(instance)
    lastname = field.get_lastname(instance)

    if address:
        address = {
            "type": "physical",
            "address": api.safe_unicode(address),
        }

    return {
        "mrn": mrn,
        "sex": sex,
        "gender": gender,
        "birthdate": birthdate,
        "estimated_birthdate": estimated,
        "address": address,
        "firstname": api.safe_unicode(firstname),
        "middlename": api.safe_unicode(middlename),
        "lastname": api.safe_unicode(lastname),
    }


def update_results_ranges(sample):
    """Re-assigns the values of the results ranges for analyses, so dynamic
    specifications are re-calculated when patient values such as sex and date
    of birth are updated
    """
    spec = sample.getSpecification()
    if spec:
        ranges = spec.getResultsRange()
        sample.setResultsRange(ranges, recursive=False)
