# src/senaite/patient/subscribers/specs.py
# -*- coding: utf-8 -*-
from Products.CMFCore.utils import getToolByName
from zope.lifecycleevent.interfaces import IObjectAddedEvent, IObjectModifiedEvent
from bika.lims import api, logger

# Compatibilidad Py2/Py3
try:
    basestring
except NameError:  # Py3
    basestring = str

try:
    from Products.CMFPlone.utils import safe_unicode as _safe_unicode
except Exception:
    def _safe_unicode(x):
        try:
            return str(x)
        except Exception:
            return x

# -------------------------------------------------------------------
# SOPORTE A AT y DX
# -------------------------------------------------------------------

PORTAL_TYPES_SPECS = (
    'Specification',             # AT
    'DynamicAnalysisSpec',       # DX
    'dynamic_analysisspec',      # DX alias
)

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
# UTILIDADES
# -------------------------------------------------------------------

def _uid(obj):
    try:
        if hasattr(obj, "UID"):
            return obj.UID()
    except Exception:
        pass
    try:
        return getattr(obj, "getId", lambda: None)() or getattr(obj, "id", None) or repr(obj)
    except Exception:
        return repr(obj)

def _obj_uid(obj, attr_name, default=None):
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
        if isinstance(val, basestring):
            return _safe_unicode(val)
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

def _has_dx_support(analysis):
    """True si podemos enlazar una DynamicAnalysisSpec de forma nativa:
    - Setters DX directos en Analysis, o
    - vía AnalysisSpec hijo con setters DX.
    """
    try:
        # 1) Setters DX directos en Analysis
        if callable(getattr(analysis, "setDynamicAnalysisSpec", None)) \
           or callable(getattr(analysis, "setDynamicAnalysisSpecUID", None)):
            return True
        # 2) Vía AnalysisSpec hijo
        aspec = _get_analysis_spec(analysis)
        if aspec and (callable(getattr(aspec, "setDynamicAnalysisSpec", None)) or
                      callable(getattr(aspec, "setDynamicAnalysisSpecUID", None))):
            return True
    except Exception:
        pass
    return False

def _spec_matches(spec_obj, service_uid, client_uid, sampletype_uid, method_uid):
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
# LOGGING DE CAPACIDADES
# -------------------------------------------------------------------

def _log_capabilities(analysis, aspec):
    try:
        a_kw = getattr(analysis, 'getKeyword', getattr(analysis, 'getId', lambda: '?'))()
        svc_uid = getattr(analysis, 'getServiceUID', lambda: None)()
        caps = {
            "analysis": {
                "setSpecification": callable(getattr(analysis, "setSpecification", None)),
                "setSpecificationUID": callable(getattr(analysis, "setSpecificationUID", None)),
                "setDynamicAnalysisSpec": callable(getattr(analysis, "setDynamicAnalysisSpec", None)),
                "setDynamicAnalysisSpecUID": callable(getattr(analysis, "setDynamicAnalysisSpecUID", None)),
            },
            "aspec": {
                "exists": bool(aspec),
                "setSpecification": bool(aspec and callable(getattr(aspec, "setSpecification", None))),
                "setSpecificationUID": bool(aspec and callable(getattr(aspec, "setSpecificationUID", None)))),
                "setDynamicAnalysisSpec": bool(aspec and callable(getattr(aspec, "setDynamicAnalysisSpec", None)))),
                "setDynamicAnalysisSpecUID": bool(aspec and callable(getattr(aspec, "setDynamicAnalysisSpecUID", None)))),
                "getSpecification": bool(aspec and callable(getattr(aspec, "getSpecification", None)))),
                "getDynamicAnalysisSpec": bool(aspec and callable(getattr(aspec, "getDynamicAnalysisSpec", None)))),
            }
        }
        logger.info("[AutoSpec][caps] %s svc=%s caps=%r", a_kw, svc_uid, caps)
    except Exception as e:
        logger.warn("[AutoSpec][caps] fallo al loggear capacidades: %r", e)

# -------------------------------------------------------------------
# ESTADO ACTUAL / ASPEC
# -------------------------------------------------------------------

