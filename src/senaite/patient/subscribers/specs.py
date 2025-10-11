# -*- coding: utf-8 -*-
from Products.CMFCore.utils import getToolByName
from zope.lifecycleevent.interfaces import IObjectAddedEvent
from bika.lims import api, logger

# ----------------- utilidades robustas de catálogo -----------------

def _get_catalogs(portal):
    """Devuelve (catalogo, es_setup_catalog) con fallback seguro."""
    cat = None
    try:
        # preferido
        cat = getToolByName(portal, 'bika_setup_catalog', None)
    except Exception:
        cat = None

    if cat:
        return cat, True

    # Fallback: portal_catalog
    try:
        pcat = getToolByName(portal, 'portal_catalog', None)
    except Exception:
        pcat = None

    return pcat, False


def _search_specs(cat, is_setup, query):
    """Abstrae búsqueda sobre el catálogo disponible."""
    if not cat:
        return []

    # setup_catalog y portal_catalog soportan llamadas tipo cat(**kwargs)
    try:
        brains = cat(**query)
    except TypeError:
        # Algunos catálogos exponen searchResults
        try:
            brains = cat.searchResults(**query)
        except Exception:
            brains = []
    except Exception:
        brains = []

    return brains or []


def _obj_uid(obj, attr_name, default=None):
    """Obtiene UID desde getXxxUID() si existe; si no, intenta getXxx(); si no, default."""
    try:
        getter_uid = getattr(obj, attr_name, None)
        if callable(getter_uid):
            return getter_uid()
    except Exception:
        pass
    # intenta getXxx sin UID y de ahí un UID
    base = attr_name.replace("UID", "")
    try:
        getter = getattr(obj, base, None)
        val = getter() if callable(getter) else None
        if val and hasattr(val, "UID"):
            return val.UID()
    except Exception:
        pass
    return default


def _spec_matches(spec_obj, service_uid, client_uid, sampletype_uid, method_uid):
    """Valida en Python si la Specification coincide (para el fallback
    cuando portal_catalog no tiene todos los índices). Se relaja
    progresivamente si el spec no define alguno de los campos."""
    try:
        s_uid = (_obj_uid(spec_obj, "getServiceUID")
                 or _obj_uid(spec_obj, "getService", default=None))
        if s_uid and service_uid and s_uid != service_uid:
            return False

        c_uid = _obj_uid(spec_obj, "getClientUID") or None
        if c_uid and client_uid and c_uid != client_uid:
            return False

        st_uid = _obj_uid(spec_obj, "getSampleTypeUID") or None
        if st_uid and sampletype_uid and st_uid != sampletype_uid:
            return False

        m_uid = _obj_uid(spec_obj, "getMethodUID") or None
        if m_uid and method_uid and m_uid != method_uid:
            return False

        return True
    except Exception:
        return False


# ----------------- lógica de auto-aplicación de Specs -----------------

def _find_matching_spec(portal, analysis, ar):
    """Busca la Specification más específica disponible, con fallback de catálogo."""
    cat, is_setup = _get_catalogs(portal)
    if not cat:
        logger.warn("[AutoSpec] No hay catálogo disponible (bika_setup_catalog ni portal_catalog).")
        return None

    # Contexto
    try:
        service_uid = analysis.getServiceUID()
    except Exception:
        service_uid = None

    try:
        client_uid = ar.aq_parent.UID() if hasattr(ar.aq_parent, 'UID') else None
    except Exception:
        client_uid = None

    try:
        sampletype_uid = analysis.getSampleTypeUID()
    except Exception:
        sampletype_uid = None

    try:
        method_uid = analysis.getMethodUID()
    except Exception:
        method_uid = None

    # Estrategia: busca por especificidad decreciente
    queries = [
        dict(portal_type='Specification',
             getServiceUID=service_uid,
             getClientUID=client_uid,
             getSampleTypeUID=sampletype_uid,
             getMethodUID=method_uid),
        dict(portal_type='Specification',
             getServiceUID=service_uid,
             getClientUID=client_uid,
             getSampleTypeUID=sampletype_uid),
        dict(portal_type='Specification',
             getServiceUID=service_uid,
             getClientUID=client_uid),
        dict(portal_type='Specification',
             getServiceUID=service_uid),
        dict(portal_type='Specification'),
    ]

    for q in queries:
        brains = _search_specs(cat, is_setup, q)
        if not brains:
            continue

        # Si estamos en portal_catalog (fallback), puede que no existan los índices
        # getXxxUID → filtramos en Python.
        for brain in brains:
            try:
                spec = brain.getObject()
            except Exception:
                continue
            if _spec_matches(spec, service_uid, client_uid, sampletype_uid, method_uid):
                return spec

    logger.info("[AutoSpec] Sin Specification para service=%s client=%s sampletype=%s method=%s",
                service_uid, client_uid, sampletype_uid, method_uid)
    return None


def _apply_spec(analysis, spec):
    """Asigna la Specification al análisis/AR respetando API de SENAITE 2.6."""
    try:
        if hasattr(analysis, 'setSpecification'):
            analysis.setSpecification(spec)
        else:
            aspec = getattr(analysis, 'getAnalysisSpec', lambda: None)()
            if aspec and hasattr(aspec, 'setSpecification'):
                aspec.setSpecification(spec)
        analysis.reindexObject()
        return True
    except Exception as e:
        logger.warn("[AutoSpec] No se pudo asignar Spec a %s: %r", analysis.getId(), e)
        return False


def apply_specs_for_ar(ar, event):
    """Al crear el AR, recorre sus análisis y aplica la Spec correcta a cada uno."""
    if not IObjectAddedEvent.providedBy(event):
        return
    portal = api.get_portal()
    analyses = getattr(ar, 'getAnalyses', lambda: [])() or []
    for an in analyses:
        spec = _find_matching_spec(portal, an, ar)
        if spec:
            ok = _apply_spec(an, spec)
            logger.info("[AutoSpec] %s -> %s [%s]",
                        getattr(spec, 'Title', lambda: spec)(),
                        an.Title(), "OK" if ok else "FAIL")


def apply_spec_for_analysis(analysis, event):
    """Si se añade un análisis después, también aplicamos Spec automáticamente."""
    if not IObjectAddedEvent.providedBy(event):
        return
    # Localiza el AR
    ar = getattr(analysis, 'getAnalysisRequest', lambda: None)()
    if not ar:
        parent = getattr(analysis, 'aq_parent', None)
        if parent and getattr(parent, 'portal_type', '') == 'AnalysisRequest':
            ar = parent
    if not ar:
        return

    portal = api.get_portal()
    spec = _find_matching_spec(portal, analysis, ar)
    if spec:
        ok = _apply_spec(analysis, spec)
        logger.info("[AutoSpec] %s -> %s [%s]",
                    getattr(spec, 'Title', lambda: spec)(),
                    analysis.Title(), "OK" if ok else "FAIL")
