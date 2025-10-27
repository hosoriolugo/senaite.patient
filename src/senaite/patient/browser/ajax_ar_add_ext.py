# -*- coding: utf-8 -*-
from Products.Five.browser import BrowserView
from zope.interface import alsoProvides, noLongerProvides
from zope.component import getMultiAdapter
from Products.CMFCore.utils import getToolByName

import json
import logging

from senaite.patient.interfaces import ISenaitePatientLayer

logger = logging.getLogger("senaite.patient.ajax_ar_add_ext")


# ------------------------------------------------------------------------------
# Utilidades de capa / delegación
# ------------------------------------------------------------------------------

def _has_patient_layer(request):
    return ISenaitePatientLayer.providedBy(request)


class AjaxARAddExt(BrowserView):
    """Wrapper para la vista 'samples/ajax_ar_add' del core.
    - Delegamos TODO al core para mantener funcionalidad original.
    - En recalculate_records, si SampleType está vacío, lo autocompletamos
      a partir del/los Profiles seleccionados.
    """

    # --------------------------------------------------------------------------
    # Diagnóstico
    # --------------------------------------------------------------------------
    def ping(self):
        active = False
        try:
            core = self._core_view()
            active = core is not None
        except Exception as e:
            logger.warn("Ping delegation error: %r", e)

        if active:
            return u"OK: AjaxARAddExt en modo ACTIVO; delegación al core OK."
        return (
            u"WARN: AjaxARAddExt en modo PASIVO; no pude resolver la vista base @@ajax_ar_add.\n"
            u"Esto no rompe el sitio, pero evita la delegación. Revisa el registro de la vista en el core."
        )

    # --------------------------------------------------------------------------
    # Resolución de la vista base del core (evitando recursión)
    # --------------------------------------------------------------------------
    def _core_view(self):
        """Obtiene @@ajax_ar_add del core removiendo temporalmente nuestro layer
        para que Zope no resuelva otra vez esta misma clase.
        """
        request = self.request
        removed = False
        if _has_patient_layer(request):
            noLongerProvides(request, ISenaitePatientLayer)
            removed = True
        try:
            core = self.context.restrictedTraverse('@@ajax_ar_add')
            return core
        finally:
            if removed:
                alsoProvides(request, ISenaitePatientLayer)

    def _delegate_json(self, method_name):
        """Invoca al método homónimo del core y devuelve su body JSON (texto)."""
        core = self._core_view()
        if not core:
            logger.warn("No se pudo resolver @@ajax_ar_add (core). Método: %s", method_name)
            return json.dumps({"success": False, "message": "Core AJAX view not found"})
        method = getattr(core, method_name, None)
        if not method:
            logger.warn("El core no expone el método %s", method_name)
            return json.dumps({"success": False, "message": "Core method not found: %s" % method_name})
        return method()

    # --------------------------------------------------------------------------
    # Métodos delegados 1:1 al core
    # --------------------------------------------------------------------------
    def get_global_settings(self):
        return self._delegate_json('get_global_settings')

    def get_flush_settings(self):
        return self._delegate_json('get_flush_settings')

    def recalculate_prices(self):
        return self._delegate_json('recalculate_prices')

    def get_service(self):
        return self._delegate_json('get_service')

    def is_reference_value_allowed(self):
        return self._delegate_json('is_reference_value_allowed')

    # --------------------------------------------------------------------------
    # recalculate_records con post-proceso de Sample Type
    # --------------------------------------------------------------------------
    def recalculate_records(self):
        raw = self._delegate_json('recalculate_records')

        try:
            payload = json.loads(raw)
        except Exception:
            logger.exception("Respuesta del core no es JSON. raw=%r", raw)
            return raw  # devolver tal cual para no romper

        try:
            # 1) ¿Hay que completar SampleType?
            if not _is_sampletype_already_set(payload):
                # 2) Obtener UID de AnalysisProfile seleccionado (si existe)
                profile_uid = _extract_profile_uid(payload)

                # 3) Resolver SampleType desde el Profile (catálogo setup)
                if profile_uid:
                    st_uid, st_title = _find_sampletype_from_profile(self.context, profile_uid)
                    if st_uid and st_title:
                        changed = _apply_sampletype_if_missing(payload, st_uid, st_title)
                        if changed:
                            logger.info(
                                "SampleType autocompletado desde Profile UID=%s -> %s",
                                profile_uid, st_title
                            )
        except Exception:
            logger.exception("Fallo en post-proceso SampleType; devuelvo JSON core intacto.")
            return raw

        return json.dumps(payload)


