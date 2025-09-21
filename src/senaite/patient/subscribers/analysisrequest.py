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
from senaite.core.behaviors import IClientShareableBehavior
from senaite.patient import api as patient_api
from senaite.patient import check_installed
from senaite.patient import logger

# Eventos para filtrar o reconocer
try:
    from zope.container.interfaces import IContainerModifiedEvent
except Exception:
    IContainerModifiedEvent = None

try:
    from zope.lifecycleevent.interfaces import IObjectInitializedEvent, IObjectModifiedEvent
except Exception:
    IObjectInitializedEvent = None
    IObjectModifiedEvent = None


def _unwrap(obj):
    """Devuelve el objeto real (sin wrappers de adquisición como RequestContainer)."""
    # 1) API de senaite/bika si existe
    try:
        real = api.get_object(obj)
        if real is not None:
            return real
    except Exception:
        pass
    # 2) Adquisición clásica
    try:
        from Acquisition import aq_inner, aq_base
        return aq_base(aq_inner(obj))
    except Exception:
        return obj


def _is_analysis_request(obj):
    """True si el objeto (desenvuelto) es un AnalysisRequest."""
    o = _unwrap(obj)
    # Vía interfaz “oficial”, si está presente en este entorno
    try:
        from bika.lims.interfaces import IAnalysisRequest
        return IAnalysisRequest.providedBy(o)
    except Exception:
        # Fallback por atributos característicos del AR
        return (
            hasattr(o, "isMedicalRecordTemporary") and
            hasattr(o, "getMedicalRecordNumberValue") and
            hasattr(o, "getSpecification")
        )


@check_installed(None)
def on_object_created(instance, event):
    """Se crea un AR (sample)."""
    instance = _unwrap(instance)
    if not _is_analysis_request(instance):
        return

    patient = update_patient(instance)

    if not patient:
        return

    # Añadir email del paciente a CC si aplica
    if patient.getEmailReport():
        email = patient.getEmail()
        add_cc_email(instance, email)

    # Compartir paciente con usuarios del cliente si está habilitado
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
    """Se edita un AR (sample)."""
    # Ignorar modificaciones de contenedores (ruido de creación/copia)
    if IContainerModifiedEvent is not None and IContainerModifiedEvent.providedBy(event):
        return

    # El evento puede ser IObjectInitializedEvent (como en tu log) o IObjectModifiedEvent;
    # en ambos casos necesitamos el objeto real, no el RequestContainer.
    instance = _unwrap(instance)
    if not _is_analysis_request(instance):
        return

    update_patient(instance)
    update_results_ranges(instance)


def add_cc_email(sample, email):
    emails = sample.getCCEmails().split(",")
    if email in emails:
        return
    emails.append(email)
    emails = map(lambda e: e.strip(), emails)
    sample.setCCEmails(",".join(emails))


def update_patient(instance):
    """Mantiene la lógica nativa, pero blindada contra wrappers."""
    instance = _unwrap(instance)
    if not _is_analysis_request(instance):
        return None

    # Algunos wrappers no exponen el método; evitamos el AttributeError
    try:
        if instance.isMedicalRecordTemporary():
            return None
    except AttributeError:
        return None

    mrn = instance.getMedicalRecordNumberValue()
    if mrn is None:
        return None

    patient = patient_api.get_patient_by_mrn(mrn, include_inactive=True)

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
    return patient


def get_patient_fields(instance):
    instance = _unwrap(instance)
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
        address = {"type": "physical", "address": api.safe_unicode(address)}

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
    sample = _unwrap(sample)
    spec = sample.getSpecification()
    if spec:
        ranges = spec.getResultsRange()
        sample.setResultsRange(ranges, recursive=False)
