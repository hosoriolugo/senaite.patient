# -*- coding: utf-8 -*-
from __future__ import absolute_import
import logging
import json
import unicodedata

from Products.Five.browser import BrowserView
from Products.CMFCore.utils import getToolByName

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Intento perezoso de localizar el AjaxARAdd del core (muchas rutas candidatas)
# -----------------------------------------------------------------------------
_CACHED_CORE = {"cls": None, "errors": []}

_CANDIDATES = (
    # SENAITE (distintas estructuras)
    "senaite.core.browser.samples.ajax_ar_add.AjaxARAdd",
    "senaite.core.browser.samples.ajax.AjaxARAdd",
    "senaite.core.browser.ajax_ar_add.AjaxARAdd",
    "senaite.core.browser.ajax.AjaxARAdd",
    "senaite.core.browser.analysisrequest.ajax_ar_add.AjaxARAdd",
    "senaite.core.browser.analysisrequest.ajax.AjaxARAdd",
    "senaite.core.browser.analysisrequests.ajax_ar_add.AjaxARAdd",
    "senaite.core.browser.analysisrequests.ajax.AjaxARAdd",
    "senaite.core.browser.client.ajax_ar_add.AjaxARAdd",
    "senaite.core.browser.client.ajax.AjaxARAdd",
    # BIKA
    "bika.lims.browser.samples.ajax_ar_add.AjaxARAdd",
    "bika.lims.browser.samples.ajax.AjaxARAdd",
    "bika.lims.browser.ajax_ar_add.AjaxARAdd",
    "bika.lims.browser.ajax.AjaxARAdd",
)

def _resolve_core():
    """Resuelve y cachea la clase AjaxARAdd del core sin romper el arranque."""
    if _CACHED_CORE["cls"] is not None:
        return _CACHED_CORE["cls"]

    errs = []
    for dotted in _CANDIDATES:
        try:
            module_path, cls_name = dotted.rsplit(".", 1)
            mod = __import__(module_path, fromlist=[cls_name])
            cls = getattr(mod, cls_name)
            _CACHED_CORE["cls"] = cls
            logger.info("INFOLABSA: Core AjaxARAdd localizado en %s", dotted)
            return cls
        except Exception as e:
            errs.append("%s -> %r" % (dotted, e))

    _CACHED_CORE["errors"] = errs
    logger.warn("INFOLABSA: NO se pudo localizar AjaxARAdd del core; "
                "operando en modo pasivo. Intentos:\n  - %s",
                "\n  - ".join(errs))
    return None


# -----------------------------------------------------------------------------
# Utils
# -----------------------------------------------------------------------------
def _norm_u(s):
    if s is None:
        return u""
    if not isinstance(s, unicode):
        try:
            s = s.decode("utf-8")
        except Exception:
            s = unicode(s)
    s = s.strip().lower()
    s = unicodedata.normalize('NFKD', s)
    s = u"".join([c for c in s if not unicodedata.combining(c)])
    return s

def _first_not_empty(*vals):
    for v in vals:
        if v:
            return v
    return None


# -----------------------------------------------------------------------------
# Clase extendida (tolerante si no hay core)
# -----------------------------------------------------------------------------
_CoreAjax = _resolve_core()
_Base = _CoreAjax if _CoreAjax is not None else BrowserView

