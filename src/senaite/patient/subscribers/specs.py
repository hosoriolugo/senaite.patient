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
            return unicode(x)  # noqa: F821  # Py2
        except Exception:
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

def _title(obj, default=u"?"):
    try:
        t = getattr(obj, "Title", None)
        if callable(t):
            v = t() or default
            return _safe_unicode(v)
        # algunos objetos exponen 'title' como attr o prop
        val = getattr(obj, "title", None)
        if isinstance(val, basestring):
            return _safe_unicode(val or default)
    except Exception:
        pass
    return _safe_unicode(default)

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
        a_kw = _safe_unicode(a_kw)
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
                "setDynamicAnalysisSpec": bool(aspec and callable(getattr(aspec, "setDynamicAnalysisSpec", None))),
                "setDynamicAnalysisSpecUID": bool(aspec and callable(getattr(aspec, "setDynamicAnalysisSpecUID", None))),
                "getSpecification": bool(aspec and callable(getattr(aspec, "getSpecification", None))),
                "getDynamicAnalysisSpec": bool(aspec and callable(getattr(aspec, "getDynamicAnalysisSpec", None))),
            }
        }
        logger.info(u"[AutoSpec][caps] %s svc=%s caps=%r", a_kw, svc_uid, caps)
    except Exception as e:
        logger.warning(u"[AutoSpec][caps] fallo al loggear capacidades: %r", e)

# -------------------------------------------------------------------
# PACIENTE: sexo y edad (días)
# -------------------------------------------------------------------

def _get_patient_from_ar(ar):
    try:
        # SENAITE Patient add-on: AR puede exponer getPatient() o un field
        pat = getattr(ar, 'getPatient', lambda: None)()
        if pat:
            return pat
    except Exception:
        pass
    try:
        # a veces el paciente cuelga de una carpeta PatientFolder con relación
        return getattr(ar, 'Patient', None)
    except Exception:
        return None

def _norm_gender_code(g):
    """Normaliza género a 'M'/'F'/'U'."""
    if not g:
        return 'U'
    try:
        s = _safe_unicode(g).strip().lower()
    except Exception:
        s = str(g).strip().lower()
    if s in ('m', 'male', 'masculino', 'man', 'h', 'hombre'):
        return 'M'
    if s in ('f', 'female', 'femenino', 'woman', 'mujer'):
        return 'F'
    # algunos sistemas guardan 'U', 'X', 'O', '-', ''
    return 'U'

def _get_patient_gender(ar):
    pat = _get_patient_from_ar(ar)
    if not pat:
        return 'U'
    for getter in ('getGender', 'Gender', 'gender'):
        try:
            fn = getattr(pat, getter, None)
            v = fn() if callable(fn) else fn
            if v:
                return _norm_gender_code(v)
        except Exception:
            continue
    return 'U'

def _to_date(obj):
    """Convierte DateTime/datetime/str a date; None si falla."""
    try:
        # Zope DateTime
        from DateTime import DateTime as ZDT  # noqa
        if isinstance(obj, ZDT):
            return obj.asdatetime().date()
    except Exception:
        pass
    try:
        import datetime as _dt
        if isinstance(obj, _dt.datetime):
            return obj.date()
        if isinstance(obj, _dt.date):
            return obj
    except Exception:
        pass
    # intentar parseo muy básico de 'YYYY-MM-DD'
    try:
        import datetime as _dt
        s = _safe_unicode(obj).strip()
        parts = s.split('T')[0].split('-')
        if len(parts) == 3:
            y, m, d = [int(x) for x in parts]
            return _dt.date(y, m, d)
    except Exception:
        return None
    return None

def _get_patient_birthdate(ar):
    pat = _get_patient_from_ar(ar)
    if not pat:
        return None
    for getter in ('getBirthDate', 'getBirthdate', 'BirthDate', 'birthdate', 'birth_date'):
        try:
            fn = getattr(pat, getter, None)
            v = fn() if callable(fn) else fn
            if v:
                return _to_date(v)
        except Exception:
            continue
    # a veces solo hay una string "getLocalizedBirthdate", no confiable para cálculo
    return None

def _today_date():
    try:
        import datetime as _dt
        return _dt.date.today()
    except Exception:
        return None

