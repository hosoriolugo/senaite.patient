# -*- coding: utf-8 -*-
from Products.CMFCore.utils import getToolByName
from zope.lifecycleevent.interfaces import IObjectAddedEvent
from bika.lims import api, logger

def _find_matching_spec(portal, analysis, ar):
    setupcat = getToolByName(portal, 'bika_setup_catalog')
    service_uid = analysis.getServiceUID()
    client_uid = getattr(ar.aq_parent, 'UID', lambda: None)()
    sampletype_uid = getattr(analysis, 'getSampleTypeUID', lambda: None)()
    method_uid = getattr(analysis, 'getMethodUID', lambda: None)()

    queries = [
        dict(portal_type='Specification', getServiceUID=service_uid,
             getClientUID=client_uid, getSampleTypeUID=sampletype_uid,
             getMethodUID=method_uid),
        dict(portal_type='Specification', getServiceUID=service_uid,
             getClientUID=client_uid, getSampleTypeUID=sampletype_uid),
        dict(portal_type='Specification', getServiceUID=service_uid,
             getClientUID=client_uid),
        dict(portal_type='Specification', getServiceUID=service_uid),
    ]
    for q in queries:
        brains = setupcat(**q)
        if brains:
            return brains[0].getObject()
    return None

def _apply_spec(analysis, spec):
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
        logger.warn("No se pudo asignar Spec a %s: %r", analysis.getId(), e)
        return False

def apply_specs_for_ar(ar, event):
    """Al crear el AR, asigna Spec a todos sus análisis."""
    if not IObjectAddedEvent.providedBy(event):
        return
    portal = api.get_portal()
    analyses = getattr(ar, 'getAnalyses', lambda: [])() or []
    for an in analyses:
        spec = _find_matching_spec(portal, an, ar)
        if spec:
            ok = _apply_spec(an, spec)
            logger.info("Auto-apply Spec %s -> %s [%s]",
                        getattr(spec, 'Title', lambda: spec)(),
                        an.Title(), "OK" if ok else "FAIL")

def apply_spec_for_analysis(analysis, event):
    """Si se añade un análisis después, también aplicamos Spec automáticamente."""
    if not IObjectAddedEvent.providedBy(event):
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
        logger.info("Auto-apply Spec %s -> %s [%s]",
                    getattr(spec, 'Title', lambda: spec)(),
                    analysis.Title(), "OK" if ok else "FAIL")
