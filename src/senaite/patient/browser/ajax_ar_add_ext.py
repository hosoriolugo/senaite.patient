# -*- coding: utf-8 -*-
# INFOLABSA: Auto-asignar SampleType por match Prefijo (SampleType) == Palabra clave (Perfil)
from Products.Five import BrowserView
from zope.interface import alsoProvides, noLongerProvides
from bika.lims import api
import logging

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


class AjaxARAddExt(BrowserView):
    """Proxy del view core @@samples/ajax_ar_add con post-procesado INFOLABSA.

    Estrategia:
      - Quitamos temporalmente nuestro layer del request
      - Resolvemos el view original por traversal @@samples/ajax_ar_add
      - Llamamos recalculate_records() del core
      - Restauramos el layer
      - Post-procesamos 'records' para autocompletar SampleType
    """

    def recalculate_records(self):
        # 1) Desactivar *temporalmente* nuestro layer para no resolvernos a nosotros mismos
        noLongerProvides(self.request, ISenaitePatientLayer)
        try:
            # 2) Resolver el view original del core y ejecutar su lógica
            base_view = self.context.restrictedTraverse('@@samples/ajax_ar_add')
            data = base_view.recalculate_records()
        finally:
            # 3) Restaurar nuestro layer
            alsoProvides(self.request, ISenaitePatientLayer)

        # 4) Post-procesar: autocompletar SampleType si hay match Perfil↔Prefijo
        try:
            records = data.get("records", [])
        except Exception:
            logger.warn("INFOLABSA: payload sin 'records'")
            return data

        for rec in records:
            # Si ya hay Tipo de muestra, no tocamos
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
