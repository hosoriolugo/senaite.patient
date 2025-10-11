# src/senaite/patient/subscribers/specs.py
# -*- coding: utf-8 -*-
from Products.CMFCore.utils import getToolByName
from zope.lifecycleevent.interfaces import IObjectAddedEvent, IObjectModifiedEvent
from bika.lims import api, logger

# --- Parche de seguridad para dynamicresultsrange -------------------
# Evita crash cuando el adaptador recibe un RequestContainer en lugar de un AnalysisSpec
try:
    from bika.lims.adapters import dynamicresultsrange as _drr
    _DRR = getattr(_drr, "DynamicResultsRange", None)
    if _DRR:
        _orig_dynspec_prop = getattr(_DRR, "dynamicspec", None)
        _orig_call = getattr(_DRR, "__call__", None)

        def _safe_dynamicspec(self):
            try:
                spec = getattr(self, "specification", None)
                # Si no es un AnalysisSpec válido, no hay DX todavía → None
                if spec is None or not hasattr(spec, "getDynamicAnalysisSpec"):
                    return None
                # Usa el getter original si existe como property
                if isinstance(_orig_dynspec_prop, property) and _orig_dynspec_prop.fget:
                    return _orig_dynspec_prop.fget(self)
                # Fallback: intenta la ruta clásica del core
                try:
                    return spec.getDynamicAnalysisSpec()
                except Exception:
                    return None
            except Exception:
                return None

        _DRR.dynamicspec = property(_safe_dynamicspec)

        if callable(_orig_call):
            def _safe_call(self, *a, **kw):
                try:
                    return _orig_call(self, *a, **kw)
                except AttributeError:
                    # Si algo interno intenta usar un RequestContainer como spec, devuelve rango vacío
                    return {}
                except Exception:
                    # No matar la transacción por errores no críticos de rango
                    return {}
            _DRR.__call__ = _safe_call
except Exception:
    # Nunca impedir que cargue el módulo por fallar el parche
    pass
# --------------------------------------------------------------------

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
    """UID segura para comparar objetos; vuelve a un id predecible si no hay UID()."""
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

def _spec_matches(spec_obj, service_uid, client_uid, sampletype_uid, method_uid):
    """Filtro suave para AT/DX; el detalle por edad/sexo lo resuelve el adaptador."""
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
    """Recorre AT y DX en carpetas conocidas."""
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
# LOGGING DE CAPACIDADES (diagnóstico)
# -------------------------------------------------------------------

def _log_capabilities(analysis, aspec):
    """Loggea qué setters están disponibles para DX/AT, en Analysis y en AnalysisSpec."""
    try:
        a_id = getattr(analysis, 'getId', lambda: '?')()
        a_kw = getattr(analysis, 'getKeyword', lambda: a_id)()
        try:
            svc_uid = getattr(analysis, 'getServiceUID', lambda: None)()
        except Exception:
            svc_uid = None

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
                "setSpecificationUID": bool(aspec and callable(getattr(aspec, "setSpecificationUID", None))),
                "setDynamicAnalysisSpec": bool(aspec and callable(getattr(aspec, "setDynamicAnalysisSpec", None))),
                "setDynamicAnalysisSpecUID": bool(aspec and callable(getattr(aspec, "setDynamicAnalysisSpecUID", None))),
                "getSpecification": bool(aspec and callable(getattr(aspec, "getSpecification", None))),
                "getDynamicAnalysisSpec": bool(aspec and callable(getattr(aspec, "getDynamicAnalysisSpec", None))),
            }
        }
        logger.info("[AutoSpec][caps] %s svc=%s caps=%r", a_kw, svc_uid, caps)
    except Exception as e:
        logger.warn("[AutoSpec][caps] fallo al loggear capacidades: %r", e)

# -------------------------------------------------------------------
# ESTADO ACTUAL DEL ANALYSIS: ¿ya hay algo elegido por el usuario?
# -------------------------------------------------------------------

def _get_analysis_spec(analysis):
    """Devuelve/crea el AnalysisSpec del análisis probando múltiples firmas."""
    get_aspec = getattr(analysis, 'getAnalysisSpec', None)
    if callable(get_aspec):
        for args, kwargs in (
            ((), {'create': True}),
            ((True,), {}),
            ((), {}),
        ):
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

# -------------------- NUEVOS AJUSTES PARA INICIALIZACIÓN --------------------