class AjaxARAddExt(_Base):
    """Override para auto-asignar SampleType por Prefijo ↔ ProfileKey."""

    # Endpoint de diagnóstico
    def ping(self, *a, **kw):
        if _CACHED_CORE["cls"] is not None:
            return "OK: AjaxARAddExt activo (core={} )".format(
                _CACHED_CORE["cls"].__module__ + "." + _CACHED_CORE["cls"].__name__
            )
        return "WARN: AjaxARAddExt en modo pasivo; core no localizado.\n" + \
               "Intentos:\n  - " + "\n  - ".join(_CACHED_CORE["errors"])

    # Si no hay core, no intentamos sobreescribir nada más (modo pasivo)
    if _CoreAjax is not None:

        def recalculate_records(self, *args, **kwargs):
            logger.info("INFOLABSA: override recalculate_records() - ENTER")

            parent_result = super(AjaxARAddExt, self).recalculate_records(*args, **kwargs)

            data = None
            if isinstance(parent_result, basestring):
                try:
                    data = json.loads(parent_result)
                except Exception as e:
                    logger.warn("INFOLABSA: no se pudo parsear JSON del core: %r", e)
                    return parent_result
            elif isinstance(parent_result, dict):
                data = parent_result
            else:
                logger.warn("INFOLABSA: tipo de respuesta inesperado: %s", type(parent_result))
                return parent_result

            profile_key_norm = self._extract_profile_key_normalized(data)
            if not profile_key_norm:
                logger.info("INFOLABSA: sin ProfileKey detectable; no se autoasigna SampleType")
                return parent_result

            prefix_map = self._build_sampletype_prefix_map()
            if not prefix_map:
                logger.info("INFOLABSA: no hay SampleTypes activos; no se autoasigna")
                return parent_result

            match_info = prefix_map.get(profile_key_norm)
            logger.info("INFOLABSA: ProfileKey='%s' | match=%s",
                        profile_key_norm, bool(match_info))

            if not match_info:
                return parent_result

            st_uid, st_title = match_info

            recs = data.get("records") or data.get("data") or []
            changed = False
            for idx, rec in enumerate(recs):
                st_val = rec.get("SampleType")
                has_value = bool(st_val) and (isinstance(st_val, dict) and st_val.get("UID")) or \
                            (isinstance(st_val, basestring) and st_val.strip())
                if has_value:
                    continue
                rec["SampleType"] = {"UID": st_uid, "Title": st_title}
                changed = True
                logger.info("INFOLABSA: asignado SampleType en record #%s -> UID=%s Title='%s'",
                            idx, st_uid, st_title)

            if not changed:
                return parent_result

            if isinstance(parent_result, basestring):
                try:
                    return json.dumps(data)
                except Exception as e:
                    logger.warn("INFOLABSA: fallo al serializar JSON modificado: %r", e)
                    return parent_result
            else:
                return data

        # ---- helpers sólo si hay core (para evitar llamadas indebidas) ----
        def _extract_profile_key_normalized(self, data):
            recs = data.get("records") or data.get("data") or []
            for rec in recs:
                profiles = rec.get("Profiles") or rec.get("Profile") or []
                if isinstance(profiles, dict):
                    profiles = [profiles]
                for p in profiles:
                    key = _first_not_empty(
                        p.get("getProfileKey"),
                        p.get("ProfileKey"),
                        p.get("Keyword"),
                        p.get("Key"),
                        p.get("profile_key"),
                    )
                    if key:
                        return _norm_u(key)

            form = getattr(self.request, "form", {}) or {}
            profile_uids = set()
            for name, val in form.items():
                if not name.startswith("Profiles"):
                    continue
                if name.endswith("_uid"):
                    profile_uids.add(val)
                else:
                    if isinstance(val, basestring) and len(val) > 10 and "-" in val:
                        profile_uids.add(val)

            if profile_uids:
                uid = next(iter(profile_uids))
                key = self._get_profile_key_from_uid(uid)
                if key:
                    return _norm_u(key)

            return None

        def _get_profile_key_from_uid(self, uid):
            try:
                rc = getToolByName(self.context, "reference_catalog")
                obj = rc.lookupObject(uid)
                if obj is None:
                    return None
                getters = ("getProfileKey", "getKeyword", "getKey", "ProfileKey", "Keyword", "Key")
                for g in getters:
                    if hasattr(obj, g):
                        try:
                            return getattr(obj, g)()
                        except TypeError:
                            try:
                                return getattr(obj, g)
                            except Exception:
                                pass
                try:
                    field = obj.Schema().getField("ProfileKey")
                    if field:
                        return field.get(obj)
                except Exception:
                    pass
            except Exception as e:
                logger.warn("INFOLABSA: error leyendo Perfil por UID=%s: %r", uid, e)
            return None

        def _build_sampletype_prefix_map(self):
            result = {}
            try:
                setup = getToolByName(self.context, "senaite_catalog_setup")
                brains = setup.searchResults(
                    portal_type="SampleType",
                    is_active=True,
                    sort_on="sortable_title",
                    sort_order="ascending",
                )
                for b in brains:
                    try:
                        obj = b.getObject()
                    except Exception:
                        obj = None
                    if obj is None:
                        continue
                    prefix = None
                    title = b.Title
                    uid = b.UID
                    for accessor in ("getPrefix", "Prefix", "prefix"):
                        if hasattr(obj, accessor):
                            try:
                                prefix = getattr(obj, accessor)()
                            except TypeError:
                                try:
                                    prefix = getattr(obj, accessor)
                                except Exception:
                                    pass
                        if prefix:
                            break
                    if prefix is None:
                        try:
                            field = obj.Schema().getField("Prefix")
                            if field:
                                prefix = field.get(obj)
                        except Exception:
                            pass
                    prefix_norm = _norm_u(prefix)
                    if prefix_norm:
                        result[prefix_norm] = (uid, title)
            except Exception as e:
                logger.warn("INFOLABSA: error construyendo mapa de SampleType por Prefijo: %r", e)

            try:
                if result:
                    some = sorted(result.keys())[:10]
                    logger.info("INFOLABSA: Prefijos SampleType disponibles (ej.): %s", ", ".join(some))
                else:
                    logger.info("INFOLABSA: no se encontraron Prefijos de SampleType")
            except Exception:
                pass
            return result