# ------------------------------------------------------------------------------
# Helpers: extracción / resolución / parcheo de JSON
# ------------------------------------------------------------------------------

def _first_record(payload):
    """Devuelve el primer registro del payload típico de recalculate_records."""
    if isinstance(payload, dict):
        recs = payload.get("records") or payload.get("data") or []
        if isinstance(recs, list) and recs:
            return recs[0]
        if isinstance(recs, dict):
            return recs
    return None


def _record_values(record):
    """Intenta localizar el dict con los valores del record (varía por versión)."""
    if not isinstance(record, dict):
        return None
    # Convenciones observadas en SENAITE/Bika:
    # - record.get("values")
    # - record.get("fields")
    # - el propio record puede ser el dict de campos
    for key in ("values", "fields"):
        if isinstance(record.get(key), dict):
            return record[key]
    if any(k for k in record.keys() if isinstance(k, basestring) and "-" in k):
        return record
    return None


def _is_sampletype_already_set(payload):
    """Verifica si ya hay SampleType definido en el payload (cubre varias claves)."""
    rec = _first_record(payload)
    vals = _record_values(rec)
    if not isinstance(vals, dict):
        return False

    # Variantes comunes de nombres de campo de referencia:
    candidates = [k for k in vals.keys() if k.startswith("SampleType") or k.startswith("Sampletype")]
    for key in candidates or ("SampleType-0", "SampleType", "Sampletype-0", "Sampletype"):
        v = vals.get(key)
        # Puede venir como dict con uid/title o como string uid
        if isinstance(v, dict) and v.get("uid"):
            return True
        if isinstance(v, basestring) and v.strip():
            return True
        # variantes des-serializadas por widgets:
        if vals.get("%s_uid" % key) or vals.get("%s:uid" % key):
            return True
    return False


def _extract_profile_uid(payload):
    """Intenta extraer el UID del AnalysisProfile seleccionado desde el payload.

    Cubre formatos típicos:
    - 'Profiles-0': {'uid': 'UID', 'title': '...'}
    - 'Profiles-0': 'UID'
    - 'Profiles': ['UID', ...]
    - 'Profile-0' / 'Profile' variantes
    """
    rec = _first_record(payload)
    vals = _record_values(rec) or {}
    if not isinstance(vals, dict):
        return None

    candidate_keys = []
    # Prioriza 'Profiles-0', luego otras variaciones
    candidate_keys.extend([k for k in vals.keys() if k.startswith("Profiles-0")])
    candidate_keys.extend([k for k in vals.keys() if k.startswith("Profiles")])
    candidate_keys.extend([k for k in vals.keys() if k.startswith("Profile")])

    for key in candidate_keys:
        v = vals.get(key)
        # dict {'uid': '...'}
        if isinstance(v, dict) and v.get("uid"):
            return v.get("uid")
        # string 'UID'
        if isinstance(v, basestring) and v.strip():
            return v.strip()
        # lista de dicts/strings
        if isinstance(v, (list, tuple)) and v:
            first = v[0]
            if isinstance(first, dict) and first.get("uid"):
                return first.get("uid")
            if isinstance(first, basestring) and first.strip():
                return first.strip()

        # a veces widgets guardan aparte:
        uid_alt = vals.get("%s_uid" % key) or vals.get("%s:uid" % key)
        if isinstance(uid_alt, basestring) and uid_alt.strip():
            return uid_alt.strip()

    return None


