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
    """Event handler when a sample was created"""
    patient = update_patient(instance)

    # no patient created when the MRN is temporary o si no aplica
    if not patient:
        return

    # append patient email to sample CC emails
    try:
        if patient.getEmailReport():
            email = patient.getEmail()
            add_cc_email(instance, email)
    except Exception:
        pass

    # share patient with sample's client users if necessary
    reg_key = "senaite.patient.share_patients"
    if api.get_registry_record(reg_key, default=False):
        try:
            client_uid = api.get_uid(instance.getClient())
            behavior = IClientShareableBehavior(patient)
            client_uids = behavior.getRawClients() or []
            if client_uid not in client_uids:
                client_uids.append(client_uid)
                behavior.setClients(client_uids)
        except Exception as e:
            logger.warning("[senaite.patient] No se pudo compartir el paciente: %s", e)


@check_installed(None)
def on_object_edited(instance, event):
    """Event handler when a sample was edited"""
    update_patient(instance)
    update_results_ranges(instance)


def add_cc_email(sample, email):
    """add CC email recipient to sample"""
    emails = safe_text(sample.getCCEmails()).split(",")
    if email in emails:
        return
    emails.append(email)
    emails = map(lambda e: e.strip(), emails)
    sample.setCCEmails(",".join(emails))


def _get_mrn_from_ar_or_patient(instance):
    """Devuelve MRN priorizando el AR; si está vacío, cae al Paciente vinculado."""
    mrn = safe_text(getattr(instance, "getMedicalRecordNumberValue", lambda: u"")())
    if mrn:
        return mrn
    try:
        patient = getattr(instance, "getPatient", lambda: None)()
    except Exception:
        patient = None
    if patient:
        return safe_text(getattr(patient, "getMRN", lambda: u"")())
    return u""


def _get_or_create_patient_by_mrn(instance, mrn):
    """Busca el paciente por MRN o lo crea con los datos del AR."""
    patient = patient_api.get_patient_by_mrn(mrn, include_inactive=True)
    if patient is not None:
        return patient

    # Crear en cliente o carpeta global
    if patient_api.is_patient_allowed_in_client():
        container = instance.getClient()
    else:
        container = patient_api.get_patient_folder()

    if not patient_api.is_patient_creation_allowed(container):
        return None

    logger.info("Creating new Patient in '%s' with MRN: '%s'", api.get_path(container), mrn)
    values = get_patient_fields(instance)
    try:
        patient = api.create(container, "Patient")
        patient_api.update_patient(patient, **values)
    except ValueError as exc:
        logger.error("%s", exc)
        logger.error("Failed to create patient for values: %r", values)
        raise exc
    return patient


def _bind_patient_and_mrn(instance, patient, mrn):
    """Vincula Patient/MRN al AR y reindexa campos usados en listados/búsquedas."""
    try:
        if patient and hasattr(instance, "setPatient"):
            instance.setPatient(patient)

        # Fijar MRN a través del field para evitar mutadores con nombre diferente
        try:
            fld = instance.getField("MedicalRecordNumber")
            if fld and mrn:
                fld.set(instance, mrn)
        except Exception:
            # fallback por si existe un mutador explícito
            if mrn and hasattr(instance, "setMedicalRecordNumber"):
                instance.setMedicalRecordNumber(mrn)

        # Reindex de índices/metadatos usados en catálogos y listados
        idxs = [
            "getPatientUID",
            "getPatientFullName",
            "getMedicalRecordNumberValue",
            "medical_record_number",
        ]
        try:
            instance.reindexObject(idxs=idxs)
        except TypeError:
            instance.reindexObject()
    except Exception as e:
        logger.warning("[senaite.patient] No se pudo persistir MRN/Paciente en %r: %s", instance, e)


def update_patient(instance):
    """Update or create Patient object for a given Analysis Request.

    Tolerante a objetos que no sean AR reales (p.ej. RequestContainer del add form).
    """
    # Si no parece un AR, salir sin romper
    if not hasattr(instance, "getMedicalRecordNumberValue") or not hasattr(instance, "getField"):
        return None

    if hasattr(instance, "isMedicalRecordTemporary") and instance.isMedicalRecordTemporary():
        return None

    # 1) Asegurar MRN (desde AR o desde Paciente si AR está vacío)
    mrn = _get_mrn_from_ar_or_patient(instance)
    if not mrn:
        # MRN vacío permitido -> nada que actualizar
        return None

    # 2) Obtener/crear Paciente
    try:
        patient = getattr(instance, "getPatient", lambda: None)()
    except Exception:
        patient = None
    if not patient:
        patient = _get_or_create_patient_by_mrn(instance, mrn)

    # 3) Vincular y reindexar
    if patient:
        _bind_patient_and_mrn(instance, patient, mrn)

    return patient


def get_patient_fields(instance):
    """Extract the patient fields from the sample"""
    mrn = safe_text(getattr(instance, "getMedicalRecordNumberValue", lambda: u"")())
    # Campos básicos (tolerantes a Missing)
    sex = safe_text(getattr(instance.getField("Sex"), "get", lambda _i: u"")(instance))
    gender = safe_text(getattr(instance.getField("Gender"), "get", lambda _i: u"")(instance))

    # Fecha de nacimiento/estimación
    dob_field = instance.getField("DateOfBirth")
    birthdate = getattr(dob_field, "get_date_of_birth", lambda _i: None)(instance)
    estimated = getattr(dob_field, "get_estimated", lambda _i: False)(instance)

    # Dirección
    address_val = getattr(instance.getField("PatientAddress"), "get", lambda _i: u"")(instance)
    address_val = safe_text(address_val)
    address = None
    if address_val:
        address = {"type": "physical", "address": address_val}

    # Nombre completo (con apellido materno si el esquema lo tiene)
    fn_field = instance.getField("PatientFullName")
    firstname = safe_text(getattr(fn_field, "get_firstname", lambda _i: u"")(instance))
    middlename = safe_text(getattr(fn_field, "get_middlename", lambda _i: u"")(instance))
    lastname = safe_text(getattr(fn_field, "get_lastname", lambda _i: u"")(instance))
    maternal_lastname = safe_text(getattr(fn_field, "get_maternal_lastname", lambda _i: u"")(instance))

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
    try:
        spec = sample.getSpecification()
        if spec:
            ranges = spec.getResultsRange()
            sample.setResultsRange(ranges, recursive=False)
    except Exception:
        pass
