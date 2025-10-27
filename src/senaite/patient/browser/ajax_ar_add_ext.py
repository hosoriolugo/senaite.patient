# -*- coding: utf-8 -*-
from Products.Five.browser import BrowserView
from zope.interface import alsoProvides, noLongerProvides
from Products.CMFCore.utils import getToolByName
from zope.component import getMultiAdapter

import json
import logging

from senaite.patient.interfaces import ISenaitePatientLayer

logger = logging.getLogger("senaite.patient.ajax_ar_add_ext")


# ------------------------------------------------------------------------------
# Helpers de capa
# ------------------------------------------------------------------------------
def _has_patient_layer(request):
    return ISenaitePatientLayer.providedBy(request)


def _json_loads(raw):
    try:
        return json.loads(raw)
    except Exception:
        logger.exception("AjaxARAddExt: respuesta del core no es JSON; raw[0:300]=%r", raw[:300])
        return None


def _json_dumps(payload):
    try:
        return json.dumps(payload)
    except Exception:
        logger.exception("AjaxARAddExt: no pude serializar payload; lo retorno tal cual.")
        return payload


# ------------------------------------------------------------------------------
# Vista Wrapper
# ------------------------------------------------------------------------------
class AjaxARAddExt(BrowserView):
    """Wrapper para @@ajax_ar_add del core con post-proceso:
       - Mantiene funcionalidad original.
       - Autocompleta SampleType si está vacío usando el AnalysisProfile.
    """

    # -------------------------- Diagnóstico -----------------------------------
    def ping(self):
        try:
            core = self._core_view()
            if core:
                return u"OK: AjaxARAddExt ACTIVO; delegación al core @@ajax_ar_add OK."
        except Exception as e:
            logger.warn("Ping delegation error: %r", e)
        return (u"WARN: AjaxARAddExt PASIVO; no pude resolver @@ajax_ar_add en el core. "
                u"Revisa registro de la vista base.")

    # -------------------------- Delegación segura ------------------------------
    def _core_view(self):
        """Obtiene @@ajax_ar_add del core removiendo temporalmente nuestro layer
        para evitar recursión.
        """
        request = self.request
        removed = False
        if _has_patient_layer(request):
            noLongerProvides(request, ISenaitePatientLayer)
            removed = True
        try:
            return self.context.restrictedTraverse('@@ajax_ar_add')
        finally:
            if removed:
                alsoProvides(request, ISenaitePatientLayer)

    def _delegate_raw(self, method_name):
        core = self._core_view()
        if not core:
            logger.warn("AjaxARAddExt: no se pudo resolver @@ajax_ar_add (core). Método=%s", method_name)
            return json.dumps({"success": False, "message": "Core AJAX view not found"})
        method = getattr(core, method_name, None)
        if not method:
            logger.warn("AjaxARAddExt: el core no expone método %s", method_name)
            return json.dumps({"success": False, "message": "Core method not found: %s" % method_name})
        return method()

    # -------------------------- Métodos 1:1 -----------------------------------
    def get_global_settings(self):
        return self._delegate_raw('get_global_settings')

    def get_flush_settings(self):
        return self._delegate_raw('get_flush_settings')

    def recalculate_prices(self):
        return self._delegate_raw('recalculate_prices')

    def get_service(self):
        return self._delegate_raw('get_service')

    def is_reference_value_allowed(self):
        return self._delegate_raw('is_reference_value_allowed')

    # -------------------------- Post-proceso clave -----------------------------
    def recalculate_records(self):
        """Delegamos al core y luego, si SampleType está vacío, lo autocompletamos
        desde el AnalysisProfile seleccionado.
        """
        raw = self._delegate_raw('recalculate_records')
        payload = _json_loads(raw)
        if payload is None:
            return raw

        try:
            # Log mínimo para entender la forma del JSON en tu ambiente
            top_keys = list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__
            logger.debug("AjaxARAddExt: recalculate_records payload top_keys=%r", top_keys)

            # 1) Encontrar el record/valores
            rec, vals = _locate_record_and_values(payload)
            if not vals:
                logger.info("AjaxARAddExt: no hallé dict de valores en payload; devuelvo sin cambios.")
                return raw

            # 2) ¿SampleType ya está?
            if _sampletype_is_set(vals):
                logger.debug("AjaxARAddExt: SampleType ya estaba seteado; no cambio nada.")
                return raw

            # 3) Extraer UID de Profile
            profile_uid = _extract_profile_uid(vals)
            logger.debug("AjaxARAddExt: profile_uid detectado=%r", profile_uid)

            if not profile_uid:
                logger.info("AjaxARAddExt: no encontré profile seleccionado; no autocompleto SampleType.")
                return raw

            # 4) Resolver SampleType (UID/Title) desde el Profile
            st_uid, st_title = _resolve_sampletype_from_profile(self.context, profile_uid)
            logger.debug("AjaxARAddExt: resuelto SampleType (uid=%r, title=%r) desde profile %r",
                         st_uid, st_title, profile_uid)

            if not st_uid:
                logger.info("AjaxARAddExt: el AnalysisProfile %r no tiene SampleType asociado.", profile_uid)
                return raw

            # 5) Setear en TODOS los formatos comunes
            changed = _force_set_sampletype(vals, st_uid, st_title)
            if changed:
                logger.info("AjaxARAddExt: SampleType autocompletado -> uid=%s, title=%s", st_uid, st_title)
                return _json_dumps(payload)

            logger.debug("AjaxARAddExt: no se cambió nada (posiblemente el widget espera otra clave).")
            return raw

        except Exception:
            logger.exception("AjaxARAddExt: fallo en post-proceso de SampleType; devuelvo JSON del core intacto.")
            return raw


