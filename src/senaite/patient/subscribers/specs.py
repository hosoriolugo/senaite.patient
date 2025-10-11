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

def _safe_unicode(value):
    """Convierte a unicode de forma segura"""
    try:
        return unicode(value)
    except UnicodeDecodeError:
        return value.decode('utf-8', 'replace')
    except:
        return str(value)

def _uid(obj):
    """Obtiene UID de forma segura"""
    if obj is None:
        return None
    try:
        return api.get_uid(obj)
    except:
        try:
            return obj.UID()
        except:
            return getattr(obj, 'id', None) or repr(obj)

def _get_ar_info(analysis):
    """Obtiene información del AnalysisRequest padre"""
    try:
        ar = analysis.aq_parent
        if getattr(ar, 'portal_type', '') == 'AnalysisRequest':
            client = ar.getClient()
            sample_type = ar.getSampleType()
            return {
                'ar': ar,
                'client_uid': _uid(client),
                'sampletype_uid': _uid(sample_type)
            }
    except:
        pass
    return {'ar': None, 'client_uid': None, 'sampletype_uid': None}

def _find_best_spec(analysis, portal):
    """Encuentra la mejor especificación para el análisis"""
    ar_info = _get_ar_info(analysis)
    service_uid = _uid(analysis.getService())
    
    if not service_uid:
        return None

    # Buscar en Dynamic Analysis Specs (DX)
    setup = getattr(portal, 'setup', None)
    if setup:
        dx_folder = getattr(setup, 'dynamicanalysisspecs', None) or \
                   getattr(setup, 'dynamic_analysisspecs', None)
        if dx_folder:
            for spec in dx_folder.objectValues():
                if _spec_matches(spec, service_uid, ar_info['client_uid'], 
                               ar_info['sampletype_uid'], None):
                    return spec

    # Buscar en Specifications clásicas (AT)
    bika_setup = getattr(portal, 'bika_setup', None)
    if bika_setup:
        specs_folder = getattr(bika_setup, 'specifications', None) or \
                      getattr(bika_setup, 'bika_specifications', None)
        if specs_folder:
            for spec in specs_folder.objectValues():
                if getattr(spec, 'portal_type', '') == 'Specification':
                    if _spec_matches(spec, service_uid, ar_info['client_uid'],
                                   ar_info['sampletype_uid'], None):
                        return spec
    return None

def _spec_matches(spec, service_uid, client_uid, sampletype_uid, method_uid):
    """Verifica si la spec coincide con los parámetros"""
    try:
        # Obtener service UID de la spec
        spec_service_uid = None
        if hasattr(spec, 'getServiceUID'):
            spec_service_uid = spec.getServiceUID()
        elif hasattr(spec, 'service_uid'):
            spec_service_uid = spec.service_uid
        elif hasattr(spec, 'getService'):
            service_obj = spec.getService()
            spec_service_uid = _uid(service_obj)
        
        if spec_service_uid and service_uid and spec_service_uid != service_uid:
            return False

        # Verificar cliente
        spec_client_uid = None
        if hasattr(spec, 'getClientUID'):
            spec_client_uid = spec.getClientUID()
        elif hasattr(spec, 'client_uid'):
            spec_client_uid = spec.client_uid
        
        if spec_client_uid and client_uid and spec_client_uid != client_uid:
            return False

        # Verificar tipo de muestra
        spec_sampletype_uid = None
        if hasattr(spec, 'getSampleTypeUID'):
            spec_sampletype_uid = spec.getSampleTypeUID()
        elif hasattr(spec, 'sampletype_uid'):
            spec_sampletype_uid = spec.sampletype_uid
        
        if spec_sampletype_uid and sampletype_uid and spec_sampletype_uid != sampletype_uid:
            return False

        return True
    except:
        return False

