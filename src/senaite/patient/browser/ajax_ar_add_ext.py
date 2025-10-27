# -*- coding: utf-8 -*-
from Products.Five.browser import BrowserView
from zope.interface import alsoProvides, noLongerProvides
from Products.CMFCore.utils import getToolByName
from zope.publisher.browser import BrowserView as ZopeBrowserView  # por si hiciera falta
from zope.component import getMultiAdapter  # no usado, pero mantenido por compat

import json
import logging

from senaite.patient.interfaces import ISenaitePatientLayer

# Compat Py2/Py3
try:
    basestring  # type: ignore
except NameError:  # Py3
    basestring = str  # type: ignore

logger = logging.getLogger("senaite.patient.ajax_ar_add_ext")

# ------------------------------------------------------------------------------#
# Helpers de capa / JSON
# ------------------------------------------------------------------------------#
def _has_patient_layer(request):
    return ISenaitePatientLayer.providedBy(request)


def _json_loads(raw):
    try:
        return json.loads(raw)
    except Exception:
        logger.exception(
            "AjaxARAddExt: respuesta del core no es JSON; raw[0:300]=%r", raw[:300]
        )
        return None


def _json_dumps(payload):
    try:
        return json.dumps(payload)
    except Exception:
        logger.exception("AjaxARAddExt: no pude serializar payload; lo retorno tal cual.")
        return payload


# ------------------------------------------------------------------------------#
# Vista Wrapper
# ------------------------------------------------------------------------------#
class AjaxARAddExt(BrowserView):
    """Wrapper para @@ajax_ar_add del core con post-proceso:
       - Mantiene funcionalidad original.
       - Autocompleta SampleType si está vacío usando el AnalysisProfile.
       - Expone endpoints de diagnóstico para inspección controlada.
    """

    # -------------------------- Diagnóstico -----------------------------------#
    def ping(self):
        try:
            core = self._core_view()
            if core:
                return u"OK: AjaxARAddExt ACTIVO; delegación al core @@ajax_ar_add OK."
        except Exception as e:
            try:
                logger.warn("Ping delegation error: %r", e)  # Py2
            except Exception:
                logger.warning("Ping delegation error: %r", e)  # Py3
        return (u"WARN: AjaxARAddExt PASIVO; no pude resolver @@ajax_ar_add en el core. "
                u"Revisa registro de la vista base.")

    def diag_env(self):
        """Muestra información mínima del entorno para confirmar capa y resolución del core."""
        request = self.request
        has_layer = _has_patient_layer(request)
        core = self._core_view()
        msg = [
            u"AjaxARAddExt DIAG ENV",
            u"- ISenaitePatientLayer activo: %s" % has_layer,
            u"- Core @@ajax_ar_add resolvió: %s" % bool(core),
        ]
        return u"\n".join(msg)

    def diag_widgetpaths(self):
        """Devuelve las rutas de claves típicas que miramos en values (SampleType y Profile)."""
        keys_sample = [
            "SampleType-0", "SampleType", "SampleTypes", "SampleTypes-0",
            "SampleType-0_uid", "SampleType-0:uid", "SampleType-0:title", "SampleType-0_dict",
        ]
        keys_profile = [
            "Profiles-0", "Profiles", "Profile", "profile",
            "Profiles-0_uid", "Profiles-0:uid",
        ]
        msg = [
            u"AjaxARAddExt DIAG WIDGETPATHS",
            u"- Claves SampleType candidatas: %s" % ", ".join(keys_sample),
            u"- Claves Profile candidatas: %s" % ", ".join(keys_profile),
        ]
        return u"\n".join(msg)

    def diag_lookup(self):
        """Prueba de resolución: ?profile_uid=<UID> → muestra SampleType (uid, title)."""
        profile_uid = (self.request.form.get("profile_uid") or u"").strip()
        if not profile_uid:
            return u"USO: @@ajax_ar_add_ext_diag_lookup?profile_uid=<UID>"
        st_uid, st_title = _resolve_sampletype_from_profile(self.context, profile_uid)
        return u"LOOKUP: profile_uid=%s -> sampletype_uid=%s, title=%s" % (
            profile_uid, st_uid, st_title
        )

    # -------------------------- Delegación segura ------------------------------#
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

    # -------------------------- Métodos 1:1 -----------------------------------#
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

    # -------------------------- Post-proceso clave -----------------------------#
    def recalculate_records(self):
        """Delegamos al core y luego, si SampleType está vacío, lo autocompletamos
        desde el AnalysisProfile seleccionado. Soporta múltiples records.
        """
        raw = self._delegate_raw('recalculate_records')
        payload = _json_loads(raw)
        if payload is None:
            return raw

        try:
            changed_any = False
            for idx, vals in _iter_values_dicts(payload):
                # LOG a nivel INFO para ver qué claves llegan realmente
                try:
                    logger.info("AjaxARAddExt: [%s] values_keys=%r", idx, sorted(list(vals.keys())))
                except Exception:
                    logger.info("AjaxARAddExt: [%s] values_keys=<no-listable>", idx)

                if _sampletype_is_set(vals):
                    logger.debug("AjaxARAddExt: [%s] SampleType ya estaba seteado; sin cambios.", idx)
                    continue

                profile_uid = _extract_profile_uid(vals)
                logger.info("AjaxARAddExt: [%s] profile_uid detectado=%r", idx, profile_uid)
                if not profile_uid:
                    logger.info("AjaxARAddExt: [%s] sin profile seleccionado; no autocompleto SampleType.", idx)
                    continue

                st_uid, st_title = _resolve_sampletype_from_profile(self.context, profile_uid)
                logger.info("AjaxARAddExt: [%s] SampleType resuelto desde profile %r -> uid=%r, title=%r",
                            idx, profile_uid, st_uid, st_title)
                if not st_uid:
                    logger.info("AjaxARAddExt: [%s] AnalysisProfile %r sin SampleType asociado.", idx, profile_uid)
                    continue

                if _force_set_sampletype(vals, st_uid, st_title):
                    changed_any = True
                    logger.info("AjaxARAddExt: [%s] SampleType autocompletado -> uid=%s, title=%s",
                                idx, st_uid, st_title)

            if changed_any:
                return _json_dumps(payload)

            logger.debug("AjaxARAddExt: no se realizaron cambios; devuelvo JSON del core intacto.")
            return raw

        except Exception:
            logger.exception("AjaxARAddExt: fallo en post-proceso de SampleType; devuelvo JSON del core intacto.")
            return raw