def _get_analysis_spec(analysis):
    get_aspec = getattr(analysis, 'getAnalysisSpec', None)
    if callable(get_aspec):
        for args, kwargs in (((), {'create': True}), ((True,), {}), ((), {})):
            try:
                aspec = get_aspec(*args, **kwargs)
                if aspec:
                    return aspec
            except TypeError:
                pass
            except Exception:
                pass

    for alt in ('getOrCreateAnalysisSpec', 'ensureAnalysisSpec', '_get_or_create_analysis_spec'):
        try:
            fn = getattr(analysis, alt, None)
            if callable(fn):
                aspec = fn()
                if aspec:
                    return aspec
        except Exception:
            pass

    try:
        schema = getattr(analysis, 'Schema', lambda: None)()
        if schema and 'AnalysisSpec' in schema:
            aspec = schema['AnalysisSpec'].get(analysis)
            if aspec:
                return aspec
    except Exception:
        pass

    return None

def _current_spec_state(analysis):
    aspec = _get_analysis_spec(analysis)
    if not aspec:
        return (None, None)

    get_dx = getattr(aspec, "getDynamicAnalysisSpec", None)
    if callable(get_dx):
        try:
            dx = get_dx()
            if dx:
                return ("dx", dx)
        except Exception:
            pass

    get_at = getattr(aspec, "getSpecification", None)
    if callable(get_at):
        try:
            at = get_at()
            if at:
                return ("at", at)
        except Exception:
            pass

    return (None, None)

def _user_already_selected(analysis):
    try:
        rr = getattr(analysis, "getResultsRange", lambda: None)()
        if rr:
            return True
    except Exception:
        pass
    kind, obj = _current_spec_state(analysis)
    return bool(obj)

# -------------------------------------------------------------------
# INICIALIZACIÓN DE ASPEC
# -------------------------------------------------------------------

def _force_create_analysis_spec_legacy(analysis):
    try:
        if _get_analysis_spec(analysis):
            return True

        try:
            from bika.lims import api as bika_api
            aspec = bika_api.create(container=analysis, type_name="AnalysisSpec", id="analysisspec")
            if aspec:
                try:
                    analysis.reindexObject()
                except Exception:
                    pass
                return _get_analysis_spec(analysis) is not None
        except Exception:
            pass

        try:
            if hasattr(analysis, "invokeFactory"):
                new_id = None
                try:
                    new_id = analysis.invokeFactory("AnalysisSpec", id="analysisspec")
                except Exception:
                    new_id = analysis.invokeFactory("AnalysisSpec")
                if new_id or True:
                    try:
                        analysis.reindexObject()
                    except Exception:
                        pass
                    return _get_analysis_spec(analysis) is not None
        except Exception:
            pass

    except Exception:
        pass
    return False

def _ensure_analysis_spec_initialized(analysis):
    if _get_analysis_spec(analysis):
        return True

    try:
        get_aspec = getattr(analysis, 'getAnalysisSpec', None)
        if callable(get_aspec):
            for args, kwargs in (((), {'create': True}), ((True,), {})):
                try:
                    aspec = get_aspec(*args, **kwargs)
                    if aspec:
                        return True
                except TypeError:
                    pass
                except Exception:
                    pass
    except Exception:
        pass

    for alt in ('getOrCreateAnalysisSpec', 'ensureAnalysisSpec', '_get_or_create_analysis_spec'):
        try:
            fn = getattr(analysis, alt, None)
            if callable(fn) and fn():
                return True
        except Exception:
            pass

    # 🔴 NO tocar ResultsRange aquí (evita inyectar RequestContainer en adapters)
    try:
        analysis.reindexObject()
    except Exception:
        pass

    if _get_analysis_spec(analysis):
        return True

    try:
        if _force_create_analysis_spec_legacy(analysis):
            return True
    except Exception:
        pass

    return False

# -------------------------------------------------------------------
# SELECTOR DE SPEC (prioriza DX)
# -------------------------------------------------------------------