def _find_sampletype_from_profile(context, profile_uid):
    """Dado un UID de AnalysisProfile, intenta obtener su SampleType (uid, title)."""
    try:
        catalog = getToolByName(context, 'senaite_catalog_setup', None)
        if catalog is None:
            catalog = getToolByName(context, 'portal_catalog', None)

        if catalog is None:
            logger.warn("No hay catálogo disponible para resolver SampleType desde Profile.")
            return (None, None)

        # Buscar el profile por UID
        brains = catalog(UID=profile_uid)
        brain = brains[0] if brains else None
        obj = brain.getObject() if brain else None
        if obj is None:
            return (None, None)

        # Variaciones de API según versión:
        # - obj.getSampleType()
        # - obj.SampleType o obj.sampletype
        # - relation 'sampletype' y luego .to_object
        st_obj = None
        for getter in ("getSampleType", "SampleType", "sampletype"):
            if hasattr(obj, getter):
                st_obj = getattr(obj, getter)
                st_obj = st_obj() if callable(st_obj) else st_obj
                break

        # Relaciones (dexterity)
        if st_obj and hasattr(st_obj, "to_object"):
            st_obj = st_obj.to_object

        if st_obj is None and hasattr(obj, "schema"):
            # Archetypes: a veces field name es 'SampleType'
            try:
                st_obj = obj.getField("SampleType").get(obj)
            except Exception:
                pass

        if not st_obj:
            # Último intento: mirar en catalog metadata
            # Algunos catálogos exponen sampletype_uid y Title
            st_uid = getattr(brain, "sampletype_uid", None)
            st_title = getattr(brain, "sampletype_title", None)
            if st_uid and st_title:
                return (st_uid, st_title)
            return (None, None)

        # Extraer UID y Title del SampleType
        st_uid = getattr(st_obj, "UID", None)
        st_uid = st_uid() if callable(st_uid) else st_uid
        st_title = getattr(st_obj, "Title", None)
        st_title = st_title() if callable(st_title) else st_title

        if st_uid and st_title:
            return (st_uid, st_title)
        return (None, None)

    except Exception:
        logger.exception("Error resolviendo SampleType desde Profile UID=%s", profile_uid)
        return (None, None)


def _apply_sampletype_if_missing(payload, st_uid, st_title):
    """Inserta SampleType en el JSON si falta. Devuelve True si cambió algo."""
    if not (st_uid and st_title):
        return False

    changed = False
    rec = _first_record(payload)
    if not rec:
        return False

    # Localiza el dict de valores
    vals = _record_values(rec)
    if not isinstance(vals, dict):
        return False

    # Posibles claves para SampleType en el payload
    candidate_keys = []
    candidate_keys.extend([k for k in vals.keys() if k.startswith("SampleType-0")])
    candidate_keys.extend([k for k in vals.keys() if k.startswith("SampleType")])
    candidate_keys = candidate_keys or ["SampleType-0", "SampleType"]

    def _set_ref_value(key):
        """Setea valor de referencia en varios formatos que los widgets suelen aceptar."""
        # Formato dict
        if not isinstance(vals.get(key), dict):
            vals[key] = {"uid": st_uid, "title": st_title}
            return True

        # Si ya es dict, sólo ajusta si faltan datos
        cur = vals[key]
        updated = False
        if not cur.get("uid"):
            cur["uid"] = st_uid
            updated = True
        if not cur.get("title"):
            cur["title"] = st_title
            updated = True
        return updated

    # Intenta setear en la primera clave candidata que falte
    already = _is_sampletype_already_set(payload)
    if not already:
        for key in candidate_keys:
            if _set_ref_value(key):
                changed = True
                # Además, setea variantes *-uid para widgets que lo leen así
                vals["%s_uid" % key] = st_uid
                vals["%s:title" % key] = st_title
                break

    return changed
