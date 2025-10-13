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
# FLAGS DE BÚSQUEDA / FALLBACK (MODO PRODUCCIÓN)
# -------------------------------------------------------------------
PROD_MODE = True

# En producción: desactivar traversal por defecto (evita specs “azarosas”).
ALLOW_TRAVERSAL_FALLBACK = False

# Si alguna vez activas traversal, limita a AT para evitar DX en objetos sin soporte DX.
TRAVERSAL_ONLY_AT = True

# En producción no tomamos el “primer AT que aparezca” si no hay match por servicio.
AT_FALLBACK_FIRST = False

# Exigir coincidencia de ServiceUID cuando la spec la trae seteada (recomendado en prod).
REQUIRE_SERVICE_MATCH = True

# Títulos preferidos (por si habilitas traversal alguna vez)
PREFERRED_DX_TITLES = (
    u"Quimica 3 Elementos",
    u"Química 3 Elementos",
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

    # 🔴 NO tocar ResultsRange aquí
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

def _has_dx_support(analysis):
    """True si podemos enlazar una DynamicAnalysisSpec de forma nativa:
    - Setters DX directos en Analysis, o
    - vía AnalysisSpec hijo con setters DX (créalo si falta).
    """
    try:
        # 1) Setters DX directos en Analysis
        if (callable(getattr(analysis, "setDynamicAnalysisSpec", None)) or
                callable(getattr(analysis, "setDynamicAnalysisSpecUID", None))):
            return True
        # 2) Vía AnalysisSpec hijo (intenta crearlo si no existe)
        if not _get_analysis_spec(analysis):
            _ensure_analysis_spec_initialized(analysis)
        aspec = _get_analysis_spec(analysis)
        if aspec and (callable(getattr(aspec, "setDynamicAnalysisSpec", None)) or
                      callable(getattr(aspec, "setDynamicAnalysisSpecUID", None))):
            return True
    except Exception:
        pass
    return False

def _spec_matches(spec_obj, service_uid, client_uid, sampletype_uid, method_uid):
    """Match estricto por ServiceUID (si la spec lo define) y por otros filtros si están definidos."""
    try:
        s_uid = (_obj_uid(spec_obj, "getServiceUID")
                 or _obj_uid(spec_obj, "getService")
                 or getattr(spec_obj, "service_uid", None))
        if REQUIRE_SERVICE_MATCH and s_uid:
            if not service_uid or s_uid != service_uid:
                return False
        elif s_uid and service_uid and s_uid != service_uid:
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
                "setSpecificationUID": bool(aspec and callable(getattr(aspec, "setSpecificationUID", None))),
                "setDynamicAnalysisSpec": bool(aspec and callable(getattr(aspec, "setDynamicAnalysisSpec", None)))),
                "setDynamicAnalysisSpecUID": bool(aspec and callable(getattr(aspec, "setDynamicAnalysisSpecUID", None))),
                "getSpecification": bool(aspec and callable(getattr(aspec, "getSpecification", None))),
                "getDynamicAnalysisSpec": bool(aspec and callable(getattr(aspec, "getDynamicAnalysisSpec", None))),
            }
        }
        logger.info("[AutoSpec][caps] %s svc=%s caps=%r", a_kw, svc_uid, caps)
    except Exception as e:
        logger.warning("[AutoSpec][caps] fallo al loggear capacidades: %r", e)

# -------------------------------------------------------------------
# ESTADO ACTUAL / ASPEC
# -------------------------------------------------------------------

def _current_spec_state(analysis):
    """Detecta spec existente tanto en el AnalysisSpec hijo como directamente en Analysis."""
    # 0) DX/AT directo en Analysis (algunas instalaciones lo usan)
    try:
        get_dx = getattr(analysis, "getDynamicAnalysisSpec", None)
        if callable(get_dx):
            dx = get_dx()
            if dx:
                return ("dx", dx)
    except Exception:
        pass
    try:
        get_at = getattr(analysis, "getSpecification", None)
        if callable(get_at):
            at = get_at()
            if at:
                return ("at", at)
    except Exception:
        pass

    # 1) Vía AnalysisSpec hijo
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
# AJUSTE: soporte de filas/keyword para DX
# -------------------------------------------------------------------