def _prefer_dx_spec(portal, analysis, ar):
    # ⚠️ Solo preferir DX si hay soporte; si no, forzar fallback a AT
    if not _has_dx_support(analysis):
        logger.info("[AutoSpec] %s: sin soporte DX; se omitirá DX y se evaluará AT",
                    getattr(analysis, 'Title', lambda: '?')())
        return None

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

    wanted_titles = (u"quimica 3 elementos", u"química 3 elementos")
    for obj in dx_specs:
        try:
            t = getattr(obj, "Title", lambda: u"")()
            if isinstance(t, basestring) and t.strip().lower() in wanted_titles:
                return obj
        except Exception:
            pass

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

    return dx_specs[0]

# -------------------------------------------------------------------
# BÚSQUEDA DE SPEC
# -------------------------------------------------------------------

def _find_matching_spec(portal, analysis, ar):
    try:
        service_uid = analysis.getServiceUID()
    except Exception:
        service_uid = None
    if not service_uid:
        logger.info("[AutoSpec] %s: sin ServiceUID aún; se reintentará en Modified",
                    getattr(analysis, 'getId', lambda: '?')())
        return None

    spec = _prefer_dx_spec(portal, analysis, ar)
    if spec:
        logger.info("[AutoSpec] DX candidate: %s", getattr(spec, 'Title', lambda: spec)())
        return spec

    bsetup = getattr(portal, "bika_setup", None)
    if bsetup:
        for name in ("specifications", "bika_specifications", "Specifications"):
            at_folder = getattr(bsetup, name, None)
            if not at_folder:
                continue
            at_specs = [obj for obj in at_folder.objectValues()
                        if getattr(obj, "portal_type", "") == "Specification"]
            if at_specs:
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
                        logger.info("[AutoSpec] AT candidate: %s", getattr(cand, 'Title', lambda: cand)())
                        return cand
                logger.info("[AutoSpec] AT fallback (first): %s", getattr(at_specs[0], 'Title', lambda: at_specs[0])())
                return at_specs[0]

    for cand in _iter_specs_by_traversal(portal):
        logger.info("[AutoSpec] Traversal candidate: %s", getattr(cand, 'Title', lambda: cand)())
        return cand

    logger.info("[AutoSpec] Sin Specification encontrada")
    return None

# -------------------------------------------------------------------
# APLICACIÓN DE SPEC (sin pisar manual; sin tocar ResultsRange)
# -------------------------------------------------------------------

