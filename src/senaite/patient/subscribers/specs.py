# -*- coding: utf-8 -*-
from Products.CMFCore.utils import getToolByName
from zope.lifecycleevent.interfaces import IObjectAddedEvent, IObjectModifiedEvent
from bika.lims import api, logger

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
# ESTADO ACTUAL DEL ANALYSIS: ¿ya hay algo elegido por el usuario?
# -------------------------------------------------------------------

def _get_analysis_spec(analysis):
    """Devuelve/crea el AnalysisSpec del análisis probando múltiples firmas."""
    get_aspec = getattr(analysis, 'getAnalysisSpec', None)
    if callable(get_aspec):
        # Variantes vistas en distintos builds
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
                # firma no soportada
                pass
            except Exception:
                pass

    # Alternativas comunes
    for alt in ('getOrCreateAnalysisSpec', 'ensureAnalysisSpec', '_get_or_create_analysis_spec'):
        try:
            fn = getattr(analysis, alt, None)
            if callable(fn):
                aspec = fn()
                if aspec:
                    return aspec
        except Exception:
            pass

    # AT clásico vía Schema (algunos builds)
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
    """Informa qué hay enlazado actualmente en el Analysis:
       - kind: 'dx' | 'at' | None
       - obj: la spec enlazada, si existe
    """
    aspec = _get_analysis_spec(analysis)
    if not aspec:
        return (None, None)

    # ¿DX ya enlazada?
    get_dx = getattr(aspec, "getDynamicAnalysisSpec", None)
    if callable(get_dx):
        try:
            dx = get_dx()
            if dx:
                return ("dx", dx)
        except Exception:
            pass

    # ¿AT ya enlazada?
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
    """True si el análisis ya tiene alguna spec elegida (manual o previa)."""
    # Mantener compatibilidad: si ya hay ResultsRange, también considerarlo manual/previo
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
    """Último recurso para AT antiguos: intenta crear un AnalysisSpec embebido.
    Solo se ejecuta si _get_analysis_spec aún no devuelve nada.
    """
    try:
        if _get_analysis_spec(analysis):
            return True

        # Intento 1: API de bika/senaite (si existe)
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

        # Intento 2: Archetypes clásico
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
    """Garantiza que el AnalysisSpec interno exista antes de aplicar la Spec.
    Intenta múltiples firmas, 'wakeups' y, como último recurso, creación explícita (AT legacy).
    """
    # ¿ya existe?
    if _get_analysis_spec(analysis):
        return True

    # Reintentos con creación explícita por firma
    try:
        get_aspec = getattr(analysis, 'getAnalysisSpec', None)
        if callable(get_aspec):
            for args, kwargs in (
                ((), {'create': True}),
                ((True,), {}),
            ):
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

    # “Wake-up” clásico
    try:
        set_rr = getattr(analysis, "setResultsRange", None)
        if callable(set_rr):
            set_rr({})
    except Exception as e:
        logger.warn("[AutoSpec] No se pudo inicializar AnalysisSpec para %s: %r",
                    getattr(analysis, 'getId', lambda: 'analysis')(), e)

    try:
        analysis.reindexObject()
    except Exception:
        pass

    if _get_analysis_spec(analysis):
        return True

    # 🔴 Último recurso: creación explícita para builds AT antiguos
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

    # Heurística por título (ej: "Quimica 3 Elementos")
    wanted_titles = (u"quimica 3 elementos", u"química 3 elementos")
    for obj in dx_specs:
        try:
            t = getattr(obj, "Title", lambda: u"")()
            if isinstance(t, basestring) and t.strip().lower() in wanted_titles:
                return obj
        except Exception:
            pass

    # Heurística por cliente
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
    """DX en /setup/dynamicanalysisspecs > AT en /bika_setup/specifications > traversal."""
    # Puede no estar listo el servicio en el mismo tick de creación
    try:
        service_uid = analysis.getServiceUID()
    except Exception:
        service_uid = None
    if not service_uid:
        logger.info("[AutoSpec] %s: sin ServiceUID aún; se reintentará en Modified",
                    getattr(analysis, 'getId', lambda: '?')())
        return None

    # 1) DX
    spec = _prefer_dx_spec(portal, analysis, ar)
    if spec:
        logger.info("[AutoSpec] DX candidate: %s", getattr(spec, 'Title', lambda: spec)())
        return spec

    # 2) AT
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

    # 3) Cualquiera candidata por traversal
    for cand in _iter_specs_by_traversal(portal):
        logger.info("[AutoSpec] Traversal candidate: %s", getattr(cand, 'Title', lambda: cand)())
        return cand

    logger.info("[AutoSpec] Sin Specification encontrada")
    return None

# -------------------------------------------------------------------
# APLICACIÓN DE SPEC (no sobreescribe manual; idempotente)
# -------------------------------------------------------------------