# ------------------------------------------------------------------------------#
# Utilidades de JSON/payload
# ------------------------------------------------------------------------------#
def _iter_values_dicts(payload):
    """Yield (index_str, values_dict) para cada record encontrado en el payload."""
    if isinstance(payload, dict):
        container = payload.get("records")
        if container is None:
            container = payload.get("data")

        if isinstance(container, list):
            for i, rec in enumerate(container):
                vals = _values_from_record(rec)
                if isinstance(vals, dict):
                    yield (str(i), vals)
            return

        if isinstance(container, dict):
            for k, rec in container.items():
                vals = _values_from_record(rec)
                if isinstance(vals, dict):
                    yield (str(k), vals)
            return

        vals = _values_from_record(payload)
        if isinstance(vals, dict):
            yield ("0", vals)


def _values_from_record(rec):
    if not isinstance(rec, dict):
        return None
    if isinstance(rec.get("values"), dict):
        return rec.get("values")
    if isinstance(rec.get("fields"), dict):
        return rec.get("fields")
    return rec


def _sampletype_is_set(vals):
    """True si hay algún valor de SampleType ya presente (varias variantes)."""
    for key in list(vals.keys()):
        if key.startswith("SampleType"):
            v = vals.get(key)
            if isinstance(v, basestring) and v.strip():
                return True
            if isinstance(v, dict) and (v.get("uid") or v.get("UID")):
                return True
            if vals.get("%s_uid" % key) or vals.get("%s:uid" % key):
                return True

    for key in ("SampleType-0", "SampleType", "SampleTypes", "SampleTypes-0"):
        v = vals.get(key)
        if isinstance(v, basestring) and v.strip():
            return True
        if isinstance(v, dict) and (v.get("uid") or v.get("UID")):
            return True
        if vals.get("%s_uid" % key) or vals.get("%s:uid" % key):
            return True
        if isinstance(v, (list, tuple)) and v:
            first = v[0]
            if isinstance(first, basestring) and first.strip():
                return True
            if isinstance(first, dict) and (first.get("uid") or first.get("UID")):
                return True
    return False


