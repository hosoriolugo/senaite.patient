# -*- coding: utf-8 -*-
from __future__ import absolute_import

import logging
import json
import unicodedata

from Products.CMFCore.utils import getToolByName

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Resolver dinámico del AjaxARAdd del core (SENAITE / Bika)
# ---------------------------------------------------------------------------
_CoreAjaxARAdd = None
_import_errors = []

for dotted in (
    # SENAITE (nuevas)
    "senaite.core.browser.samples.ajax_ar_add.AjaxARAdd",
    "senaite.core.browser.samples.ajax.AjaxARAdd",
    "senaite.core.browser.ajax_ar_add.AjaxARAdd",
    "senaite.core.browser.ajax.AjaxARAdd",
    # Bika (antiguas)
    "bika.lims.browser.samples.ajax_ar_add.AjaxARAdd",
    "bika.lims.browser.samples.ajax.AjaxARAdd",
    "bika.lims.browser.ajax_ar_add.AjaxARAdd",
    "bika.lims.browser.ajax.AjaxARAdd",
):
    try:
        module_path, cls_name = dotted.rsplit(".", 1)
        mod = __import__(module_path, fromlist=[cls_name])
        _CoreAjaxARAdd = getattr(mod, cls_name)
        logger.info("INFOLABSA: Core AjaxARAdd localizado en %s", dotted)
        break
    except Exception as e:
        _import_errors.append("%s -> %r" % (dotted, e))

if _CoreAjaxARAdd is None:
    raise ImportError(
        "INFOLABSA: No se pudo localizar AjaxARAdd en el core. Intentos:\n  - " +
        "\n  - ".join(_import_errors)
    )

logger.info("INFOLABSA: módulo ajax_ar_add_ext importado")

# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def _norm_u(s):
    """Normaliza una cadena a minúsculas, sin tildes y sin espacios externos.
    Soporta None/bytes/utf-8.
    """
    if s is None:
        return u""
    if not isinstance(s, unicode):
        try:
            s = s.decode("utf-8")
        except Exception:
            s = unicode(s)
    s = s.strip().lower()
    # quitar tildes
    s = unicodedata.normalize('NFKD', s)
    s = u"".join([c for c in s if not unicodedata.combining(c)])
    return s


def _first_not_empty(*vals):
    for v in vals:
        if v:
            return v
    return None


# ---------------------------------------------------------------------------
# Clase Extendida
# ---------------------------------------------------------------------------

