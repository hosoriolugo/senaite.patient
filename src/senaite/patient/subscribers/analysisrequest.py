# -*- coding: utf-8 -*-
#
# This file is part of SENAITE.PATIENT.
#
# SENAITE.PATIENT is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, version 2.
#
# ------------------------------------------------------------------------
# Subscribers for Analysis Requests
# Final adjusted version: always use field "MedicalRecordNumber" (normalized)
# and the 4-part fullname fields. Prevents dict errors and binds Patient to AR.
# ------------------------------------------------------------------------

from __future__ import absolute_import

from bika.lims import api
from senaite.core.behaviors import IClientShareableBehavior
from senaite.patient import api as patient_api
from senaite.patient import check_installed
from senaite.patient import logger

try:
    basestring
except NameError:
    basestring = str


def _safe_reindex(obj):
    """Reindex patient-related indexes in AR, tolerante a RequestContainer."""
    try:
        try:
            pt = api.get_portal_type(obj)
        except Exception:
            pt = None

        # Solo forzamos índices específicos para AR/Sample
        if pt in ("AnalysisRequest", "Sample"):
            idxs = ["getPatientUID", "getPatientFullName"]

            # Forzar MRN solo si el objeto realmente expone el campo/método
            has_field = hasattr(obj, "getField") and obj.getField("MedicalRecordNumber")
            has_accessor = hasattr(obj, "getMedicalRecordNumber")
            if has_field or has_accessor:
                # ¡Nombre correcto del índice!
                idxs.append("getMedicalRecordNumber")

            obj.reindexObject(idxs=idxs)
        else:
            obj.reindexObject()
    except Exception:
        # Fallback al reindex genérico
        try:
            obj.reindexObject()
        except Exception as e:
            logger.warning("[senaite.patient] Reindex fallback failed: %r", e)


def _normalize_mrn(value):
    """Ensure MRN is always a unicode string (never dict)."""
    if not value:
        return u""
    # dict payload from ReferenceWidget
    if isinstance(value, dict):
        for key in ("mrn", "MRN", "value", "text", "label", "title", "Title"):
            v = value.get(key)
            if isinstance(v, basestring) and v.strip():
                return api.safe_unicode(v.strip())
        return u""
    if isinstance(value, basestring):
        return api.safe_unicode(value.strip())
    return api.safe_unicode(str(value))


def _get_mrn_from_field(instance):
    """Lee y normaliza el MRN del campo 'MedicalRecordNumber'."""
    if not hasattr(instance, "getField"):
        return u""
    field = instance.getField("MedicalRecordNumber")
    if not field:
        return u""
    try:
        raw = field.get(instance)
    except Exception:
        return u""
    return _normalize_mrn(raw)


@check_installed(None)
def on_object_created(instance, event):
    """Event handler when a sample was created"""
    patient = update_patient(instance)
    if not patient:
        return

    if patient.getEmailReport():
        email = patient.getEmail()
        add_cc_email(instance, email)

    reg_key = "senaite.patient.share_patients"
    if api.get_registry_record(reg_key, default=False):
        client_uid = api.get_uid(instance.getClient())
        behavior = IClientShareableBehavior(patient)
        client_uids = behavior.getRawClients() or []
        if client_uid not in client_uids:
            client_uids.append(client_uid)
            behavior.setClients(client_uids)

    _safe_reindex(instance)


@check_installed(None)
def on_object_edited(instance, event):
    """Event handler when a sample was edited"""
    update_patient(instance)
    update_results_ranges(instance)
    _safe_reindex(instance)


def add_cc_email(sample, email):
    """add CC email recipient to sample"""
    current = sample.getCCEmails() or ""
    emails = [e.strip() for e in current.split(",") if e.strip()]
    new_email = (email or "").strip()
    if new_email and new_email not in emails:
        emails.append(new_email)
    sample.setCCEmails(",".join(emails))


def update_patient(instance):
    """Update or create Patient object for a given Analysis Request."""
    if not hasattr(instance, "getField"):
        return None

    # MRN normalizado (puede venir como dict del ReferenceWidget)
    mrn = _get_mrn_from_field(instance)
    if not mrn:
        return None

    # skip temporary MRN
    if hasattr(instance, "isMedicalRecordTemporary") and callable(instance.isMedicalRecordTemporary):
        try:
            if instance.isMedicalRecordTemporary():
                return None
        except Exception:
            pass

    patient = patient_api.get_patient_by_mrn(mrn, include_inactive=True)

    if patient is None:
        if patient_api.is_patient_allowed_in_client():
            container = instance.getClient()
        else:
            container = patient_api.get_patient_folder()

        if not patient_api.is_patient_creation_allowed(container):
            return None

        logger.info("Creating new Patient in '%s' with MRN: '%s'",
                    api.get_path(container), mrn)
        values = get_patient_fields(instance, mrn)
        try:
            patient = api.create(container, "Patient")
            patient_api.update_patient(patient, **values)
        except ValueError as exc:
            logger.error("%s", exc)
            logger.error("Failed to create patient for values: %r", values)
            raise exc

    # --- Enlace AR → Patient y persistencia de MRN en el AR ---
    if hasattr(instance, "setPatient"):
        try:
            instance.setPatient(patient)
            # Verificar que el vinculado fue exitoso
            linked_patient = instance.getPatient()
            if linked_patient:
                logger.info("Patient %s successfully linked to AR %s", 
                           linked_patient.getId(), instance.getId())
            else:
                logger.warning("setPatient succeeded but getPatient returned None for AR %s", 
                              instance.getId())
        except Exception as exc:
            logger.error("Failed to setPatient on AR %s: %r", instance.getId(), exc)

    # Persistir los valores en los campos del AR
    try:
        # Usar los setters adecuados para los campos
        if hasattr(instance, "setMedicalRecordNumberValue"):
            instance.setMedicalRecordNumberValue(mrn)
        
        if hasattr(instance, "setPatientFullName") and patient:
            fullname = patient.getFullname()
            if fullname:
                instance.setPatientFullName(fullname)
    except Exception as exc:
        logger.error("Failed to set patient fields on AR %s: %r", instance.getId(), exc)

    return patient


def get_patient_fields(instance, mrn=None):
    """Extract the patient fields from the sample"""
    mrn = _normalize_mrn(mrn)

    sex = instance.getField("Sex").get(instance)
    gender = instance.getField("Gender").get(instance)
    dob_field = instance.getField("DateOfBirth")
    birthdate = dob_field.get_date_of_birth(instance)
    estimated = dob_field.get_estimated(instance)
    address = instance.getField("PatientAddress").get(instance)

    # Obtener los 4 campos del nombre usando los métodos del campo PatientFullName
    fullname_field = instance.getField("PatientFullName")
    
    # Usar los métodos del campo para extraer los componentes individuales
    firstname = fullname_field.get_firstname(instance)
    middlename = fullname_field.get_middlename(instance)
    lastname = fullname_field.get_lastname(instance)
    maternallastname = fullname_field.get_maternal_lastname(instance)

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
        "maternal_lastname": api.safe_unicode(maternallastname),
    }


def update_results_ranges(sample):
    """Re-assigns results ranges when patient values change"""
    spec = sample.getSpecification()
    if spec:
        ranges = spec.getResultsRange()
        sample.setResultsRange(ranges, recursive=False)