# ------------------------------------------------------------------------------
# Utilidades de JSON/payload
# ------------------------------------------------------------------------------
def _locate_record_and_values(payload):
    """Devuelve (record_dict, values_dict) o (None, None)."""
    rec = None
    vals = None

    if isinstance(payload, dict):
        # formatos frecuentes: payload["records"] = [ { "values": {...} } ]
        container = payload.get("records") or payload.get("data")
        if isinstance(container, list) and container:
            rec = container[0]
        elif isinstance(container, dict):
            rec = container
        elif isinstance(payload.get("values"), dict):
            rec = payload

    if isinstance(rec, dict):
        if isinstance(rec.get("values"), dict):
            vals = rec.get("values")
        elif isinstance(rec.get("fields"), dict):
            vals = rec.get("fields")
        else:
            # fallback: el record en sí mismo es el dict de campos
            vals = rec

    return rec, vals


def _sampletype_is_set(vals):
    """True si hay algún valor de SampleType ya presente."""
    for key in vals.keys():
        if key.startswith("SampleType"):
            v = vals.get(key)
            if isinstance(v, basestring) and v.strip():
                return True
            if isinstance(v, dict) and (v.get("uid") or v.get("UID")):
                return True
            if vals.get("%s_uid" % key) or vals.get("%s:uid" % key):
                return True
    # claves típicas
    for key in ("SampleType-0", "SampleType"):
        v = vals.get(key)
        if isinstance(v, basestring) and v.strip():
            return True
        if isinstance(v, dict) and (v.get("uid") or v.get("UID")):
            return True
        if vals.get("%s_uid" % key) or vals.get("%s:uid" % key):
            return True
    return False


def _extract_profile_uid(vals):
    """Intenta extraer el UID del AnalysisProfile seleccionado desde vals."""
    candidate_keys = []
    candidate_keys.extend([k for k in vals.keys() if k.startswith("Profiles-0")])
    candidate_keys.extend([k for k in vals.keys() if k.startswith("Profiles")])
    candidate_keys.extend([k for k in vals.keys() if k.startswith("Profile")])

    for key in candidate_keys:
        v = vals.get(key)
        # dict {'uid': '...'} o {'UID': '...'}
        if isinstance(v, dict):
            return v.get("uid") or v.get("UID")
        # string 'UID'
        if isinstance(v, basestring) and v.strip():
            return v.strip()
        # lista de dicts/strings
        if isinstance(v, (list, tuple)) and v:
            first = v[0]
            if isinstance(first, dict):
                return first.get("uid") or first.get("UID")
            if isinstance(first, basestring) and first.strip():
                return first.strip()

        # variantes widgets
        uid_alt = vals.get("%s_uid" % key) or vals.get("%s:uid" % key)
        if isinstance(uid_alt, basestring) and uid_alt.strip():
            return uid_alt.strip()

    return None


