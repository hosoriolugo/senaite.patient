# -*- coding: utf-8 -*-
from senaite.core.browser.samples.ajax_ar_add import AjaxARAdd as CoreAjaxARAdd
from bika.lims import api
import logging
logger = logging.getLogger(__name__)

def _safe_lower(s):
    try: return s.strip().lower()
    except Exception: return None

def _get_profile_key(profile_obj):
    for getter in ("getProfileKey","getKeyword","getKey"):
        if hasattr(profile_obj, getter):
            try:
                val = getattr(profile_obj, getter)()
                if val: return val
            except Exception: pass
    return None

def _get_prefix(sampletype_obj):
    for getter in ("getPrefix","Prefix","prefix","get_prefix"):
        if hasattr(sampletype_obj, getter):
            try: return getattr(sampletype_obj, getter)()
            except Exception: pass
    return None

def _find_sampletype_uid_by_prefix(prefix_value):
    if not prefix_value: return None
    wanted = _safe_lower(prefix_value)
    setup_catalog = api.get_tool("senaite_catalog_setup")
    for brain in setup_catalog(portal_type="SampleType", is_active=True):
        try: st = brain.getObject()
        except Exception: continue
        pref = _get_prefix(st)
        if pref and _safe_lower(pref) == wanted:
            return st.UID()
    return None

class AjaxARAddExt(CoreAjaxARAdd):
    def recalculate_records(self):
        data = super(AjaxARAddExt, self).recalculate_records()
        records = data.get("records", [])
        for rec in records:
            if rec.get("SampleType"):  # ya seleccionado -> no tocar
                continue
            profiles = rec.get("Profiles") or rec.get("Profile") or []
            if not profiles: continue
            first = profiles[0]
            prof_uid = first.get("UID") if isinstance(first, dict) else first
            if not prof_uid: continue
            prof_obj = api.get_object_by_uid(prof_uid)
            if not prof_obj: continue
            key = _get_profile_key(prof_obj)
            if not key: continue
            st_uid = _find_sampletype_uid_by_prefix(key)
            if not st_uid: continue
            rec["SampleType"] = {"UID": st_uid}
            logger.info("INFOLABSA: asignado SampleType=%s por Prefijo='%s'", st_uid, key)
        return data