def _age_in_days(ar):
    """Edad del paciente en días; None si no se puede calcular."""
    b = _get_patient_birthdate(ar)
    t = _today_date()
    if not (b and t):
        return None
    try:
        return (t - b).days
    except Exception:
        return None

# -------------------------------------------------------------------
# AJUSTE: soporte de filas/keyword para DX (ahora con sexo/edad)
# -------------------------------------------------------------------

def _iter_dx_rows(dx):
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
        return
    # normalizar posibles tipos (BTrees, tuple, etc.)
    try:
        for r in rows:
            yield r
    except Exception:
        return

def _norm_upper(x):
    if x is None:
        return None
    try:
        return _safe_unicode(x).strip().upper()
    except Exception:
        try:
            return str(x).strip().upper()
        except Exception:
            return x

def _row_value(r, *keys):
    for k in keys:
        if k in r:
            return r.get(k)
    return None

def _dx_best_row_for(keyword, gender, age_days, dx, client_uid=None, sampletype_uid=None, method_uid=None):
    """
    Selecciona la mejor fila de DX para (keyword, gender, age_days).
    Prioridad:
      1) filas cuyo gender == 'M'/'F' que coincida; si no hay, usar 'U' o vacío.
      2) edad dentro de [age_min_days; age_max_days] cuando ambos existan.
      3) si no hay edad calculable, no se filtra por edad.
      4) si hay client_uid/sampletype_uid/method_uid en la fila, deben coincidir si vienen.
    Devuelve (fila_dict) o None.
    """
    if not dx:
        return None

    kw = _norm_upper(keyword)
    g = (gender or 'U')
    g_u = _norm_upper(g)

    # recolectar candidatas por keyword
    rows_kw = []
    for r in _iter_dx_rows(dx):
        k = _norm_upper(_row_value(r, "Keyword", "keyword", "service_keyword"))
        if k == kw:
            rows_kw.append(r)
    if not rows_kw:
        return None

    # helper de coincidencias “suaves/estrictas”
    def _uid_ok(val, target):
        if not target:
            return True
        if not val:
            return True
        return _norm_upper(val) == _norm_upper(target)

    def _age_ok(r_):
        if age_days is None:
            return True  # sin edad -> no filtramos por edad
        try:
            amin = _row_value(r_, "age_min_days", "age_min", "age_min_d", "min_age_days")
            amax = _row_value(r_, "age_max_days", "age_max", "age_max_d", "max_age_days")
            amin = int(amin) if amin not in (None, '') else None
            amax = int(amax) if amax not in (None, '') else None
        except Exception:
            return True
        if amin is not None and age_days < amin:
            return False
        if amax is not None and age_days > amax:
            return False
        return True

    # separa por preferencia de género
    exact_gender = []
    unisex = []
    others = []
    for r in rows_kw:
        rg = _norm_upper(_row_value(r, "gender", "Gender", "sex"))
        # filtros de UID por fila (si están presentes)
        if not _uid_ok(_row_value(r, "client_uid", "ClientUID", "client"), client_uid):
            continue
        if not _uid_ok(_row_value(r, "sampletype_uid", "SampleTypeUID", "sample_type"), sampletype_uid):
            continue
        if not _uid_ok(_row_value(r, "method_uid", "MethodUID", "method"), method_uid):
            continue
        if not _age_ok(r):
            continue

        if rg in ('M', 'F'):
            if rg == g_u:
                exact_gender.append(r)
            else:
                others.append(r)
        else:
            # 'U', vacío, 'X'…
            unisex.append(r)

    choose = exact_gender or unisex or others
    return choose[0] if choose else None

def _dx_supports(dx, keyword, client_uid=None, sampletype_uid=None, method_uid=None,
                 gender=None, age_days=None):
    """
    True si la DX contiene alguna fila aplicable al análisis:
      - keyword coincide
      - (si hay) coinciden client/sampletype/method
      - (si hay) coincide género y edad
    None si no se pudo inspeccionar filas.
    """
    try:
        best = _dx_best_row_for(keyword, gender, age_days, dx, client_uid, sampletype_uid, method_uid)
        if best is None:
            # si no hay filas o no pudimos iterarlas, devolvemos None solo si no hay filas;
            # pero si sí hay filas y ninguna aplicó, devolvemos False
            # Comprobamos si hay filas por keyword:
            found_kw = False
            for r in _iter_dx_rows(dx) or []:
                k = _norm_upper(_row_value(r, "Keyword", "keyword", "service_keyword"))
                if k == _norm_upper(keyword):
                    found_kw = True
                    break
            return None if not found_kw else False
        return True
    except Exception:
        return None

