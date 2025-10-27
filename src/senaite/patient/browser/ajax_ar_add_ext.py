# -*- coding: utf-8 -*-
# INFOLABSA: Auto-asignar SampleType por match Prefijo (SampleType) == Palabra clave (Perfil)
from Products.Five import BrowserView
from zope.interface import alsoProvides, noLongerProvides
from bika.lims import api
import logging, json

from senaite.patient.interfaces import ISenaitePatientLayer

logger = logging.getLogger(__name__)

def _safe_lower(s):
    try: return s.strip().lower()
    except Exception: return None

def _get_profile_key(profile_obj):
    for getter in ("getProfileKey", "getKeyword", "getKey"):
        if hasattr(profile_obj, getter):
            try:
                val = getattr(profile_obj, getter)()
                if val: return val
            except Exception:
                pass
    return None

def _get_prefix(sampletype_obj):
    for getter in ("getPrefix", "Prefix", "prefix", "get_prefix"):
        if hasattr(sampletype_obj, getter):
            try:
                return getattr(sampletype_obj, getter)()
            except Exception:
                pass
    return None

def _find_sampletype_uid_by_prefix(prefix_value):
    if not prefix_value: return None
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
    """Proxy del view core @@samples/ajax_ar_add.
       Expone TODOS los métodos que usa la UI y delega al core,
       pero modifica recalculate_records() para autollenar SampleType.
    """

    # ---------- util: conseguir el view del core sin nuestro layer ----------
    def _core(self):
        noLongerProvides(self.request, ISenaitePatientLayer)
        try:
            return self.context.restrictedTraverse('@@samples/ajax_ar_add')
        finally:
            alsoProvides(self.request, ISenaitePatientLayer)

    # ---------- métodos llamados por la UI (delegan al core) ----------
    def get_global_settings(self, *a, **kw):
        return self._core().get_global_settings(*a, **kw)

    def get_flush_settings(self, *a, **kw):
        return self._core().get_flush_settings(*a, **kw)

    def get_service(self, *a, **kw):
        return self._core().get_service(*a, **kw)

    def is_reference_value_allowed(self, *a, **kw):
        return self._core().is_reference_value_allowed(*a, **kw)

    def recalculate_prices(self, *a, **kw):
        # Precios no los tocamos; 100% core
        return self._core().recalculate_prices(*a, **kw)

    # ---------- único método con lógica INFOLABSA ----------
    def recalculate_records(self, *a, **kw):
        logger.info("INFOLABSA: override recalculate_records() - ENTER")
        base_resp = self._core().recalculate_records(*a, **kw)

        # Normaliza str(JSON) -> dict
        was_string = isinstance(base_resp, basestring)
        data = base_resp
        if was_string:
            try:
                data = json.loads(base_resp)
                logger.info("INFOLABSA: JSON decoded OK (len=%d)", len(base_resp))
            except Exception as e:
                logger.warn("INFOLABSA: JSON decode failed: %r", e)
                return base_resp  # no rompemos la UI

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

        # Devuelve en el mismo formato que vino
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
