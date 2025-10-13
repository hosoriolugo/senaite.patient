# src/senaite/patient/subscribers/specs.py
# -*- coding: utf-8 -*-
from Products.CMFCore.utils import getToolByName
from zope.lifecycleevent.interfaces import IObjectAddedEvent, IObjectModifiedEvent
from bika.lims import api, logger

# -------------------------------------------------------------------
# Compatibilidad Py2/Py3 + utilidades unicode para logging seguro
# -------------------------------------------------------------------
try:
    basestring
except NameError:  # Py3
    basestring = str

try:
    unicode  # Py2
except NameError:  # Py3
    unicode = str

try:
    from Products.CMFPlone.utils import safe_unicode as _safe_unicode
except Exception:
    def _safe_unicode(x):
        try:
            if isinstance(x, unicode):
                return x
            if isinstance(x, bytes):
                return x.decode('utf-8', 'ignore')
            return unicode(x)
        except Exception:
            try:
                return unicode(repr(x))
            except Exception:
                return u''

def _ulog(level, msg, *args):
    """
    Logger unicode-safe para Py2/Py3. Evita UnicodeDecodeError con '±', acentos, etc.
    Uso: _ulog('info', u"Texto con acento: %s", valor)
    """
    try:
        umsg = msg if isinstance(msg, unicode) else _safe_unicode(msg)
        uargs = tuple(_safe_unicode(a) for a in args)
        getattr(logger, level)(umsg, *uargs)
    except Exception:
        # Último recurso para no romper la transacción por logging
        try:
            getattr(logger, level)(_safe_unicode(msg))
        except Exception:
            pass

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

ALLOW_TRAVERSAL_FALLBACK = False
TRAVERSAL_ONLY_AT = True
AT_FALLBACK_FIRST = False
REQUIRE_SERVICE_MATCH = True

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
    """
    True si podemos enlazar una DynamicAnalysisSpec de forma nativa:
    - Setters DX directos en Analysis, o
    - VÍA AnalysisSpec HIJO YA EXISTENTE con setters DX.

    OJO: NO intenta crear AnalysisSpec aquí. Si no hay soporte real, devuelve False.
    """
    try:
        if (callable(getattr(analysis, "setDynamicAnalysisSpec", None)) or
                callable(getattr(analysis, "setDynamicAnalysisSpecUID", None))):
            return True
        aspec = _get_analysis_spec(analysis)  # no forzar creación aquí
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
                "setSpecificationUID": bool(aspec and callable(getattr(aspec, "setSpecificationUID", None)))),
                "setDynamicAnalysisSpec": bool(aspec and callable(getattr(aspec, "setDynamicAnalysisSpec", None))),
                "setDynamicAnalysisSpecUID": bool(aspec and callable(getattr(aspec, "setDynamicAnalysisSpecUID", None))),
                "getSpecification": bool(aspec and callable(getattr(aspec, "getSpecification", None))),
                "getDynamicAnalysisSpec": bool(aspec and callable(getattr(aspec, "getDynamicAnalysisSpec", None))),
            }
        }
        _ulog('info', u"[AutoSpec][caps] %s svc=%s caps=%r", a_kw, svc_uid, caps)
    except Exception as e:
        _ulog('warning', u"[AutoSpec][caps] fallo al loggear capacidades: %r", e)

# -------------------------------------------------------------------
# ESTADO ACTUAL / ASPEC
# -------------------------------------------------------------------

def _current_spec_state(analysis):
    # DX/AT directo en Analysis
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

    # Vía AnalysisSpec hijo
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
            return None

        def norm(x):
            if x is None:
                return None
            try:
                return x.strip().upper()
            except Exception:
                try:
                    return _safe_unicode(x).strip().upper()
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
# SELECTOR DE SPEC (prioriza DX si y solo si hay soporte DX REAL)
# -------------------------------------------------------------------