def _log_dx_row_selected(analysis, dx, row):
    """Log detallado de la fila elegida (ayuda a validar sexo/edad/unidad/min/max)."""
    try:
        if not row:
            return
        kw = getattr(analysis, "getKeyword", getattr(analysis, "getId", lambda: "?"))()
        unit = _row_value(row, "unit", "Unit")
        vmin = _row_value(row, "min", "Min")
        vmax = _row_value(row, "max", "Max")
        warn_low = _row_value(row, "warn_low")
        warn_high = _row_value(row, "warn_high")
        panic_low = _row_value(row, "panic_low")
        panic_high = _row_value(row, "panic_high")
        target = _row_value(row, "target", "Target")
        gender = _row_value(row, "gender", "Gender", "sex")
        amin = _row_value(row, "age_min_days", "age_min", "age_min_d", "min_age_days")
        amax = _row_value(row, "age_max_days", "age_max", "age_max_d", "max_age_days")
        notes = _row_value(row, "notes", "Notes")

        logger.info(
            u"[AutoSpec][row] %s → %s | gender=%s age=[%s;%s] unit=%s min=%s max=%s warn=[%s;%s] panic=[%s;%s] target=%s notes=%s",
            _safe_unicode(_title(dx)), _safe_unicode(kw), _safe_unicode(gender),
            _safe_unicode(amin), _safe_unicode(amax), _safe_unicode(unit),
            _safe_unicode(vmin), _safe_unicode(vmax),
            _safe_unicode(warn_low), _safe_unicode(warn_high),
            _safe_unicode(panic_low), _safe_unicode(panic_high),
            _safe_unicode(target), _safe_unicode(notes),
        )
    except Exception:
        # no bloquea
        pass

# -------------------------------------------------------------------
# SELECTOR DE SPEC (prioriza DX)
# -------------------------------------------------------------------

def _prefer_dx_spec(portal, analysis, ar):
    """No exige soporte DX por adelantado; selecciona candidata y _apply_spec la enlaza.
       Ahora pondera con sexo y edad para elegir mejor cuando hay varias DX."""
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

    # Datos del análisis/paciente para filtrar
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

    gender = _get_patient_gender(ar)
    age_days = _age_in_days(ar)

    wanted_titles = tuple(t.strip().lower() for t in PREFERRED_DX_TITLES)
    scored = []
    for obj in dx_specs:
        score = 0

        # +100 si la DX es específica del cliente y coincide
        cx = getattr(obj, "client_uid", None) or _obj_uid(obj, "getClientUID")
        if cx and client_uid and cx == client_uid:
            score += 100

        # +60 si soporta keyword + sexo + edad (+ filtros opcionales)
        sup = _dx_supports(obj, keyword, client_uid, sampletype_uid, method_uid, gender, age_days)
        if sup is True:
            score += 60
        elif sup is None:
            score += 10  # no se pudo inspeccionar, posible candidata

        # +5 por título preferido
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
        logger.info(u"[AutoSpec] DX candidate (scored): %s", _title(best))
    return best

# -------------------------------------------------------------------
# BÚSQUEDA DE SPEC
# -------------------------------------------------------------------

