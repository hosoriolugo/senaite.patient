# -*- coding: utf-8 -*-
#
# This file is part of SENAITE.PATIENT.
#
# SENAITE.PATIENT is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, version 2.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Copyright 2020-2025 by it's authors.
# Some rights reserved, see README and LICENSE.

from bika.lims import api
from senaite.core.behaviors import IClientShareableBehavior
from senaite.patient import api as patient_api
from senaite.patient import check_installed
from senaite.patient import logger
from Missing import Value as MissingValue


def safe_text(val):
    """Convierte Missing/None a '' y asegura unicode seguro"""
    if val is None or val is MissingValue:
        return u""
    try:
        return api.safe_unicode(val)
    except Exception:
        return u""


@check_installed(None)
def on_object_created(instance, event):
    """Event handler when a sample was created
    """
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


@check_installed(None)
def on_object_edited(instance, event):
    """Event handler when a sample was edited
    """
    update_patient(instance)
    # update results ranges so dynamic specs are recalculated
    update_results_ranges(instance)


def add_cc_email(sample, email):
    """add CC email recipient to sample
    """
    emails = sample.getCCEmails().split(",")
    if email in emails:
        return
    emails.append(email)
    emails = map(lambda e: e.strip(), emails)
    sample.setCCEmails(",".join(emails))


def update_patient(instance):
    """Update or create Patient object for a given Analysis Request
    """
    if not hasattr(instance, "getMedicalRecordNumberValue"):
        logger.debug("[senaite.patient] Ignorando update_patient: %r no parece un AnalysisRequest", instance)
        return None

    if not hasattr(instance, "isMedicalRecordTemporary"):
        logger.debug("[senaite.patient] Objeto sin isMedicalRecordTemporary: %r", instance)
        return None

    if instance.isMedicalRecordTemporary():
        return None

    mrn = safe_text(instance.getMedicalRecordNumberValue())
    if not mrn:
        return None

    patient = patient_api.get_patient_by_mrn(mrn, include_inactive=True)

    # Crear paciente si no existe
    if patient is None:
        if patient_api.is_patient_allowed_in_client():
            container = instance.getClient()
        else:
            container = patient_api.get_patient_folder()

        if not patient_api.is_patient_creation_allowed(container):
            return None

        logger.info("Creating new Patient in '{}' with MRN: '{}'".format(api.get_path(container), mrn))
        values = get_patient_fields(instance)
        try:
            patient = api.create(container, "Patient")
            patient_api.update_patient(patient, **values)
        except ValueError as exc:
            logger.error("%s" % exc)
            logger.error("Failed to create patient for values: %r" % values)
            raise exc

    # 🔹 Vincular Paciente y MRN al AR y reindexar
    try:
        instance.setPatient(patient)
        instance.setMedicalRecordNumber(mrn)
        instance.reindexObject(idxs=[
            "getMedicalRecordNumberValue",
            "getPatientUID",
            "getPatientFullName",
        ])
    except Exception as e:
        logger.warning("[senaite.patient] No se pudo persistir MRN/Paciente en %r: %s", instance, e)

    return patient


def get_patient_fields(instance):
    """Extract the patient fields from the sample
    """
    mrn = safe_text(instance.getMedicalRecordNumberValue())
    sex = safe_text(instance.getField("Sex").get(instance))
    gender = safe_text(instance.getField("Gender").get(instance))
    dob_field = instance.getField("DateOfBirth")
    birthdate = dob_field.get_date_of_birth(instance)
    estimated = dob_field.get_estimated(instance)
    address = instance.getField("PatientAddress").get(instance)
    field = instance.getField("PatientFullName")

    firstname = safe_text(field.get_firstname(instance))
    middlename = safe_text(field.get_middlename(instance))
    lastname = safe_text(field.get_lastname(instance))
    maternal_lastname = safe_text(field.get_maternal_lastname(instance))

    if address:
        address = {
            "type": "physical",
            "address": safe_text(address),
        }

    return {
        "mrn": mrn,
        "sex": sex,
        "gender": gender,
        "birthdate": birthdate,
        "estimated_birthdate": estimated,
        "address": address,
        "firstname": firstname,
        "middlename": middlename,
        "lastname": lastname,
        "maternal_lastname": maternal_lastname,
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