def _prefer_dx_spec(portal, analysis, ar):
    # DX solo si HAY soporte DX real en este Analysis
    if not _has_dx_support(analysis):
        _ulog('info', u"[AutoSpec] %s: sin soporte DX; se omitirá DX y se evaluará AT",
              getattr(analysis, 'Title', lambda: u'?')())
        return None

    setup = getattr(portal, "setup", None)
    if not setup:
        return None
    dx_folder = (getattr(setup, "dynamicanalysisspecs", None)
                 or getattr(setup, "dynamic_analysisspecs", None))
    if not dx_folder:
        return None

    dx_specs = [o for o in dx_folder.objectValues()
                if getattr(o, "portal_type", "") in ("DynamicAnalysisSpec", "dynamic_analysisspec")]
    if not dx_specs:
        return None
    if len(dx_specs) == 1:
        return dx_specs[0]

    try:
        keyword = (getattr(analysis, "getKeyword", None) or getattr(analysis, "getId", None) or (lambda: None))()
    except Exception:
        keyword = None
    keyword = (keyword or u"").strip()

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
            if isinstance(t, basestring) and _safe_unicode(t).strip().lower() in wanted_titles:
                score += 5
        except Exception:
            pass

        scored.append((score, obj))

    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    best = scored[0][1]
    if best:
        _ulog('info', u"[AutoSpec] DX candidate (scored): %s", getattr(best, 'Title', lambda: best)())
    return best

# -------------------------------------------------------------------
# BÚSQUEDA DE SPEC
# -------------------------------------------------------------------

def _find_matching_at_spec(portal, analysis, ar):
    try:
        service_uid = getattr(analysis, "getServiceUID", lambda: None)()
    except Exception:
        service_uid = None

    bsetup = getattr(portal, "bika_setup", None)
    if not bsetup:
        return None

    for name in ("specifications", "bika_specifications", "Specifications"):
        at_folder = getattr(bsetup, name, None)
        if not at_folder:
            continue
        at_specs = [obj for obj in at_folder.objectValues()
                    if getattr(obj, "portal_type", "") == "Specification"]
        if not at_specs:
            continue

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
                _ulog('info', u"[AutoSpec] AT candidate: %s", getattr(cand, 'Title', lambda: cand)())
                return cand

        if AT_FALLBACK_FIRST and at_specs:
            _ulog('info', u"[AutoSpec] AT fallback (first): %s",
                  getattr(at_specs[0], 'Title', lambda: at_specs[0])())
            return at_specs[0]

    return None

def _find_matching_spec(portal, analysis, ar):
    """
    Devuelve una spec priorizando DX solo si hay soporte DX real.
    Si no hay DX apta, intenta AT por servicio.
    """
    # 1) Intentar DX (solo si el Analysis tiene soporte DX real)
    spec = _prefer_dx_spec(portal, analysis, ar)
    if spec:
        _ulog('info', u"[AutoSpec] DX candidate: %s", getattr(spec, 'Title', lambda: spec)())
        return spec

    # 2) AT por carpeta clásica
    spec = _find_matching_at_spec(portal, analysis, ar)
    if spec:
        return spec

    # 3) Nada más (sin traversal)
    if not getattr(analysis, "getServiceUID", lambda: None)():
        _ulog('info', u"[AutoSpec] %s: sin ServiceUID y sin DX apta; se reintentará en Modified",
              getattr(analysis, 'getId', lambda: u'?')())
    else:
        _ulog('info', u"[AutoSpec] Sin Specification encontrada")
    return None

# -------------------------------------------------------------------
# APLICACIÓN DE SPEC (sin pisar manual; sin tocar ResultsRange)
# -------------------------------------------------------------------

