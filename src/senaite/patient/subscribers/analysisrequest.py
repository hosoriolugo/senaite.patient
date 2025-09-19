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
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
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
    """Event handler cuando se crea un Analysis Request (muestra)"""
    patient = update_patient(instance)

    # No hay paciente (p.ej. MRN temporal o MRN vacío) => nada más que hacer
    if not patient:
        return

    # Agregar email del paciente a CC si aplica
    try:
        if patient.getEmailReport():
            email = patient.getEmail()
            add_cc_email(instance, email)
    except Exception:
        # No bloquear el flujo si el paciente no expone estos getters
        pass

    # Compartir paciente con usuarios del cliente si la opción está activa
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
    """Event handler cuando se edita un Analysis Request"""
    update_patient(instance)
    # Recalcular especificaciones dinámicas si cambian datos de paciente
    update_results_ranges(instance)


def add_cc_email(sample, email):
    """Agregar destinatario CC al AR"""
    emails = sample.getCCEmails().split(",")
    if email in emails:
        return
    emails.append(email)
    emails = map(lambda e: e.strip(), emails)
    sample.setCCEmails(",".join(emails))


def update_patient(instance):
    """Crear/actualizar Paciente y persistir MRN/Paciente en el AR."""
    # Asegurar que es un AR y que soporta MRN temporal
    if not hasattr(instance, "getMedicalRecordNumberValue"):
        logger.debug("[senaite.patient] Ignorando update_patient: %r no parece un AnalysisRequest", instance)
        return None

    if not hasattr(instance, "isMedicalRecordTemporary"):
        logger.debug("[senaite.patient] Objeto sin isMedicalRecordTemporary: %r", instance)
        return None

    # Si el MRN es temporal, no crear/vincular paciente
    if instance.isMedicalRecordTemporary():
        return None

    # Tomar MRN actual del AR (valor normalizado)
    mrn = safe_text(instance.getMedicalRecordNumberValue())
    if not mrn:
        # Permitido si el flujo no exige paciente; no hay nada que vincular
        return None

    # Buscar o crear Paciente por MRN
    patient = patient_api.get_patient_by_mrn(mrn, include_inactive=True)

    if patient is None:
        # Elegir contenedor según la configuración
        if patient_api.is_patient_allowed_in_client():
            container = instance.getClient()
        else:
            container = patient_api.get_patient_folder()

        # Verificar permiso de creación
        if not patient_api.is_patient_creation_allowed(container):
            return None

        logger.info("Creating new Patient in '{}' with MRN: '{}'".format(api.get_path(container), mrn))
        values = get_patient_fields(instance)
        try:
            patient = api.create(container, "Patient")
            patient_api.update_patient(patient, **values)
        except ValueError as exc:
            logger.error("%s", exc)
            logger.error("Failed to create patient for values: %r", values)
            raise

    # Vincular Paciente al AR (tolerante a errores)
    try:
        if hasattr(instance, "setPatient"):
            instance.setPatient(patient)
    except Exception as e:
        logger.warning("[senaite.patient] No se pudo setPatient(%r): %s", patient, e)

    # ⚠️ Persistir MRN en el AR con el **setter correcto**
    # En 2.6 el getter es getMedicalRecordNumberValue() => el setter es setMedicalRecordNumberValue()
    try:
        if hasattr(instance, "setMedicalRecordNumberValue"):
            instance.setMedicalRecordNumberValue(mrn)
        else:
            # Fallback (no debería ser necesario, pero no rompemos si no existe)
            logger.debug("[senaite.patient] setMedicalRecordNumberValue no disponible en %r", instance)
    except Exception as e:
        logger.warning("[senaite.patient] No se pudo setMedicalRecordNumberValue(%s): %s", mrn, e)

    # (Opcional) Persistir nombre completo en el AR si el esquema lo soporta
    try:
        if hasattr(instance, "setPatientFullName"):
            fullname = u""
            if hasattr(patient, "getFullname"):
                fullname = safe_text(patient.getFullname())
            instance.setPatientFullName(fullname)
    except Exception as e:
        # Si el campo no existe o algo falla, no impedir el flujo
        logger.debug("[senaite.patient] setPatientFullName omitido: %s", e)

    # Reindexar el AR para que el listado vea MRN/Paciente
    try:
        # Reindex completo: deja que el catálogo resuelva índices y metadatos
        instance.reindexObject()
    except Exception as e:
        logger.warning("[senaite.patient] reindexObject() falló: %s", e)

    return patient


def get_patient_fields(instance):
    """Extrae campos de Paciente desde el AR para alta/actualización."""
    mrn = safe_text(instance.getMedicalRecordNumberValue())

    # Campos básicos
    sex = safe_text(instance.getField("Sex").get(instance))
    gender = safe_text(instance.getField("Gender").get(instance))

    # Fecha de nacimiento (maneja estimado)
    dob_field = instance.getField("DateOfBirth")
    birthdate = dob_field.get_date_of_birth(instance)
    estimated = dob_field.get_estimated(instance)

    # Dirección
    address_val = instance.getField("PatientAddress").get(instance)
    address = {"type": "physical", "address": safe_text(address_val)} if address_val else None

    # Nombre compuesto (4 partes si el esquema lo expone)
    field = instance.getField("PatientFullName")
    firstname = safe_text(field.get_firstname(instance))
    middlename = safe_text(field.get_middlename(instance))
    lastname = safe_text(field.get_lastname(instance))
    maternal_lastname = u""
    if hasattr(field, "get_maternal_lastname"):
        maternal_lastname = safe_text(field.get_maternal_lastname(instance))

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
