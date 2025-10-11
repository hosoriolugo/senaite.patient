# -*- coding: utf-8 -*-
from Products.CMFCore.utils import getToolByName
from zope.lifecycleevent.interfaces import IObjectAddedEvent, IObjectModifiedEvent
from bika.lims import api, logger

# -------------------------------------------------------------------
# SOPORTE A AT y DX
# -------------------------------------------------------------------

# Tipos de Spec que soportamos:
PORTAL_TYPES_SPECS = (
    # AT clásico
    'Specification',
    # DX moderno (varía por build; incluimos alias comunes)
    'DynamicAnalysisSpec',
    'dynamic_analysisspec',
)

# Carpetas candidatas donde pueden vivir las Specs:
SPEC_FOLDERS_CANDIDATES = (
    # AT:
    ('bika_setup', 'specifications'),
    ('bika_setup', 'bika_specifications'),
    ('bika_setup', 'Specifications'),
    # DX:
    ('setup', 'dynamicanalysisspecs'),
    ('setup', 'dynamic_analysisspecs'),
)


# -------------------------------------------------------------------
# UTILIDADES ROBUSTAS (no rompen si algo falta)
# -------------------------------------------------------------------

def _get_portal_catalog(portal):
    """Devuelve portal_catalog si existe (o None)."""
    try:
        return getToolByName(portal, 'portal_catalog', None)
    except Exception:
        return None


def _search(cat, query):
    """Intenta cat(**q) o searchResults(**q) y nunca rompe."""
    if not cat:
        return []
    try:
        res = cat(**query) or []
    except TypeError:
        try:
            res = cat.searchResults(**query) or []
        except Exception:
            res = []
    except Exception:
        res = []
    return res


def _obj_uid(obj, attr_name, default=None):
    """UID por getXxxUID() o getXxx().UID() o campo llano *_uid (DX)."""
    # 1) getXxxUID()
    try:
        getter_uid = getattr(obj, attr_name, None)
        if callable(getter_uid):
            return getter_uid()
    except Exception:
        pass
    # 2) getXxx().UID() o string
    base = attr_name.replace("UID", "")
    try:
        getter = getattr(obj, base, None)
        val = getter() if callable(getter) else None
        if val and hasattr(val, "UID"):
            return val.UID()
        from Products.CMFPlone.utils import safe_unicode
        if isinstance(val, basestring):
            return safe_unicode(val)
    except Exception:
        pass
    # 3) Campo llano DX: <base>_uid
    try:
        val = getattr(obj, base + "_uid", None)
        if isinstance(val, basestring):
            return val
    except Exception:
        pass
    return default


def _spec_matches(spec_obj, service_uid, client_uid, sampletype_uid, method_uid):
    """Filtra en Python; soporta AT/DX con getters o campos llanos.
    OJO: en DX la elección real de fila se hace por Keyword/edad/sexo
    dentro del adaptador dinámico; aquí solo evitamos Specs obvias que no aplican.
    """
    try:
        s_uid = (_obj_uid(spec_obj, "getServiceUID")
                 or _obj_uid(spec_obj, "getService")
                 or getattr(spec_obj, "service_uid", None))
        if s_uid and service_uid and s_uid != service_uid:
            return False

        c_uid = (_obj_uid(spec_obj, "getClientUID")
                 or getattr(spec_obj, "client_uid", None))
        if c_uid and client_uid and c_uid != client_uid:
            return False

        st_uid = (_obj_uid(spec_obj, "getSampleTypeUID")
                  or getattr(spec_obj, "sampletype_uid", None))
        if st_uid and sampletype_uid and st_uid != sampletype_uid:
            return False

        m_uid = (_obj_uid(spec_obj, "getMethodUID")
                 or getattr(spec_obj, "method_uid", None))
        if m_uid and method_uid and m_uid != method_uid:
            return False

        return True
    except Exception:
        return False


def _iter_specs_by_traversal(portal):
    """Recorre AT y DX: bika_setup/specifications y setup/dynamicanalysisspecs."""
    try:
        for root_name, folder_name in SPEC_FOLDERS_CANDIDATES:
            root = getattr(portal, root_name, None)
            if not root:
                continue
            folder = getattr(root, folder_name, None)
            if not folder:
                continue
            try:
                for obj in folder.objectValues():
                    pt = getattr(obj, 'portal_type', '')
                    if pt in PORTAL_TYPES_SPECS:
                        yield obj
            except Exception:
                continue
    except Exception:
        return


# -------------------------------------------------------------------
# SELECTOR DE SPEC: PRIORIZA DX ("Especificaciones dinámicas de análisis")
# -------------------------------------------------------------------

def _prefer_dx_spec(portal, analysis, ar):
    """Intenta devolver una Spec DX adecuada. Si hay una sola, úsala.
    Si hay varias, heurísticas simples: título, cliente."""
    setup = getattr(portal, "setup", None)
    if not setup:
        return None
    dx_folder = (getattr(setup, "dynamicanalysisspecs", None)
                 or getattr(setup, "dynamic_analysisspecs", None))
    if not dx_folder:
        return None

    dx_specs = [o for o in dx_folder.objectValues()
                if getattr(o, "portal_type", "") in PORTAL_TYPES_SPECS]

    if not dx_specs:
        return None
    if len(dx_specs) == 1:
        return dx_specs[0]

    # Heurística por título (tu ejemplo: "Quimica 3 Elementos")
    wanted_titles = (u"quimica 3 elementos", u"química 3 elementos")
    for obj in dx_specs:
        try:
            t = getattr(obj, "Title", lambda: u"")()
            if isinstance(t, basestring) and t.strip().lower() in wanted_titles:
                return obj
        except Exception:
            pass

    # Heurística por cliente (si la Spec DX almacena client_uid)
    client_uid = None
    try:
        client_uid = ar.aq_parent.UID() if hasattr(ar.aq_parent, 'UID') else None
    except Exception:
        client_uid = None
    if client_uid:
        for obj in dx_specs:
            cx = getattr(obj, "client_uid", None) or _obj_uid(obj, "getClientUID")
            if cx and cx == client_uid:
                return obj

    # Si nada matchea, devuelve la primera para que el adaptador resuelva filas
    return dx_specs[0]


