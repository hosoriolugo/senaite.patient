# -*- coding: utf-8 -*-
from Products.Five.browser import BrowserView
from zope.interface import alsoProvides, noLongerProvides
from Products.CMFCore.utils import getToolByName
from zope.component import getMultiAdapter

import json
import logging

from senaite.patient.interfaces import ISenaitePatientLayer

logger = logging.getLogger("senaite.patient.ajax_ar_add_ext")

# Python 3: reemplazo de basestring
STRING_TYPES = (str, bytes)

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
            logger.warning("Ping delegation error: %r", e)
        return (u"WARN: AjaxARAddExt PASIVO; no pude resolver @@ajax_ar_add en el core. "
                u"Revisa registro de la vista base.")

    # Pequeñas páginas de diagnóstico (opcionales)
    def diag_env(self):
        has_layer = _has_patient_layer(self.request)
        core_ok = bool(self._core_view())
        return (u"AjaxARAddExt DIAG ENV\n"
                u"- ISenaitePatientLayer activo: %s\n"
                u"- Core @@ajax_ar_add resolvió: %s" % (has_layer, core_ok))

    def diag_widgetpaths(self):
        sampletype_keys = [
            "SampleType-0", "SampleType", "SampleTypes", "SampleTypes-0",
            "SampleType-0_uid", "SampleType-0:uid", "SampleType-0:title", "SampleType-0_dict",
        ]
        profile_keys = [
            "Profiles-0", "Profiles", "Profile", "profile",
            "Profiles-0_uid", "Profiles-0:uid",
        ]
        return (u"AjaxARAddExt DIAG WIDGETPATHS\n"
                u"- Claves SampleType candidatas: %s\n"
                u"- Claves Profile candidatas: %s" % (", ".join(sampletype_keys), ", ".join(profile_keys)))

    def diag_lookup(self):
        uid = self.request.get("profile_uid", "").strip()
        if not uid:
            return u"LOOKUP: falta parámetro profile_uid"
        st_uid, st_title = _resolve_sampletype_from_profile(self.context, uid)
        return u"LOOKUP: profile_uid=%s -> sampletype_uid=%s, title=%s" % (uid, st_uid, st_title)

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
            logger.warning("AjaxARAddExt: no se pudo resolver @@ajax_ar_add (core). Método=%s", method_name)
            return json.dumps({"success": False, "message": "Core AJAX view not found"})
        method = getattr(core, method_name, None)
        if not method:
            logger.warning("AjaxARAddExt: el core no expone método %s", method_name)
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
            # 0) Marca visible en logs actuales (WARNING)
            logger.warning("AjaxARAddExt: intercept recalculate_records")

            # 1) Encontrar el record/valores
            rec, vals = _locate_record_and_values(payload)
            if not vals:
                logger.warning("AjaxARAddExt: no hallé dict de valores en payload; devuelvo sin cambios.")
                return raw

            logger.warning("AjaxARAddExt: values_keys=%s", sorted(list(vals.keys()))[:50])

            # 2) ¿SampleType ya está?
            if _sampletype_is_set(vals):
                logger.warning("AjaxARAddExt: SampleType ya estaba seteado; no cambio nada.")
                return raw

            # 3) Extraer UID de Profile
            profile_uid = _extract_profile_uid(vals)
            logger.warning("AjaxARAddExt: profile_uid detectado=%r", profile_uid)

            if not profile_uid:
                logger.warning("AjaxARAddExt: no encontré profile seleccionado; no autocompleto SampleType.")
                return raw

            # 4) Resolver SampleType (UID/Title) desde el Profile
            st_uid, st_title = _resolve_sampletype_from_profile(self.context, profile_uid)
            logger.warning("AjaxARAddExt: resuelto SampleType (uid=%r, title=%r) desde profile %r",
                           st_uid, st_title, profile_uid)

            if not st_uid:
                logger.warning("AjaxARAddExt: el AnalysisProfile %r no tiene SampleType asociado.", profile_uid)
                return raw

            # 5) Setear en formatos comunes
            changed = _force_set_sampletype(vals, st_uid, st_title)
            if changed:
                logger.warning("AjaxARAddExt: SampleType autocompletado -> uid=%s, title=%s", st_uid, st_title)
                return _json_dumps(payload)

            logger.warning("AjaxARAddExt: no se cambió nada (posible clave distinta).")
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
            vals = rec

    return rec, vals


