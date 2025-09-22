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

from senaite.core.api import dtime
from senaite.core.browser.form.adapters import EditFormAdapterBase


ESTIMATED_BIRTHDATE_FIELDS = (
    "form.widgets.estimated_birthdate",
    "form.widgets.estimated_birthdate:list"
)
AGE_FIELD = "form.widgets.age"
BIRTHDATE_FIELDS = (
    "form.widgets.birthdate",
    "form.widgets.birthdate-date"
)

TRUTHY = {True, "selected", "on", "true", "1", u"on", u"true", u"1"}


class PatientEditForm(EditFormAdapterBase):
    """Edit form for Patient content type

    Nueva lógica:
    - Siempre se muestran Birthdate y Age.
    - Age siempre se calcula desde Birthdate (no inferimos DoB desde Age).
    - Si 'estimated_birthdate' está marcada, la UI puede mostrar un aviso,
      pero no se ocultan campos aquí.
    """

    def initialized(self, data):
        form = data.get("form")
        estimated = form.get(ESTIMATED_BIRTHDATE_FIELDS[1])
        if estimated is None:
            estimated = form.get(ESTIMATED_BIRTHDATE_FIELDS[0])
        self._sync_fields(form, estimated)
        return self.data

    def modified(self, data):
        name = data.get("name")
        form = data.get("form")
        value = data.get("value")

        # Cambios en el checkbox "estimated"
        if name in ESTIMATED_BIRTHDATE_FIELDS:
            self._sync_fields(form, value)
            return self.data

        # Cambios en la fecha de nacimiento -> recalcula Age
        if name in BIRTHDATE_FIELDS:
            self._update_age_from_birthdate(form.get(BIRTHDATE_FIELDS[0]))
            self._show_all()
            return self.data

        # Si el usuario edita Age a mano, lo ignoramos y lo recalculamos desde Birthdate
        if name == AGE_FIELD:
            self._update_age_from_birthdate(form.get(BIRTHDATE_FIELDS[0]))
            self._show_all()
            return self.data

        return self.data

    # ----------------------
    # Helpers
    # ----------------------
    def _show_all(self):
        self.add_show_field(AGE_FIELD)
        self.add_show_field(BIRTHDATE_FIELDS[0])

    def _update_age_from_birthdate(self, birthdate):
        age = dtime.get_ymd(birthdate)
        self.add_update_field(AGE_FIELD, age)

    def _sync_fields(self, form, estimated_flag):
        """Sincroniza visibilidad/valores según el estado de 'estimated'."""
        # Siempre mostrar ambos campos
        self._show_all()

        # Siempre calcular Age desde Birthdate (si ya hay valor)
        birthdate = form.get(BIRTHDATE_FIELDS[0])
        if birthdate:
            self._update_age_from_birthdate(birthdate)

        # Nota: 'estimated_flag' queda disponible para la plantilla/UI.
        # Aquí no se ocultan campos; la advertencia de "edad estimada" la maneja la vista.