def _dx_supports(dx, keyword, client_uid=None, sampletype_uid=None, method_uid=None):
    """
    True si la DX contiene alguna fila para `keyword` (GLU/CRE/BUN, etc.),
    y si están presentes en la fila, coincide con client/sampletype/method.
    None si no se pudo inspeccionar filas (no bloquea la DX).
    """
    try:
        rows = None
        for attr in ("getRows", "getData", "get_data", "rows", "data"):
            val = getattr(dx, attr, None)
            if callable(val):
                rows = val()
            elif val is not None:
                rows = val
            if rows:
                break
        if not rows:
            return None  # no se puede inferir

        def norm(x):
            if x is None:
                return None
            try:
                return x.strip().upper()
            except Exception:
                try:
                    return str(x).strip().upper()
                except Exception:
                    return x

        kw = norm(keyword)
        for r in rows:
            k = norm(r.get("Keyword") or r.get("keyword") or r.get("service_keyword"))
            if not k or k != kw:
                continue

            ok_client = True
            ok_stype = True
            ok_method = True

            if client_uid:
                rc = r.get("client_uid") or r.get("ClientUID") or r.get("client")
                ok_client = (not rc) or (norm(rc) == norm(client_uid))
            if sampletype_uid:
                rs = r.get("sampletype_uid") or r.get("SampleTypeUID") or r.get("sample_type")
                ok_stype = (not rs) or (norm(rs) == norm(sampletype_uid))
            if method_uid:
                rm = r.get("method_uid") or r.get("MethodUID") or r.get("method")
                ok_method = (not rm) or (norm(rm) == norm(method_uid))

            if ok_client and ok_stype and ok_method:
                return True

        return False

    except Exception:
        return None

# -------------------------------------------------------------------
# SELECTOR DE SPEC (prioriza DX)
# -------------------------------------------------------------------

def _prefer_dx_spec(portal, analysis, ar):
    # Intentar DX solo si hay (o podemos tener) soporte DX
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

    # Solo tipos DX en carpeta DX
    dx_specs = [o for o in dx_folder.objectValues()
                if getattr(o, "portal_type", "") in ("DynamicAnalysisSpec", "dynamic_analysisspec")]
    if not dx_specs:
        return None
    if len(dx_specs) == 1:
        return dx_specs[0]

    # Datos del análisis para filtrar
    try:
        keyword = (getattr(analysis, "getKeyword", None) or getattr(analysis, "getId", None) or (lambda: None))()
    except Exception:
        keyword = None
    keyword = (keyword or "").strip()

    client_uid = None
    sampletype_uid = None
    method_uid = None
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

    # Scoring:
    # +100 si la DX es específica del cliente y coincide
    # +50 si confirmamos que contiene filas para el keyword (y filtros)
    # +10 si no podemos inspeccionar filas (posible candidata)
    # +5  si el título preferido coincide
    wanted_titles = tuple(t.strip().lower() for t in PREFERRED_DX_TITLES)
    scored = []
    for obj in dx_specs:
        score = 0

        cx = getattr(obj, "client_uid", None) or _obj_uid(obj, "getClientUID")
        if cx and client_uid and cx == client_uid:
            score += 100

        sup = _dx_supports(obj, keyword, client_uid, sampletype_uid, method_uid)
        if sup is True:
            score += 50
        elif sup is None:
            score += 10

        try:
            t = getattr(obj, "Title", lambda: u"")()
            if isinstance(t, basestring) and t.strip().lower() in wanted_titles:
                score += 5
        except Exception:
            pass

        scored.append((score, obj))

    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    best = scored[0][1]
    if best:
        logger.info("[AutoSpec] DX candidate (scored): %s", getattr(best, 'Title', lambda: best)())
    return best

# -------------------------------------------------------------------
# BÚSQUEDA DE SPEC
# -------------------------------------------------------------------