def _apply_spec(analysis, spec):
    try:
        existing_kind, existing_obj = _current_spec_state(analysis)
        if existing_obj:
            _ulog('info', u"[AutoSpec] %s: ya tiene spec %s (%s); no se sobreescribe",
                  getattr(analysis, 'Title', lambda: u'?')(),
                  getattr(existing_obj, 'Title', lambda: existing_obj)(),
                  existing_kind or 'unknown')
            return True

        pt = _safe_unicode(getattr(spec, "portal_type", u"")) or u""
        spec_uid = _uid(spec)

        _log_capabilities(analysis, _get_analysis_spec(analysis))

        # --- DX: SOLO si hay setters DX nativos o vía AnalysisSpec existente/creable ---
        if pt in (u"DynamicAnalysisSpec", u"dynamic_analysisspec"):
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
                        _ulog('info', u"[AutoSpec] %s: DX aplicada en Analysis vía %s",
                              analysis.Title(), setter_name)
                        return True
                    except Exception:
                        pass

            # 2) Vía AnalysisSpec (créalo si falta)
            if _ensure_analysis_spec_initialized(analysis):
                aspec = _get_analysis_spec(analysis)
                _log_capabilities(analysis, aspec)
                if aspec:
                    try:
                        get_dx = getattr(aspec, "getDynamicAnalysisSpec", None)
                        curr = get_dx() if callable(get_dx) else None
                        if curr and _uid(curr) == spec_uid:
                            _ulog('info', u"[AutoSpec] %s: DX ya enlazada (%s); no-op",
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
                                _ulog('info', u"[AutoSpec] %s: DX aplicada en AnalysisSpec vía %s → %s",
                                      analysis.Title(), setter_name,
                                      getattr(spec, 'Title', lambda: spec)())
                                return True
                            except Exception:
                                pass

            _ulog('warning', u"[AutoSpec] %s: NO se pudo aplicar DX (sin setters DX ni AnalysisSpec). Skip.",
                  getattr(analysis, 'Title', lambda: u'?')())
            return False  # ← Permite fallback a AT en el llamador

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
                        _ulog('info', u"[AutoSpec] %s: AT aplicada en %s vía %s → %s",
                              analysis.Title(), owner_name, setter_name,
                              getattr(spec, 'Title', lambda: spec)())
                        break
                    except Exception:
                        pass
            if set_ok:
                break

        if not set_ok:
            _ulog('warning', u"[AutoSpec] %s: No se pudo aplicar AT (sin setters compatibles).",
                  getattr(analysis, 'Title', lambda: u'?')())
            return False

        try:
            analysis.reindexObject()
        except Exception:
            pass

        return True

    except Exception as e:
        _ulog('warning', u"[AutoSpec] No se pudo asignar Spec a %s: %r",
              getattr(analysis, 'getId', lambda: u'?')(), e)
        return False

# -------------------------------------------------------------------
# SUBSCRIBERS
# -------------------------------------------------------------------

def _apply_with_fallbacks(portal, analysis, ar):
    """
    Intenta aplicar la spec encontrada. Si es DX y falla, cae a AT automáticamente.
    """
    spec = _find_matching_spec(portal, analysis, ar)
    if not spec:
        return

    ok = _apply_spec(analysis, spec)
    if ok:
        _ulog('info', u"[AutoSpec] %s -> %s [%s]",
              getattr(spec, 'Title', lambda: spec)(),
              analysis.Title(), u"OK")
        return

    # Fallback: si la candidata era DX y no se pudo aplicar, probar AT
    pt = _safe_unicode(getattr(spec, "portal_type", u"")) or u""
    if pt in (u"DynamicAnalysisSpec", u"dynamic_analysisspec"):
        at_spec = _find_matching_at_spec(portal, analysis, ar)
        if at_spec:
            ok2 = _apply_spec(analysis, at_spec)
            _ulog('info', u"[AutoSpec] (Fallback AT) %s -> %s [%s]",
                  getattr(at_spec, 'Title', lambda: at_spec)(),
                  analysis.Title(), u"OK" if ok2 else u"FAIL")

def apply_specs_for_ar(ar, event):
    if not IObjectAddedEvent.providedBy(event):
        return
    portal = api.get_portal()
    analyses = getattr(ar, 'getAnalyses', lambda: [])() or []
    for an in analyses:
        if _user_already_selected(an):
            _ulog('info', u"[AutoSpec] %s: ya tenía selección; skip", an.Title())
            continue
        _apply_with_fallbacks(portal, an, ar)

def apply_spec_for_analysis(analysis, event):
    if not (IObjectAddedEvent.providedBy(event) or IObjectModifiedEvent.providedBy(event)):
        return
    if _user_already_selected(analysis):
        _ulog('info', u"[AutoSpec] %s: ya tenía selección; skip", analysis.Title())
        return

    ar = getattr(analysis, 'getAnalysisRequest', lambda: None)()
    if not ar:
        parent = getattr(analysis, 'aq_parent', None)
        if parent and getattr(parent, 'portal_type', '') == 'AnalysisRequest':
            ar = parent
    if not ar:
        return

    portal = api.get_portal()
    _apply_with_fallbacks(portal, analysis, ar)

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