def _resolve_sampletype_from_profile(context, profile_uid):
    """Dado un UID de AnalysisProfile, intenta obtener el SampleType (uid, title)."""
    try:
        catalog = getToolByName(context, 'senaite_catalog_setup', None)
        if catalog is None:
            catalog = getToolByName(context, 'portal_catalog', None)

        if not catalog:
            logger.warn("AjaxARAddExt: no hay catálogo para resolver SampleType.")
            return (None, None)

        brains = catalog(UID=profile_uid)
        if not brains:
            return (None, None)

        brain = brains[0]
        obj = brain.getObject() if hasattr(brain, "getObject") else None
        st_obj = None

        # 1) API común
        for getter in ("getSampleType", "SampleType", "sampletype"):
            if obj and hasattr(obj, getter):
                st_obj = getattr(obj, getter)
                st_obj = st_obj() if callable(st_obj) else st_obj
                break

        # 2) Relaciones (dexterity)
        if st_obj and hasattr(st_obj, "to_object"):
            st_obj = st_obj.to_object

        # 3) Archetypes (field)
        if not st_obj and obj and hasattr(obj, "schema"):
            try:
                field = obj.getField("SampleType")
                if field:
                    st_obj = field.get(obj)
            except Exception:
                pass

        # 4) Metadatos del catálogo (última chance)
        if not st_obj:
            st_uid = getattr(brain, "sampletype_uid", None)
            st_title = getattr(brain, "sampletype_title", None)
            if st_uid and st_title:
                return (st_uid, st_title)
            return (None, None)

        # Extraer UID/Title
        st_uid = getattr(st_obj, "UID", None)
        st_uid = st_uid() if callable(st_uid) else st_uid
        st_title = getattr(st_obj, "Title", None)
        st_title = st_title() if callable(st_title) else st_title

        if st_uid and st_title:
            return (st_uid, st_title)
        return (None, None)

    except Exception:
        logger.exception("AjaxARAddExt: error resolviendo SampleType desde Profile UID=%s", profile_uid)
        return (None, None)


def _force_set_sampletype(vals, st_uid, st_title):
    """Setea SampleType en todos los formatos usuales. Devuelve True si cambió algo."""
    changed = False

    # Claves candidatas observadas en UI
    candidate_keys = [k for k in vals.keys() if k.startswith("SampleType-0")]
    if not candidate_keys:
        candidate_keys = [k for k in vals.keys() if k.startswith("SampleType")]
    if not candidate_keys:
        candidate_keys = ["SampleType-0", "SampleType"]

    def _ensure_all_formats(key):
        local_changed = False

        # 1) Formato preferido por varios widgets: **string UID**
        if not (isinstance(vals.get(key), basestring) and vals.get(key)):
            vals[key] = st_uid
            local_changed = True

        # 2) Claves auxiliares frecuentes
        if vals.get("%s_uid" % key) != st_uid:
            vals["%s_uid" % key] = st_uid
            local_changed = True
        if vals.get("%s:uid" % key) != st_uid:
            vals["%s:uid" % key] = st_uid
            local_changed = True
        if vals.get("%s:title" % key) != st_title:
            vals["%s:title" % key] = st_title
            local_changed = True

        # 3) Dict de compatibilidad (algunos builds lo consumen)
        if not isinstance(vals.get(key), dict):
            # si ya lo pusimos string, añadimos además el dict espejo:
            vals["%s_dict" % key] = {"uid": st_uid, "UID": st_uid, "title": st_title}
            local_changed = True
        else:
            ref = vals.get(key) or {}
            if not ref.get("uid"):
                ref["uid"] = st_uid
                local_changed = True
            if not ref.get("UID"):
                ref["UID"] = st_uid
                local_changed = True
            if not ref.get("title"):
                ref["title"] = st_title
                local_changed = True
            vals[key] = ref

        return local_changed

    for key in candidate_keys:
        if _ensure_all_formats(key):
            logger.debug("AjaxARAddExt: SampleType seteado en clave '%s'", key)
            changed = True
            break

    # Si no hubo candidate_keys en el payload, creamos explícitamente SampleType-0
    if not changed and "SampleType-0" not in vals:
        vals["SampleType-0"] = st_uid
        vals["SampleType-0_uid"] = st_uid
        vals["SampleType-0:uid"] = st_uid
        vals["SampleType-0:title"] = st_title
        vals["SampleType-0_dict"] = {"uid": st_uid, "UID": st_uid, "title": st_title}
        logger.debug("AjaxARAddExt: SampleType creado en SampleType-0 (no existía en payload).")
        changed = True

    return changed