class AjaxARAddExt(_CoreAjaxARAdd):
    """Override de AjaxARAdd para auto-asignar SampleType según Perfil.
    Coincidencia:  SampleType.Prefix == Profile.ProfileKey (normalizados)
    """

    # -----------------------------------------------------------------------
    # Endpoint de prueba para verificar que nuestro override está activo
    # Añade 'ping' a allowed_attributes en ZCML
    # -----------------------------------------------------------------------
    def ping(self, *a, **kw):
        return "OK: AjaxARAddExt activo"

    # -----------------------------------------------------------------------
    # Hook principal: intercepta la respuesta del core y autoasigna SampleType
    # -----------------------------------------------------------------------
    def recalculate_records(self, *args, **kwargs):
        logger.info("INFOLABSA: override recalculate_records() - ENTER")

        # 1) Delega al core para obtener la respuesta estándar
        parent_result = super(AjaxARAddExt, self).recalculate_records(*args, **kwargs)

        # 2) La respuesta del core puede ser dict o JSON (str)
        data = None
        if isinstance(parent_result, basestring):
            try:
                data = json.loads(parent_result)
            except Exception as e:
                logger.warn("INFOLABSA: no se pudo parsear JSON del core: %r", e)
                # Si no se puede parsear, devolvemos tal cual y salimos
                return parent_result
        elif isinstance(parent_result, dict):
            data = parent_result
        else:
            # tipo inesperado: no tocar
            logger.warn("INFOLABSA: tipo de respuesta inesperado: %s", type(parent_result))
            return parent_result

        # 3) Obtener ProfileKey (palabra clave) desde la respuesta o del request
        profile_key_norm = self._extract_profile_key_normalized(data)
        if not profile_key_norm:
            logger.info("INFOLABSA: sin ProfileKey detectable; no se autoasigna SampleType")
            return parent_result  # sin cambios

        # 4) Construir mapa de Prefijo (normalizado) -> (uid, title)
        prefix_map = self._build_sampletype_prefix_map()
        if not prefix_map:
            logger.info("INFOLABSA: no hay SampleTypes activos; no se autoasigna")
            return parent_result

        # 5) Buscar coincidencia exacta
        match_info = prefix_map.get(profile_key_norm)
        logger.info("INFOLABSA: ProfileKey='%s' | match=%s",
                    profile_key_norm, bool(match_info))

        if not match_info:
            # No match, no tocamos nada
            return parent_result

        st_uid, st_title = match_info

        # 6) Inyectar el SampleType en los records que vengan vacíos
        recs = data.get("records") or data.get("data") or []
        changed = False
        for idx, rec in enumerate(recs):
            # Estructuras posibles: SampleType vacío, None, {}, o campo ausente
            st_val = rec.get("SampleType")
            has_value = bool(st_val) and (isinstance(st_val, dict) and st_val.get("UID")) or \
                        (isinstance(st_val, basestring) and st_val.strip())
            if has_value:
                continue  # respetar selección manual/previa

            rec["SampleType"] = {"UID": st_uid, "Title": st_title}
            changed = True
            logger.info("INFOLABSA: asignado SampleType en record #%s -> UID=%s Title='%s'",
                        idx, st_uid, st_title)

        # 7) Devolver en mismo formato que el core nos entregó
        if not changed:
            return parent_result

        if isinstance(parent_result, basestring):
            try:
                return json.dumps(data)
            except Exception as e:
                logger.warn("INFOLABSA: fallo al serializar JSON modificado: %r", e)
                return parent_result  # fallback sin cambios
        else:
            return data

    # -----------------------------------------------------------------------
    # Helpers internos
    # -----------------------------------------------------------------------
    def _extract_profile_key_normalized(self, data):
        """Intenta obtener la Palabra clave (ProfileKey/Keyword/Key) del Perfil
        seleccionado. Varias estrategias para robustez:
          A) De los 'records' devueltos por el core (si el core la expone)
          B) Del request.form (valores de ReferenceWidget de Profiles-*)
          C) Consultando el objeto del Perfil por UID y leyendo su atributo
        """
        # A) Buscar dentro de records/data
        recs = data.get("records") or data.get("data") or []
        for rec in recs:
            # Posibles estructuras: "Profiles": [ { "UID":..., "getProfileKey":... }, ... ]
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

        # B) Extraer del request.form (widgets tipo Profiles-0, Profiles-0_uid, etc.)
        form = getattr(self.request, "form", {}) or {}
        profile_uids = set()

        for name, val in form.items():
            # perfiles suelen entrar como Profiles-0, Profiles-1_uid, etc.
            if not name.startswith("Profiles"):
                continue
            if name.endswith("_uid"):
                profile_uids.add(val)
            else:
                # algunos widgets mandan directamente el UID como valor principal
                if isinstance(val, basestring) and len(val) > 10 and "-" in val:
                    profile_uids.add(val)

        if profile_uids:
            # Tomamos el primero
            uid = next(iter(profile_uids))
            key = self._get_profile_key_from_uid(uid)
            if key:
                return _norm_u(key)

        # C) Nada encontrado
        return None

    def _get_profile_key_from_uid(self, uid):
        """Dado el UID de un Perfil, devuelve su ProfileKey/Keyword/Key."""
        try:
            rc = getToolByName(self.context, "reference_catalog")
            obj = rc.lookupObject(uid)
            if obj is None:
                return None
            # Intenta varios nombres de accessor/campo
            getters = ("getProfileKey", "getKeyword", "getKey", "ProfileKey", "Keyword", "Key")
            for g in getters:
                if hasattr(obj, g):
                    try:
                        return getattr(obj, g)()
                    except TypeError:
                        # podría ser campo, no callable
                        try:
                            return getattr(obj, g)
                        except Exception:
                            pass
            # Mirar en schema si fuera Archetypes
            for g in ("ProfileKey", "Keyword", "Key"):
                try:
                    # AT schema getField
                    field = obj.Schema().getField(g)
                    if field:
                        return field.get(obj)
                except Exception:
                    pass
        except Exception as e:
            logger.warn("INFOLABSA: error leyendo Perfil por UID=%s: %r", uid, e)
        return None

    def _build_sampletype_prefix_map(self):
        """Devuelve dict: prefix_norm -> (uid, title) para SampleTypes activos."""
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

                # Obtener Prefijo y Título
                prefix = None
                title = b.Title
                uid = b.UID

                # Accesores posibles
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

                # Archetypes (por si acaso)
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

        # Log de depuración (lista compacta de prefijos)
        try:
            if result:
                some = sorted(result.keys())[:10]
                logger.info("INFOLABSA: Prefijos SampleType disponibles (ej.): %s", ", ".join(some))
            else:
                logger.info("INFOLABSA: no se encontraron Prefijos de SampleType")
        except Exception:
            pass

        return result
