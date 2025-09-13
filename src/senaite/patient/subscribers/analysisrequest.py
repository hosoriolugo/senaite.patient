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
from senaite.core.behaviors import IClientShareableBehavior
from senaite.patient import api as patient_api
from senaite.patient import check_installed
from senaite.patient import logger


def _is_ar(obj):
    """True solo si es un AnalysisRequest (incluye retest/partition/secondary)."""
    try:
        return IAnalysisRequest.providedBy(obj)
    except Exception:
        return False


def _getattr_callable(obj, name, default=None):
    """Obtiene atributo si existe y es callable, si no devuelve default."""
    val = getattr(obj, name, None)
    if callable(val):
        return val
    return default


@check_installed(None)
def on_object_created(instance, event):
    """Se dispara al crear la muestra (AR)."""
    if not _is_ar(instance):
        return

    patient = update_patient(instance)

    # no patient creado cuando el MRN es temporal o no hay MRN
    if not patient:
        return

    # Añadir email del paciente a CC si corresponde
    if patient.getEmailReport():
        email = patient.getEmail()
        add_cc_email(instance, email)

    # Compartir patient con el cliente del AR si la opción está activa
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
    """Se dispara al editar la muestra (AR)."""
    if not _is_ar(instance):
        return
    update_patient(instance)
    update_results_ranges(instance)


def add_cc_email(sample, email):
    """Añade un destinatario CC al AR si no existe ya."""
    emails = sample.getCCEmails().split(",")
    if email in emails:
        return
    emails.append(email)
    emails = map(lambda e: e.strip(), emails)
    sample.setCCEmails(",".join(emails))


def update_patient(instance):
    """Crea/actualiza el Patient y asegura el enlace en el AR."""
    # Evitar cualquier caso raro (por ejemplo, during container events)
    if not _is_ar(instance):
        return None

    is_temp_fn = _getattr_callable(instance, "isMedicalRecordTemporary")
    if is_temp_fn and is_temp_fn():
        return None

    get_mrn_val = _getattr_callable(instance, "getMedicalRecordNumberValue")
    mrn = get_mrn_val() if get_mrn_val else None
    # Permitir vacío si la config no requiere pacientes, pero no hacemos nada
    if mrn is None or mrn == "":
        return None

    # Buscar Patient por MRN (incluye inactivos)
    patient = patient_api.get_patient_by_mrn(mrn, include_inactive=True)

    # Crear Patient si no existe
    if patient is None:
        if patient_api.is_patient_allowed_in_client():
            container = instance.getClient()
        else:
            container = patient_api.get_patient_folder()

        # Verificar permisos para crear Patient
        if not patient_api.is_patient_creation_allowed(container):
            logger.warn("Patient creation not allowed in '{}' for MRN '{}'"
                        .format(api.get_path(container), mrn))
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
            raise

    # ── Enlazar el AR con el Patient si aún no está enlazado ─────────────────
    set_mrn_ref = _getattr_callable(instance, "setMedicalRecordNumber")
    get_mrn_ref = _getattr_callable(instance, "getMedicalRecordNumber")
    needs_link = True

    try:
        current = get_mrn_ref() if get_mrn_ref else None
        if current:
            if isinstance(current, (list, tuple)):
                current = current[0] if current else None
            needs_link = api.get_uid(current) != api.get_uid(patient)
    except Exception:
        needs_link = True

    if set_mrn_ref and needs_link:
        try:
            set_mrn_ref(patient)
        except Exception as exc:
            logger.warn("Could not set MedicalRecordNumber reference: %s" % exc)

    # Opcional: actualizar el valor texto del MRN
    set_mrn_val = _getattr_callable(instance, "setMedicalRecordNumberValue")
    if set_mrn_val:
        try:
            pat_mrn = getattr(patient, "getMRN", None)
            pat_mrn = pat_mrn() if callable(pat_mrn) else getattr(patient, "mrn", mrn)
            set_mrn_val(pat_mrn or mrn)
        except Exception:
            pass

    # Reindexar para que el listado recoja MRN/Paciente
    try:
        instance.reindexObject()
    except Exception:
        try:
            api.reindex(instance)
        except Exception:
            logger.warn("Reindex after patient link skipped for {}".format(api.get_path(instance)))

    return patient


def get_patient_fields(instance):
    """Extrae los campos de paciente desde el AR para crear/actualizar Patient."""
    get_mrn_val = _getattr_callable(instance, "getMedicalRecordNumberValue")
    mrn = get_mrn_val() if get_mrn_val else None

    sex = instance.getField("Sex").get(instance) if instance.getField("Sex") else None
    gender = instance.getField("Gender").get(instance) if instance.getField("Gender") else None

    dob_field = instance.getField("DateOfBirth")
    if dob_field:
        birthdate = dob_field.get_date_of_birth(instance)
        estimated = dob_field.get_estimated(instance)
    else:
        birthdate = None
        estimated = False

    address_field = instance.getField("PatientAddress")
    address = address_field.get(instance) if address_field else None

    name_field = instance.getField("PatientFullName")
    if name_field:
        firstname = name_field.get_firstname(instance)
        middlename = name_field.get_middlename(instance)
        lastname = name_field.get_lastname(instance)
        # 🔹 Ajuste: soportar maternal_lastname también
        maternal_lastname = ""
        get_maternal = getattr(name_field, "get_maternal_lastname", None)
        if callable(get_maternal):
            maternal_lastname = get_maternal(instance)
    else:
        firstname = middlename = lastname = maternal_lastname = u""

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
        "maternal_lastname": api.safe_unicode(maternal_lastname),
    }


def update_results_ranges(sample):
    """Recalcula rangos de resultados después de cambiar datos del paciente."""
    spec = sample.getSpecification()
    if spec:
        ranges = spec.getResultsRange()
        sample.setResultsRange(ranges, recursive=False)
