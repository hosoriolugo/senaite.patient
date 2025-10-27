# -*- coding: utf-8 -*-
# INFOLABSA: Auto-asignar SampleType por match Prefijo (SampleType) == Palabra clave (Perfil)
from bika.lims import api
import logging

logger = logging.getLogger(__name__)

# -------- Resolver AjaxARAdd del core con fallbacks (distintas rutas entre versiones) -----
CoreAjaxARAdd = None
_import_errors = []

def _try_import(dotted, attr):
    try:
        mod = __import__(dotted, fromlist=[attr])
        return getattr(mod, attr)
    except Exception as e:
        _import_errors.append("%s.%s -> %s" % (dotted, attr, repr(e)))
        return None

# Candidatos conocidos en SENAITE/Bika (según versión/paquete)
# Ordenados por probabilidad
for dotted, attr in [
    ("senaite.core.browser.samples.ajax", "AjaxARAdd"),
    ("senaite.core.browser.samples.ajax_ar_add", "AjaxARAdd"),
    ("senaite.core.browser.ajax", "AjaxARAdd"),
    ("senaite.core.browser.ajax_ar_add", "AjaxARAdd"),
    ("bika.lims.browser.samples.ajax", "AjaxARAdd"),
    ("bika.lims.browser.samples.ajax_ar_add", "AjaxARAdd"),
    ("bika.lims.browser.ajax", "AjaxARAdd"),
]:
    CoreAjaxARAdd = _try_import(dotted, attr)
    if CoreAjaxARAdd:
        logger.info("INFOLABSA: AjaxARAdd base resuelto desde %s.%s", dotted, attr)
        break

if not CoreAjaxARAdd:
    # No encontramos ninguna ruta; dejamos un error explícito con pistas
    raise ImportError(
        "INFOLABSA: No se pudo localizar AjaxARAdd en el core. Intentos:\n  - " +
        "\n  - ".join(_import_errors)
    )

# -----------------------------------------------------------------------------------------

def _safe_lower(s):
    try:
        return s.strip().lower()
    except Exception:
        return None

def _get_profile_key(profile_obj):
    # Cubre forks: getProfileKey (estándar), getKeyword, getKey
    for getter in ("getProfileKey", "getKeyword", "getKey"):
        if hasattr(profile_obj, getter):
            try:
                val = getattr(profile_obj, getter)()
                if val:
                    return val
            except Exception:
                pass
    return None

def _get_prefix(sampletype_obj):
    # Cubre forks: getPrefix, Prefix, prefix, get_prefix
    for getter in ("getPrefix", "Prefix", "prefix", "get_prefix"):
        if hasattr(sampletype_obj, getter):
            try:
                return getattr(sampletype_obj, getter)()
            except Exception:
                pass
    return None

def _find_sampletype_uid_by_prefix(prefix_value):
    if not prefix_value:
        return None
    wanted = _safe_lower(prefix_value)
    setup_catalog = api.get_tool("senaite_catalog_setup")
    # Traemos activos; comparamos en Python para no depender de índice
    for brain in setup_catalog(portal_type="SampleType", is_active=True):
        try:
            st = brain.getObject()
        except Exception:
            continue
        pref = _get_prefix(st)
        if pref and _safe_lower(pref) == wanted:
            return st.UID()
    return None


class AjaxARAddExt(CoreAjaxARAdd):
    """Post-procesa recalculate_records() para rellenar SampleType si hay match."""
    def recalculate_records(self):
        data = super(AjaxARAddExt, self).recalculate_records()
        try:
            records = data.get("records", [])
        except Exception:
            logger.warn("INFOLABSA: payload sin 'records'")
            return data

        for rec in records:
            # Si ya hay SampleType, no tocamos
            if rec.get("SampleType"):
                continue

            profiles = rec.get("Profiles") or rec.get("Profile") or []
            if not profiles:
                continue

            # Acepta dict {'UID': ...} o string UID
            first = profiles[0]
            prof_uid = first.get("UID") if isinstance(first, dict) else first
            if not prof_uid:
                continue

            prof_obj = api.get_object_by_uid(prof_uid)
            if not prof_obj:
                continue

            key = _get_profile_key(prof_obj)
            if not key:
                logger.info("INFOLABSA: Perfil %s sin clave/keyword; omitido", prof_uid)
                continue

            st_uid = _find_sampletype_uid_by_prefix(key)
            if not st_uid:
                logger.info("INFOLABSA: sin SampleType con Prefijo='%s'", key)
                continue

            rec["SampleType"] = {"UID": st_uid}
            logger.info("INFOLABSA: asignado SampleType UID=%s por Prefijo='%s'", st_uid, key)

        return data