def _force_create_analysis_spec_legacy(analysis):
    try:
        if _get_analysis_spec(analysis):
            return True
        try:
            from bika.lims import api as bika_api
            aspec = bika_api.create(
                container=analysis,
                type_name="AnalysisSpec",
                id="analysisspec"
            )
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
    """Garantiza que el AnalysisSpec interno exista (sin disparar setResultsRange)."""
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

    # 👉 Importante: NO usar setResultsRange({}) aquí; dispara el adaptador.
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
# APLICACIÓN DE SPEC (no sobreescribe manual; idempotente)
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

        if pt in ("DynamicAnalysisSpec", "dynamic_analysisspec"):
            bridge_ok = False
            for setter_name, value in (
                ("setDynamicAnalysisSpecUID", spec_uid),
                ("setDynamicAnalysisSpec", spec),
                ("setDynamicAnalysisSpec", spec_uid),
            ):
                setter = getattr(analysis, setter_name, None)
                if callable(setter):
                    try:
                        setter(value)
                        bridge_ok = True
                        logger.info("[AutoSpec] %s: DX asignada en Analysis vía %s",
                                    analysis.Title(), setter_name)
                        break
                    except Exception:
                        pass

            if not bridge_ok:
                for setter_name, value in (
                    ("setSpecificationUID", spec_uid),
                    ("setSpecification", spec_uid),
                    ("setSpecification", spec),
                ):
                    setter = getattr(analysis, setter_name, None)
                    if callable(setter):
                        try:
                            setter(value)
                            bridge_ok = True
                            logger.info("[AutoSpec] %s: DX aplicada en Analysis vía puente %s",
                                        analysis.Title(), setter_name)
                            break
                        except Exception:
                            pass

            aspec_ok = _ensure_analysis_spec_initialized(analysis)
            aspec = _get_analysis_spec(analysis) if aspec_ok else None
            _log_capabilities(analysis, aspec)

            if aspec:
                linked = False
                try:
                    get_dx = getattr(aspec, "getDynamicAnalysisSpec", None)
                    curr = get_dx() if callable(get_dx) else None
                    if curr and _uid(curr) == spec_uid:
                        linked = True
                        logger.info("[AutoSpec] %s: DX ya enlazada en AnalysisSpec; no-op",
                                    analysis.Title())
                except Exception:
                    pass

                if not linked:
                    for setter_name, value in (
                        ("setDynamicAnalysisSpecUID", spec_uid),
                        ("setDynamicAnalysisSpec", spec),
                        ("setDynamicAnalysisSpec", spec_uid),
                    ):
                        setter = getattr(aspec, setter_name, None)
                        if callable(setter):
                            try:
                                setter(value)
                                linked = True
                                logger.info("[AutoSpec] %s: DX enlazada en AnalysisSpec vía %s",
                                            analysis.Title(), setter_name)
                                break
                            except Exception:
                                pass

                if linked:
                    try:
                        if hasattr(analysis, "setResultsRange"):
                            analysis.setResultsRange(None)
                    except Exception:
                        pass
                    try:
                        analysis.reindexObject()
                    except Exception:
                        pass
                    return True

            if bridge_ok and not aspec_ok:
                logger.warn("[AutoSpec] %s: DX aplicada en Analysis, pero no se pudo "
                            "crear/enlazar AnalysisSpec; se omite limpiar ResultsRange.",
                            analysis.Title())
                try:
                    analysis.reindexObject()
                except Exception:
                    pass
                return True

            raise AttributeError("No fue posible aplicar DX (sin setters ni AnalysisSpec utilizable)")

        # --- AT clásica ---
        set_ok = False
        for setter_name, value in (
            ("setSpecificationUID", spec_uid),
            ("setSpecification", spec_uid),
            ("setSpecification", spec),
        ):
            setter = getattr(analysis, setter_name, None)
            if callable(setter):
                try:
                    setter(value)
                    set_ok = True
                    logger.info("[AutoSpec] %s: AT asignada en Analysis vía %s",
                                analysis.Title(), setter_name)
                    break
                except Exception:
                    pass

        if not set_ok:
            if not _ensure_analysis_spec_initialized(analysis):
                raise AttributeError("No AnalysisSpec disponible para AT")
            aspec = _get_analysis_spec(analysis)
            _log_capabilities(analysis, aspec)
            done = False
            for setter_name, value in (
                ("setSpecificationUID", spec_uid),
                ("setSpecification", spec_uid),
                ("setSpecification", spec),
            ):
                setter = getattr(aspec, setter_name, None)
                if callable(setter):
                    try:
                        setter(value)
                        done = True
                        logger.info("[AutoSpec] %s: AT aplicada en AnalysisSpec vía %s",
                                    analysis.Title(), setter_name)
                        break
                    except Exception:
                        pass
            if not done:
                raise AttributeError("Ni analysis/aspec exponen setter AT compatible")

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