def _sampletype_is_set(vals):
    """True si hay algún valor de SampleType ya presente."""
    for key in vals.keys():
        if key.startswith("SampleType"):
            v = vals.get(key)
            if isinstance(v, STRING_TYPES) and (v or "").strip():
                return True
            if isinstance(v, dict) and (v.get("uid") or v.get("UID")):
                return True
            if vals.get("%s_uid" % key) or vals.get("%s:uid" % key):
                return True
    for key in ("SampleType-0", "SampleType"):
        v = vals.get(key)
        if isinstance(v, STRING_TYPES) and (v or "").strip():
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
    candidate_keys.append("profile")  # por si acaso

    for key in candidate_keys:
        v = vals.get(key)
        if isinstance(v, dict):
            return v.get("uid") or v.get("UID")
        if isinstance(v, STRING_TYPES) and (v or "").strip():
            return (v or "").strip()
        if isinstance(v, (list, tuple)) and v:
            first = v[0]
            if isinstance(first, dict):
                return first.get("uid") or first.get("UID")
            if isinstance(first, STRING_TYPES) and (first or "").strip():
                return (first or "").strip()

        uid_alt = vals.get("%s_uid" % key) or vals.get("%s:uid" % key)
        if isinstance(uid_alt, STRING_TYPES) and (uid_alt or "").strip():
            return (uid_alt or "").strip()

    return None


def _resolve_sampletype_from_profile(context, profile_uid):
    """Dado un UID de AnalysisProfile, intenta obtener el SampleType (uid, title)."""
    try:
        catalog = getToolByName(context, 'senaite_catalog_setup', None)
        if catalog is None:
            catalog = getToolByName(context, 'portal_catalog', None)

        if not catalog:
            logger.warning("AjaxARAddExt: no hay catálogo para resolver SampleType.")
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

    candidate_keys = [k for k in vals.keys() if k.startswith("SampleType-0")]
    if not candidate_keys:
        candidate_keys = [k for k in vals.keys() if k.startswith("SampleType")]
    if not candidate_keys:
        candidate_keys = ["SampleType-0", "SampleType"]

    def _ensure_all_formats(key):
        local_changed = False

        # 1) String UID preferido
        cur = vals.get(key)
        if not (isinstance(cur, STRING_TYPES) and cur):
            vals[key] = st_uid
            local_changed = True

        # 2) Claves auxiliares
        if vals.get("%s_uid" % key) != st_uid:
            vals["%s_uid" % key] = st_uid
            local_changed = True
        if vals.get("%s:uid" % key) != st_uid:
            vals["%s:uid" % key] = st_uid
            local_changed = True
        if vals.get("%s:title" % key) != st_title:
            vals["%s:title" % key] = st_title
            local_changed = True

        # 3) Dict espejo (compat)
        if not isinstance(cur, dict):
            vals["%s_dict" % key] = {"uid": st_uid, "UID": st_uid, "title": st_title}
            local_changed = True
        else:
            ref = cur or {}
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

    if not changed and "SampleType-0" not in vals:
        vals["SampleType-0"] = st_uid
        vals["SampleType-0_uid"] = st_uid
        vals["SampleType-0:uid"] = st_uid
        vals["SampleType-0:title"] = st_title
        vals["SampleType-0_dict"] = {"uid": st_uid, "UID": st_uid, "title": st_title}
        logger.debug("AjaxARAddExt: SampleType creado en SampleType-0 (no existía en payload).")
        changed = True

    return changed