def _extract_profile_uid(vals):
    """Intenta extraer el UID del AnalysisProfile seleccionado desde vals."""
    candidate_keys = []
    candidate_keys.extend([k for k in vals.keys() if k.startswith("Profiles-0")])
    candidate_keys.extend([k for k in vals.keys() if k.startswith("Profiles")])
    candidate_keys.extend([k for k in vals.keys() if k.startswith("Profile")])
    candidate_keys.extend([k for k in vals.keys() if k.lower().startswith("profile")])

    for key in candidate_keys:
        v = vals.get(key)
        if isinstance(v, dict):
            if v.get("uid"): return v.get("uid")
            if v.get("UID"): return v.get("UID")
        if isinstance(v, basestring) and v.strip():
            return v.strip()
        if isinstance(v, (list, tuple)) and v:
            first = v[0]
            if isinstance(first, dict):
                if first.get("uid"): return first.get("uid")
                if first.get("UID"): return first.get("UID")
            if isinstance(first, basestring) and first.strip():
                return first.strip()
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

        for getter in ("getSampleType", "SampleType", "sampletype", "sample_type", "getSampletype"):
            if obj and hasattr(obj, getter):
                st_obj = getattr(obj, getter)
                st_obj = st_obj() if callable(st_obj) else st_obj
                if st_obj:
                    break

        if st_obj and hasattr(st_obj, "to_object"):
            st_obj = st_obj.to_object

        if not st_obj and obj and hasattr(obj, "schema"):
            try:
                field = obj.getField("SampleType")
                if field:
                    st_obj = field.get(obj)
            except Exception:
                pass

        if not st_obj:
            st_uid = getattr(brain, "sampletype_uid", None)
            st_title = getattr(brain, "sampletype_title", None)
            if st_uid and st_title:
                return (st_uid, st_title)
            return (None, None)

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
    """Setea SampleType en muchos formatos usuales. Devuelve True si cambió algo."""
    changed = False

    candidate_keys = [k for k in vals.keys() if k.startswith("SampleType-0")]
    candidate_keys += [k for k in vals.keys() if k == "SampleType"]
    candidate_keys += [k for k in vals.keys() if k.startswith("SampleType")]
    if not candidate_keys:
        candidate_keys = ["SampleType-0", "SampleType"]

    def _ensure_all_formats(key):
        local_changed = False

        cur = vals.get(key)
        if not (isinstance(cur, basestring) and cur.strip()):
            vals[key] = st_uid
            local_changed = True

        if vals.get("%s_uid" % key) != st_uid:
            vals["%s_uid" % key] = st_uid
            local_changed = True
        if vals.get("%s:uid" % key) != st_uid:
            vals["%s:uid" % key] = st_uid
            local_changed = True
        if vals.get("%s:title" % key) != st_title:
            vals["%s:title" % key] = st_title
            local_changed = True

        if not isinstance(cur, dict):
            vals["%s_dict" % key] = {"uid": st_uid, "UID": st_uid, "title": st_title}
            local_changed = True
        else:
            ref = cur or {}
            wrote = False
            if not ref.get("uid"):
                ref["uid"] = st_uid; wrote = True
            if not ref.get("UID"):
                ref["UID"] = st_uid; wrote = True
            if not ref.get("title"):
                ref["title"] = st_title; wrote = True
            if wrote:
                vals[key] = ref
                local_changed = True

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

    if "SampleTypes" in vals and not vals.get("SampleTypes"):
        vals["SampleTypes"] = [st_uid]
        changed = True

    return changed