def _find_matching_spec(portal, analysis, ar):
    """Devuelve una spec (prioriza DX). Ya no abandona si falta ServiceUID:
    intenta DX por keyword y sólo entonces registra el reintento."""
    # 1) Intentar DX primero (independiente del ServiceUID)
    spec = _prefer_dx_spec(portal, analysis, ar)
    if spec:
        logger.info("[AutoSpec] DX candidate: %s", getattr(spec, 'Title', lambda: spec)())
        return spec

    # 2) AT por carpeta de setup clásico (aquí sí conviene tener ServiceUID para match estricto)
    try:
        service_uid = getattr(analysis, "getServiceUID", lambda: None)()
    except Exception:
        service_uid = None

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

                # Buscar match estricto (si la spec define service, se respeta)
                for cand in at_specs:
                    if _spec_matches(cand, service_uid, client_uid, sampletype_uid, method_uid):
                        logger.info("[AutoSpec] AT candidate: %s", getattr(cand, 'Title', lambda: cand)())
                        return cand

                if not AT_FALLBACK_FIRST:
                    logger.info("[AutoSpec] AT: no se encontró Specification que coincida con el servicio")
                else:
                    logger.info("[AutoSpec] AT fallback (first): %s",
                                getattr(at_specs[0], 'Title', lambda: at_specs[0])())
                    return at_specs[0]

    # 3) Traversal (opcional) — mantenemos tu configuración por defecto desactivada
    if not ALLOW_TRAVERSAL_FALLBACK:
        # Si a esta altura no hay spec y además no hay ServiceUID, explicamos por qué:
        if not getattr(analysis, "getServiceUID", lambda: None)():
            logger.info("[AutoSpec] %s: sin ServiceUID y sin DX apta; se reintentará en Modified",
                        getattr(analysis, 'getId', lambda: '?')())
        else:
            logger.info("[AutoSpec] Sin Specification encontrada")
        return None

    # --- Traversal original (respetado) ---
    allow_dx = _has_dx_support(analysis)

    if not allow_dx or TRAVERSAL_ONLY_AT:
        for cand in _iter_specs_by_traversal(portal):
            pt = getattr(cand, "portal_type", "")
            if pt != "Specification":  # solo AT
                continue
            if _spec_matches(cand,
                             getattr(analysis, "getServiceUID", lambda: None)(),
                             getattr(getattr(ar, "aq_parent", None), "UID", lambda: None)() if hasattr(ar, "aq_parent") else None,
                             getattr(analysis, "getSampleTypeUID", lambda: None)(),
                             getattr(analysis, "getMethodUID", lambda: None)()):
                logger.info("[AutoSpec] Traversal candidate (AT-only): %s",
                            getattr(cand, 'Title', lambda: cand)())
                return cand
        logger.info("[AutoSpec] Traversal no encontró AT compatible")
        logger.info("[AutoSpec] Sin Specification encontrada")
        return None

    def _norm_title(obj):
        try:
            t = getattr(obj, "Title", lambda: u"")()
            return t.strip().lower()
        except Exception:
            return u""

    pref_norm = [t.strip().lower() for t in PREFERRED_DX_TITLES if t]

    first_pass = []
    second_pass = []

    try:
        kw = (getattr(analysis, "getKeyword", None) or getattr(analysis, "getId", None) or (lambda: None))()
        kw = (kw or "").strip()
    except Exception:
        kw = ""

    def _parent_uid(ar):
        try:
            return ar.aq_parent.UID() if hasattr(ar.aq_parent, 'UID') else None
        except Exception:
            return None

    for cand in _iter_specs_by_traversal(portal):
        pt = getattr(cand, "portal_type", "")

        if pt in ("DynamicAnalysisSpec", "dynamic_analysisspec") and kw:
            try:
                sup = _dx_supports(cand, kw, _parent_uid(ar),
                                   getattr(analysis, "getSampleTypeUID", lambda: None)(),
                                   getattr(analysis, "getMethodUID", lambda: None)())
                if sup is False:
                    continue
            except Exception:
                pass

        title_n = _norm_title(cand)
        if title_n in pref_norm:
            first_pass.append(cand)
        else:
            second_pass.append(cand)

    choose_from = first_pass or second_pass
    if choose_from:
        best = choose_from[0]
        logger.info("[AutoSpec] Traversal candidate (filtered): %s",
                    getattr(best, 'Title', lambda: best)())
        return best

    logger.info("[AutoSpec] Traversal no encontró candidatos válidos")
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

            logger.warning("[AutoSpec] %s: NO se pudo aplicar DX (sin setters DX ni AnalysisSpec). Skip.",
                           getattr(analysis, 'Title', lambda: '?')())
            return False

        # --- AT clásico ---
        _ensure_analysis_spec_initialized(analysis)

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
            logger.warning("[AutoSpec] %s: No se pudo aplicar AT (sin setters compatibles).",
                           getattr(analysis, 'Title', lambda: '?')())
            return False

        try:
            analysis.reindexObject()
        except Exception:
            pass

        return True

    except Exception as e:
        logger.warning("[AutoSpec] No se pudo asignar Spec a %s: %r",
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