def _find_matching_spec(portal, analysis, ar):
    """Devuelve una spec (prioriza DX). No abandona si falta ServiceUID."""
    # 1) Intentar DX primero (independiente del ServiceUID)
    spec = _prefer_dx_spec(portal, analysis, ar)
    if spec:
        logger.info(u"[AutoSpec] DX candidate: %s", _safe_unicode(_title(spec)))
        return spec

    # 2) AT por carpeta de setup clásico (match estricto cuando la spec define service)
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
                        logger.info(u"[AutoSpec] AT candidate: %s", _title(cand))
                        return cand

                if not AT_FALLBACK_FIRST:
                    logger.info(u"[AutoSpec] AT: no se encontró Specification que coincida con el servicio")
                else:
                    logger.info(u"[AutoSpec] AT fallback (first): %s", _title(at_specs[0]))
                    return at_specs[0]

    # 3) Traversal (opcional) — por defecto off para prod
    if not ALLOW_TRAVERSAL_FALLBACK:
        if not getattr(analysis, "getServiceUID", lambda: None)():
            logger.info(u"[AutoSpec] %s: sin ServiceUID y sin DX apta; se reintentará en Modified",
                        _safe_unicode(getattr(analysis, 'getId', lambda: '?')()))
        else:
            logger.info(u"[AutoSpec] Sin Specification encontrada")
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
                logger.info(u"[AutoSpec] Traversal candidate (AT-only): %s", _title(cand))
                return cand
        logger.info(u"[AutoSpec] Traversal no encontró AT compatible")
        logger.info(u"[AutoSpec] Sin Specification encontrada")
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

    def _parent_uid(ar_):
        try:
            return ar_.aq_parent.UID() if hasattr(ar_.aq_parent, 'UID') else None
        except Exception:
            return None

    # datos del paciente
    gender = _get_patient_gender(ar)
    age_days = _age_in_days(ar)

    for cand in _iter_specs_by_traversal(portal):
        pt = getattr(cand, "portal_type", "")

        if pt in ("DynamicAnalysisSpec", "dynamic_analysisspec") and kw:
            try:
                sup = _dx_supports(cand, kw, _parent_uid(ar),
                                   getattr(analysis, "getSampleTypeUID", lambda: None)(),
                                   getattr(analysis, "getMethodUID", lambda: None)(),
                                   gender, age_days)
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
        logger.info(u"[AutoSpec] Traversal candidate (filtered): %s", _title(best))
        return best

    logger.info(u"[AutoSpec] Traversal no encontró candidatos válidos")
    logger.info(u"[AutoSpec] Sin Specification encontrada")
    return None

# -------------------------------------------------------------------
# APLICACIÓN DE SPEC (sin pisar manual; sin tocar ResultsRange)
# -------------------------------------------------------------------

def _apply_spec(analysis, spec):
    try:
        existing_kind, existing_obj = _current_spec_state(analysis)
        if existing_obj:
            logger.info(u"[AutoSpec] %s: ya tiene spec %s (%s); no se sobreescribe",
                        _safe_unicode(_title(analysis)), _safe_unicode(_title(existing_obj)), existing_kind or 'unknown')
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
                        logger.info(u"[AutoSpec] %s: DX aplicada en Analysis vía %s",
                                    _safe_unicode(_title(analysis)), setter_name)
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
                            logger.info(u"[AutoSpec] %s: DX ya enlazada (%s); no-op",
                                        _safe_unicode(_title(analysis)), _safe_unicode(_title(spec)))
                            # Logueamos fila elegida para inspección (sexo/edad)
                            _log_dx_row_selected_from_analysis(aspec, analysis)
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
                                logger.info(u"[AutoSpec] %s: DX aplicada en AnalysisSpec vía %s → %s",
                                            _safe_unicode(_title(analysis)), setter_name, _safe_unicode(_title(spec)))
                                # Logueamos fila elegida para inspección (sexo/edad)
                                _log_dx_row_selected_from_analysis(aspec, analysis)
                                return True
                            except Exception:
                                pass

            logger.warning(u"[AutoSpec] %s: NO se pudo aplicar DX (sin setters DX ni AnalysisSpec). Skip.",
                           _safe_unicode(_title(analysis)))
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
                        logger.info(u"[AutoSpec] %s: AT aplicada en %s vía %s → %s",
                                    _safe_unicode(_title(analysis)), owner_name, setter_name, _safe_unicode(_title(spec)))
                        break
                    except Exception:
                        pass
            if set_ok:
                break

        if not set_ok:
            logger.warning(u"[AutoSpec] %s: No se pudo aplicar AT (sin setters compatibles).",
                           _safe_unicode(_title(analysis)))
            return False

        try:
            analysis.reindexObject()
        except Exception:
            pass

        return True

    except Exception as e:
        logger.warning(u"[AutoSpec] No se pudo asignar Spec a %s: %r",
                       _safe_unicode(getattr(analysis, 'getId', lambda: '?')()), e)
        return False

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