# -------------------------------------------------------------------
# BÚSQUEDA DE SPEC
# -------------------------------------------------------------------

def _find_matching_spec(portal, analysis, ar):
    """Devuelve la Spec para el análisis:
       1) Prioriza DX (DynamicAnalysisSpec) en /setup/dynamicanalysisspecs
       2) Si no hay DX, intenta AT en /bika_setup/specifications
       3) Si no hay nada, devuelve None
    Nota: no dependemos de índices inexistentes del catálogo; hacemos
    traversal y filtrado en Python.
    """
    # A veces el análisis recién creado aún no tiene Servicio/Keyword;
    # en ese caso dejamos que IObjectModifiedEvent reintente.
    try:
        service_uid = analysis.getServiceUID()
    except Exception:
        service_uid = None
    if not service_uid:
        return None

    # 1) Preferir DX
    spec = _prefer_dx_spec(portal, analysis, ar)
    if spec:
        return spec

    # 2) Fallback a AT en bika_setup/specifications
    bsetup = getattr(portal, "bika_setup", None)
    if bsetup:
        for name in ("specifications", "bika_specifications", "Specifications"):
            at_folder = getattr(bsetup, name, None)
            if not at_folder:
                continue
            at_specs = [obj for obj in at_folder.objectValues()
                        if getattr(obj, "portal_type", "") == "Specification"]
            if at_specs:
                # Si hay varias, filtra suavemente por contexto
                client_uid = None
                sampletype_uid = None
                method_uid = None
                try:
                    client_uid = ar.aq_parent.UID() if hasattr(ar.aq_parent, 'UID') else None
                    sampletype_uid = analysis.getSampleTypeUID()
                    method_uid = analysis.getMethodUID()
                except Exception:
                    pass
                for cand in at_specs:
                    if _spec_matches(cand, service_uid, client_uid, sampletype_uid, method_uid):
                        return cand
                return at_specs[0]

    # 3) Último recurso: traversal genérico por todas las carpetas candidatas
    for cand in _iter_specs_by_traversal(portal):
        # Acepta la primera; el adaptador dinámico hará match de filas
        return cand

    return None


# -------------------------------------------------------------------
# APLICACIÓN DE SPEC
# -------------------------------------------------------------------

def _apply_spec(analysis, spec):
    """Asigna Specification a un Analysis respetando el modelo AT/DX de SENAITE 2.6.
    - Si 'spec' es una DynamicAnalysisSpec (DX): la enlazamos en el AnalysisSpec del análisis.
    - Si 'spec' es una Specification (AT): usamos setSpecification como siempre.
    """
    try:
        pt = getattr(spec, "portal_type", "") or ""

        # Caso DX: DynamicAnalysisSpec (o alias)
        if pt in ("DynamicAnalysisSpec", "dynamic_analysisspec"):
            # Asegurar que el AnalysisSpec existe (getAnalysisSpec suele crearlo lazy)
            get_aspec = getattr(analysis, 'getAnalysisSpec', None)
            aspec = get_aspec() if callable(get_aspec) else None
            if not aspec and callable(get_aspec):
                aspec = get_aspec()
            if not aspec:
                raise AttributeError("No AnalysisSpec found/created for analysis %r" % analysis.getId())

            # Enlazar la DX dentro del AnalysisSpec
            setter = getattr(aspec, "setDynamicAnalysisSpec", None)
            if not callable(setter):
                raise AttributeError("AnalysisSpec lacks setDynamicAnalysisSpec()")
            setter(spec)

            # Opcional: limpiar ResultsRange previo para forzar recalculo dinámico
            try:
                if hasattr(analysis, "setResultsRange"):
                    analysis.setResultsRange(None)
            except Exception:
                pass

            analysis.reindexObject()
            return True

        # Caso AT clásico: Specification
        if hasattr(analysis, 'setSpecification'):
            analysis.setSpecification(spec)
        else:
            # vía AnalysisSpec intermedio si aplica
            aspec = getattr(analysis, 'getAnalysisSpec', lambda: None)()
            if aspec and hasattr(aspec, 'setSpecification'):
                aspec.setSpecification(spec)

        analysis.reindexObject()
        return True

    except Exception as e:
        logger.warn("[AutoSpec] No se pudo asignar Spec a %s: %r", analysis.getId(), e)
        return False


# -------------------------------------------------------------------
# SUBSCRIBERS
# -------------------------------------------------------------------

def apply_specs_for_ar(ar, event):
    """Al crear el AR, recorre sus análisis y aplica Spec."""
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
    """Al CREARSE o MODIFICARSE un análisis, intenta aplicar Spec."""
    if not (IObjectAddedEvent.providedBy(event) or IObjectModifiedEvent.providedBy(event)):
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
