# -*- coding: utf-8 -*-
#
# This file is part of SENAITE.PATIENT.
#
# SENAITE.PATIENT is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, version 2.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Copyright 2020-2025 by it's authors.
# Some rights reserved, see README and LICENSE.

from plone.app.layout.viewlets import ViewletBase
from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile
from bika.lims.interfaces import IAnalysisRequest
from senaite.patient import api

class TemporaryMRNViewlet(ViewletBase):
    """ Print a viewlet to display a message stating the Medical Record Number
    assigned to the current Sample is Temporary
    """
    index = ViewPageTemplateFile("templates/temporary_mrn_viewlet.pt")

    def __init__(self, context, request, view, manager=None):
        super(TemporaryMRNViewlet, self).__init__(
            context, request, view, manager=manager)
        self.context = context
        self.request = request
        self.view = view

    def is_visible(self):
        """Returns whether this viewlet must be visible or not
        """
        # Si el contexto es AnalysisRequest, obtener el paciente
        if IAnalysisRequest.providedBy(self.context):
            patient = self.context.getPatient()
            if patient and hasattr(patient, 'getTemporary'):
                return patient.getTemporary()
        
        # Si el contexto es Patient directamente
        if hasattr(self.context, 'getTemporary'):
            return self.context.getTemporary()
        
        # Si el contexto tiene el método antiguo (backward compatibility)
        if hasattr(self.context, 'isMedicalRecordTemporary'):
            return self.context.isMedicalRecordTemporary()
        
        # Intentar obtener paciente por otros medios
        try:
            patient = api.get_patient(self.context)
            if patient and hasattr(patient, 'getTemporary'):
                return patient.getTemporary()
        except:
            pass
        
        return False