def _apply_spec(analysis, spec):
    try:
        existing_kind, existing_obj = _current_spec_state(analysis)
        if existing_obj:
            logger.info("[AutoSpec] %s: ya tiene spec %s (%s); no se sobreescribe",
                        getattr(analysis, 'Title', lambda: '?')(),
                        getattr(existing_obj, 'Title', lambda: existing_obj)(),
                        existing_kind or 'unknown')
            return True

        pt = getattr(spec, "portal_type", "") or ""
        spec_uid = _uid(spec)

        _log_capabilities(analysis, _get_analysis_spec(analysis))

        # --- DX: SOLO con setters DX nativos o vía AnalysisSpec ---
        if pt in ("DynamicAnalysisSpec", "dynamic_analysisspec"):
            # 1) Setters DX nativos en Analysis
            for setter_name, value in (
                ("setDynamicAnalysisSpecUID", spec_uid),
                ("setDynamicAnalysisSpec", spec),
                ("setDynamicAnalysisSpec", spec_uid),
            ):
                setter = getattr(analysis, setter_name, None)
                if callable(setter):
                    try:
                        setter(value)
                        try:
                            analysis.reindexObject()
                        except Exception:
                            pass
                        logger.info("[AutoSpec] %s: DX aplicada en Analysis vía %s",
                                    analysis.Title(), setter_name)
                        return True
                    except Exception:
                        pass

            # 2) Vía AnalysisSpec (si existe o se puede crear)
            if _ensure_analysis_spec_initialized(analysis):
                aspec = _get_analysis_spec(analysis)
                _log_capabilities(analysis, aspec)
                if aspec:
                    # Idempotencia DX
                    try:
                        get_dx = getattr(aspec, "getDynamicAnalysisSpec", None)
                        curr = get_dx() if callable(get_dx) else None
                        if curr and _uid(curr) == spec_uid:
                            logger.info("[AutoSpec] %s: DX ya enlazada (%s); no-op",
                                        analysis.Title(), getattr(spec, 'Title', lambda: spec)())
                            return True
                    except Exception:
                        pass

                    for setter_name, value in (
                        ("setDynamicAnalysisSpecUID", spec_uid),
                        ("setDynamicAnalysisSpec", spec),
                        ("setDynamicAnalysisSpec", spec_uid),
                    ):
                        setter = getattr(aspec, setter_name, None)
                        if callable(setter):
                            try:
                                setter(value)
                                try:
                                    analysis.reindexObject()
                                except Exception:
                                    pass
                                logger.info("[AutoSpec] %s: DX aplicada en AnalysisSpec vía %s → %s",
                                            analysis.Title(), setter_name,
                                            getattr(spec, 'Title', lambda: spec)())
                                return True
                            except Exception:
                                pass

            # Evitar puente setSpecification*(DX) que puede inyectar valores erróneos
            logger.warn("[AutoSpec] %s: NO se pudo aplicar DX (sin setters DX ni AnalysisSpec). Skip.",
                        getattr(analysis, 'Title', lambda: '?')())
            return False

        # --- AT clásico ---
        set_ok = False
        for owner_name, owner in (("Analysis", analysis), ("AnalysisSpec", _get_analysis_spec(analysis))):
            if not owner:
                continue
            for setter_name, value in (
                ("setSpecificationUID", spec_uid),
                ("setSpecification", spec_uid),
                ("setSpecification", spec),
            ):
                setter = getattr(owner, setter_name, None)
                if callable(setter):
                    try:
                        setter(value)
                        set_ok = True
                        logger.info("[AutoSpec] %s: AT aplicada en %s vía %s → %s",
                                    analysis.Title(), owner_name, setter_name,
                                    getattr(spec, 'Title', lambda: spec)())
                        break
                    except Exception:
                        pass
            if set_ok:
                break

        if not set_ok:
            logger.warn("[AutoSpec] %s: No se pudo aplicar AT (sin setters compatibles).",
                        getattr(analysis, 'Title', lambda: '?')())
            return False

        try:
            analysis.reindexObject()
        except Exception:
            pass

        return True

    except Exception as e:
        logger.warn("[AutoSpec] No se pudo asignar Spec a %s: %r",
                    getattr(analysis, 'getId', lambda: '?')(), e)
        return False

# -------------------------------------------------------------------
# SUBSCRIBERS
# -------------------------------------------------------------------

def apply_specs_for_ar(ar, event):
    if not IObjectAddedEvent.providedBy(event):
        return
    portal = api.get_portal()
    analyses = getattr(ar, 'getAnalyses', lambda: [])() or []
    for an in analyses:
        if _user_already_selected(an):
            logger.info("[AutoSpec] %s: ya tenía selección; skip", an.Title())
            continue
        spec = _find_matching_spec(portal, an, ar)
        if spec:
            ok = _apply_spec(an, spec)
            logger.info("[AutoSpec] %s -> %s [%s]",
                        getattr(spec, 'Title', lambda: spec)(),
                        an.Title(), "OK" if ok else "FAIL")

def apply_spec_for_analysis(analysis, event):
    if not (IObjectAddedEvent.providedBy(event) or IObjectModifiedEvent.providedBy(event)):
        return
    if _user_already_selected(analysis):
        logger.info("[AutoSpec] %s: ya tenía selección; skip", analysis.Title())
        return

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

def on_object_added(obj, event):
    if not IObjectAddedEvent.providedBy(event):
        return
    pt = getattr(obj, 'portal_type', '')
    if pt == 'AnalysisRequest':
        apply_specs_for_ar(obj, event)
    elif pt == 'Analysis':
        apply_spec_for_analysis(obj, event)

def on_object_modified(obj, event):
    if not IObjectModifiedEvent.providedBy(event):
        return
    pt = getattr(obj, 'portal_type', '')
    if pt == 'Analysis':
        apply_spec_for_analysis(obj, event)