def _apply_spec_to_analysis(analysis, spec):
    """Aplica la especificación al análisis"""
    try:
        spec_uid = _uid(spec)
        
        # Intentar métodos DX primero
        if hasattr(analysis, 'setDynamicAnalysisSpec'):
            analysis.setDynamicAnalysisSpec(spec)
            logger.info("Spec aplicada via setDynamicAnalysisSpec: %s", analysis.getId())
            return True
        elif hasattr(analysis, 'setDynamicAnalysisSpecUID'):
            analysis.setDynamicAnalysisSpecUID(spec_uid)
            logger.info("Spec aplicada via setDynamicAnalysisSpecUID: %s", analysis.getId())
            return True
        
        # Métodos AT clásicos
        if hasattr(analysis, 'setSpecification'):
            analysis.setSpecification(spec)
            logger.info("Spec aplicada via setSpecification: %s", analysis.getId())
            return True
        elif hasattr(analysis, 'setSpecificationUID'):
            analysis.setSpecificationUID(spec_uid)
            logger.info("Spec aplicada via setSpecificationUID: %s", analysis.getId())
            return True
        
        # Último recurso: usar ResultsRange directamente
        if hasattr(spec, 'getResultsRange'):
            results_range = spec.getResultsRange()
            if results_range and hasattr(analysis, 'setResultsRange'):
                analysis.setResultsRange(results_range)
                logger.info("Spec aplicada via setResultsRange: %s", analysis.getId())
                return True
        
        logger.warning("No se pudo aplicar spec al análisis %s", analysis.getId())
        return False
        
    except Exception as e:
        logger.error("Error aplicando spec a %s: %s", analysis.getId(), str(e))
        return False

def _analysis_has_manual_spec(analysis):
    """Verifica si el análisis ya tiene una spec manual"""
    try:
        # Verificar si ya tiene ResultsRange definido
        if hasattr(analysis, 'getResultsRange'):
            rr = analysis.getResultsRange()
            if rr and len(rr) > 0:
                return True
        
        # Verificar si ya tiene spec definida
        if hasattr(analysis, 'getSpecification'):
            spec = analysis.getSpecification()
            if spec:
                return True
                
        if hasattr(analysis, 'getDynamicAnalysisSpec'):
            dx_spec = analysis.getDynamicAnalysisSpec()
            if dx_spec:
                return True
                
        return False
    except:
        return False

# Subscribers principales
def on_analysis_request_added(ar, event):
    """Se ejecuta cuando se crea un nuevo AnalysisRequest"""
    if not IObjectAddedEvent.providedBy(event):
        return
        
    try:
        portal = api.get_portal()
        analyses = ar.getAnalyses()
        
        for analysis in analyses:
            # Saltar si ya tiene spec manual
            if _analysis_has_manual_spec(analysis):
                logger.info("Análisis %s ya tiene spec manual, omitiendo", analysis.getId())
                continue
                
            # Buscar mejor spec
            spec = _find_best_spec(analysis, portal)
            if spec:
                _apply_spec_to_analysis(analysis, spec)
                logger.info("Spec aplicada automáticamente a %s", analysis.getId())
                
    except Exception as e:
        logger.error("Error en on_analysis_request_added: %s", str(e))

def on_analysis_added(analysis, event):
    """Se ejecuta cuando se crea un nuevo Analysis"""
    if not IObjectAddedEvent.providedBy(event):
        return
        
    try:
        # Saltar si ya tiene spec manual
        if _analysis_has_manual_spec(analysis):
            return
            
        portal = api.get_portal()
        spec = _find_best_spec(analysis, portal)
        if spec:
            _apply_spec_to_analysis(analysis, spec)
            logger.info("Spec aplicada automáticamente a %s", analysis.getId())
            
    except Exception as e:
        logger.error("Error en on_analysis_added: %s", str(e))

# Registro de subscribers (debes tener esto en tu configure.zcml)
"""
<subscriber
    for="bika.lims.interfaces.IAnalysisRequest
         zope.lifecycleevent.IObjectAddedEvent"
    handler=".specs.on_analysis_request_added"
    />

<subscriber
    for="bika.lims.interfaces.IAnalysis
         zope.lifecycleevent.IObjectAddedEvent"
    handler=".specs.on_analysis_added"
    />
"""