def _apply_spec(analysis, spec):
    """Asigna Specification respetando SENAITE 2.6 (AT/DX) sin pisar selección manual."""
    try:
        # 0) Si ya hay algo, no tocar (respeta selección manual / previa)
        existing_kind, existing_obj = _current_spec_state(analysis)
        if existing_obj:
            logger.info("[AutoSpec] %s: ya tiene spec %s (%s); no se sobreescribe",
                        getattr(analysis, 'Title', lambda: '?')(),
                        getattr(existing_obj, 'Title', lambda: existing_obj)(),
                        existing_kind or 'unknown')
            return True

        pt = getattr(spec, "portal_type", "") or ""
        spec_uid = _uid(spec)

        # 0.5) Asegurar que el AnalysisSpec interno exista antes de cualquier set*
        if not _ensure_analysis_spec_initialized(analysis):
            raise AttributeError("No AnalysisSpec found/created for analysis '{}'".format(
                getattr(analysis, 'getKeyword', lambda: analysis)()
            ))

        # --- DX: enlazar dentro del AnalysisSpec ---
        if pt in ("DynamicAnalysisSpec", "dynamic_analysisspec"):
            aspec = _get_analysis_spec(analysis)
            if not aspec:
                raise AttributeError("No AnalysisSpec found/created for analysis %r" % analysis.getId())

            # Idempotencia DX
            get_dx = getattr(aspec, "getDynamicAnalysisSpec", None)
            try:
                curr = get_dx() if callable(get_dx) else None
                if curr and _uid(curr) == spec_uid:
                    logger.info("[AutoSpec] %s: DX ya enlazada (%s); no-op",
                                analysis.Title(), getattr(spec, 'Title', lambda: spec)())
                    return True
            except Exception:
                pass

            # Soportar setters que esperan objeto o UID
            tried = False
            for setter_name, value in (
                ("setDynamicAnalysisSpec", spec),           # objeto
                ("setDynamicAnalysisSpec", spec_uid),       # UID (algunos builds lo aceptan)
                ("setDynamicAnalysisSpecUID", spec_uid),    # UID explícito
            ):
                setter = getattr(aspec, setter_name, None)
                if callable(setter):
                    try:
                        setter(value)
                        tried = True
                        break
                    except Exception:
                        pass
            if not tried:
                raise AttributeError("AnalysisSpec no expone setter DX compatible")

            # Limpia ResultsRange para forzar recálculo dinámico
            try:
                if hasattr(analysis, "setResultsRange"):
                    analysis.setResultsRange(None)
            except Exception:
                pass

            try:
                analysis.reindexObject()
            except Exception:
                pass

            logger.info("[AutoSpec] %s: DX aplicada → %s",
                        analysis.Title(), getattr(spec, 'Title', lambda: spec)())
            return True

        # --- AT clásico ---
        aspec = _get_analysis_spec(analysis)
        if aspec:
            # Idempotencia AT
            get_at = getattr(aspec, "getSpecification", None)
            try:
                curr = get_at() if callable(get_at) else None
                if curr and _uid(curr) == spec_uid:
                    logger.info("[AutoSpec] %s: AT ya enlazada (%s); no-op",
                                analysis.Title(), getattr(spec, 'Title', lambda: spec)())
                    return True
            except Exception:
                pass

        # Soportar setters en Analysis o en AnalysisSpec y en sus variantes por UID
        set_ok = False
        for owner in (analysis, aspec):
            if not owner:
                continue
            for setter_name, value in (
                ("setSpecification", spec),           # objeto
                ("setSpecification", spec_uid),       # UID
                ("setSpecificationUID", spec_uid),    # UID explícito
            ):
                setter = getattr(owner, setter_name, None)
                if callable(setter):
                    try:
                        setter(value)
                        set_ok = True
                        break
                    except Exception:
                        pass
            if set_ok:
                break

        if not set_ok:
            raise AttributeError("Ni analysis/aspec exponen setter AT compatible")

        try:
            analysis.reindexObject()
        except Exception:
            pass

        logger.info("[AutoSpec] %s: AT aplicada → %s",
                    analysis.Title(), getattr(spec, 'Title', lambda: spec)())
        return True

    except Exception as e:
        logger.warn("[AutoSpec] No se pudo asignar Spec a %s: %r",
                    getattr(analysis, 'getId', lambda: '?')(), e)
        return False

# -------------------------------------------------------------------
# SUBSCRIBERS ESPECÍFICOS (por interfaz)
# -------------------------------------------------------------------

def apply_specs_for_ar(ar, event):
    """AR creado ⇒ aplicar spec a todos sus Analysis (solo si no hay selección manual)."""
    if not IObjectAddedEvent.providedBy(event):
        return
    portal = api.get_portal()
    analyses = getattr(ar, 'getAnalyses', lambda: [])() or []
    for an in analyses:
        # respeta manual
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
    """Analysis creado/modificado ⇒ intentar aplicar spec (sin pisar manual)."""
    if not (IObjectAddedEvent.providedBy(event) or IObjectModifiedEvent.providedBy(event)):
        return
    # respeta manual
    if _user_already_selected(analysis):
        logger.info("[AutoSpec] %s: ya tenía selección; skip", analysis.Title())
        return

    # Localiza AR
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

# -------------------------------------------------------------------
# SUBSCRIBERS UNIVERSALES (por si la interfaz no calza en tu build)
# -------------------------------------------------------------------

def on_object_added(obj, event):
    """Fallback Added: filtramos por portal_type y respetamos manual."""
    if not IObjectAddedEvent.providedBy(event):
        return
    pt = getattr(obj, 'portal_type', '')
    if pt == 'AnalysisRequest':
        apply_specs_for_ar(obj, event)
    elif pt == 'Analysis':
        apply_spec_for_analysis(obj, event)

def on_object_modified(obj, event):
    """Fallback Modified: filtramos por portal_type y respetamos manual."""
    if not IObjectModifiedEvent.providedBy(event):
        return
    pt = getattr(obj, 'portal_type', '')
    if pt == 'Analysis':
        apply_spec_for_analysis(obj, event)