def _log_dx_row_selected_from_analysis(aspec, analysis):
    """Si hay DX enlazada, localiza y loguea la fila aplicable según sexo/edad."""
    try:
        get_dx = getattr(aspec, "getDynamicAnalysisSpec", None)
        dx = get_dx() if callable(get_dx) else None
        if not dx:
            return
        ar = getattr(analysis, 'getAnalysisRequest', lambda: None)() or getattr(analysis, 'aq_parent', None)
        gender = _get_patient_gender(ar) if ar else 'U'
        age_days = _age_in_days(ar) if ar else None
        keyword = (getattr(analysis, "getKeyword", None) or getattr(analysis, "getId", None) or (lambda: None))()
        best = _dx_best_row_for(keyword, gender, age_days, dx,
                                getattr(aspec, "getClientUID", lambda: None)(),
                                getattr(analysis, "getSampleTypeUID", lambda: None)(),
                                getattr(analysis, "getMethodUID", lambda: None)())
        if best:
            _log_dx_row_selected(analysis, dx, best)
    except Exception:
        pass

# -------------------------------------------------------------------
# SUBSCRIBERS
# -------------------------------------------------------------------

def _ensure_spec_ui(analysis):
    """Garantiza que exista el hijo AnalysisSpec para que la UI muestre '± Especificaciones'."""
    try:
        created = _ensure_analysis_spec_initialized(analysis)
        if created:
            logger.info(u"[AutoSpec] %s: AnalysisSpec presente (UI listo para '± Especificaciones')",
                        _safe_unicode(_title(analysis)))
        else:
            logger.info(u"[AutoSpec] %s: no se pudo garantizar AnalysisSpec (UI podría no mostrar '±')",
                        _safe_unicode(_title(analysis)))
    except Exception as e:
        logger.warning(u"[AutoSpec] %s: error asegurando AnalysisSpec para UI: %r",
                       _safe_unicode(_title(analysis)), e)

def apply_specs_for_ar(ar, event):
    if not IObjectAddedEvent.providedBy(event):
        return
    portal = api.get_portal()
    analyses = getattr(ar, 'getAnalyses', lambda: [])() or []
    for an in analyses:
        # 0) Asegurar que la UI muestre '± Especificaciones'
        _ensure_spec_ui(an)

        # 1) Si el usuario ya seleccionó algo, no tocamos
        if _user_already_selected(an):
            logger.info(u"[AutoSpec] %s: ya tenía selección; skip", _safe_unicode(_title(an)))
            continue

        # 2) Intentar asignación automática
        spec = _find_matching_spec(portal, an, ar)
        if spec:
            ok = _apply_spec(an, spec)
            logger.info(u"[AutoSpec] %s -> %s [%s]", _safe_unicode(_title(spec)), _safe_unicode(_title(an)), "OK" if ok else "FAIL")

def apply_spec_for_analysis(analysis, event):
    if not (IObjectAddedEvent.providedBy(event) or IObjectModifiedEvent.providedBy(event)):
        return

    # 0) Asegurar siempre el hijo para visibilidad de UI
    _ensure_spec_ui(analysis)

    # 1) Respetar selección/ResultsRange manual previa
    if _user_already_selected(analysis):
        logger.info(u"[AutoSpec] %s: ya tenía selección; skip", _safe_unicode(_title(analysis)))
        return

    # 2) Resolver AR contenedor
    ar = getattr(analysis, 'getAnalysisRequest', lambda: None)()
    if not ar:
        parent = getattr(analysis, 'aq_parent', None)
        if parent and getattr(parent, 'portal_type', '') == 'AnalysisRequest':
            ar = parent
    if not ar:
        return

    # 3) Buscar y aplicar
    portal = api.get_portal()
    spec = _find_matching_spec(portal, analysis, ar)
    if spec:
        ok = _apply_spec(analysis, spec)
        logger.info(u"[AutoSpec] %s -> %s [%s]", _safe_unicode(_title(spec)), _safe_unicode(_title(analysis)), "OK" if ok else "FAIL")

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
