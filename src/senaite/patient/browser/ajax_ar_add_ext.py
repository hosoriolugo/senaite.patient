# -*- coding: utf-8 -*-
# INFOLABSA: Auto-asignar SampleType por match Prefijo (SampleType) == Palabra clave (Perfil)
from Products.Five import BrowserView
from zope.interface import alsoProvides, noLongerProvides
from bika.lims import api
import logging
import json

# IMPORTA tu layer (el mismo del ZCML)
from senaite.patient.interfaces import ISenaitePatientLayer

logger = logging.getLogger(__name__)

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
    for brain in setup_catalog(portal_type="SampleType", is_active=True):
        try:
            st = brain.getObject()
        except Exception:
            continue
        pref = _get_prefix(st)
        if pref and _safe_lower(pref) == wanted:
            return st.UID()
    return None


class AjaxARAddExt(BrowserView):
    """Proxy del view core @@samples/ajax_ar_add con post-procesado INFOLABSA."""
    def recalculate_records(self):
        logger.info("INFOLABSA: override recalculate_records() - ENTER")

        # 1) Desactivar temporalmente nuestro layer para resolver el core
        noLongerProvides(self.request, ISenaitePatientLayer)
        try:
            base_view = self.context.restrictedTraverse('@@samples/ajax_ar_add')
            base_resp = base_view.recalculate_records()
        finally:
            alsoProvides(self.request, ISenaitePatientLayer)

        # 2) Normalizar: str(JSON) -> dict
        original_type = type(base_resp).__name__
        logger.info("INFOLABSA: core recalculate_records() returned type=%s", original_type)

        was_string = False
        data = base_resp
        if isinstance(base_resp, basestring):
            was_string = True
            try:
                data = json.loads(base_resp)
                logger.info("INFOLABSA: JSON decoded OK (len=%d)", len(base_resp))
            except Exception as e:
                logger.warn("INFOLABSA: JSON decode failed: %r", e)
                # Si falla, devolvemos lo original para no romper la UI
                return base_resp

        # 3) Post-procesar records
        try:
            records = data.get("records", [])
            logger.info("INFOLABSA: records count=%d", len(records))
        except Exception:
            logger.warn("INFOLABSA: payload sin 'records'; returning base response")
            return base_resp

        changed = False
        for idx, rec in enumerate(records):
            if rec.get("SampleType"):
                logger.info("INFOLABSA: rec[%d] SampleType ya presente -> skip", idx)
                continue

            profiles = rec.get("Profiles") or rec.get("Profile") or []
            if not profiles:
                logger.info("INFOLABSA: rec[%d] sin Profiles/Profile -> skip", idx)
                continue

            # Acepta dict {'UID': ...} o string UID
            first = profiles[0]
            prof_uid = first.get("UID") if isinstance(first, dict) else first
            if not prof_uid:
                logger.info("INFOLABSA: rec[%d] perfil sin UID -> skip", idx)
                continue

            prof_obj = api.get_object_by_uid(prof_uid)
            if not prof_obj:
                logger.info("INFOLABSA: rec[%d] perfil UID=%s no resuelve -> skip", idx, prof_uid)
                continue

            key = _get_profile_key(prof_obj)
            if not key:
                logger.info("INFOLABSA: rec[%d] perfil UID=%s sin clave/keyword -> skip", idx, prof_uid)
                continue

            st_uid = _find_sampletype_uid_by_prefix(key)
            if not st_uid:
                logger.info("INFOLABSA: rec[%d] sin SampleType con Prefijo='%s' -> skip", idx, key)
                continue

            rec["SampleType"] = {"UID": st_uid}
            changed = True
            logger.info("INFOLABSA: rec[%d] asignado SampleType UID=%s por Prefijo='%s'", idx, st_uid, key)

        # 4) Devolver en el mismo formato que trajo el core
        if was_string:
            try:
                out = json.dumps(data)
                logger.info("INFOLABSA: returning JSON string (changed=%s, len=%d)", changed, len(out))
                return out
            except Exception as e:
                logger.warn("INFOLABSA: JSON encode failed: %r; returning base response", e)
                return base_resp
        else:
            logger.info("INFOLABSA: returning dict (changed=%s)", changed)
            return data
