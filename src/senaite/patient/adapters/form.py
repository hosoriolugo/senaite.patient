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

    Lógica acordada:
    - Siempre se muestran Birthdate y Age.
    - Age siempre se calcula desde Birthdate (formato YMD: '45y 3m 20d', '67y').
    - Si 'estimated_birthdate' está marcada, la UI/plantilla muestra el aviso
      de “edad estimada”, pero aquí no se ocultan campos.
    """

    def initialized(self, data):
        form = data.get("form")
        estimated = form.get(ESTIMATED_BIRTHDATE_FIELDS[1])
        if estimated is None:
            estimated = form.get(ESTIMATED_BIRTHDATE_FIELDS[0])
        self._sync_fields(form, estimated)
        # Fallback: si ya hay fecha, calcula
        self._recalc_if_possible(form)
        return self.data

    def added(self, data):
        """Algunos widgets (datepicker) disparan 'added' al inicializar/cambiar internamente.
        Recalculamos aquí también para no depender únicamente de 'modified'."""
        form = data.get("form")
        estimated = form.get(ESTIMATED_BIRTHDATE_FIELDS[1])
        if estimated is None:
            estimated = form.get(ESTIMATED_BIRTHDATE_FIELDS[0])
        self._sync_fields(form, estimated)
        self._recalc_if_possible(form)
        return self.data

    def modified(self, data):
        name = data.get("name")
        form = data.get("form")
        value = data.get("value")

        # Cambios en el checkbox "estimated" -> recalcula desde la fecha que haya
        if name in ESTIMATED_BIRTHDATE_FIELDS:
            self._sync_fields(form, value)
            self._recalc_if_possible(form)
            return self.data

        # Cualquier cambio relacionado con la fecha de nacimiento (incluye subcampos -year/-month/-day)
        if self._is_birthdate_field(name):
            self._update_age_from_birthdate(self._get_birthdate_from_form(form))
            self._show_all()
            return self.data

        # Si el usuario edita Age a mano, lo ignoramos y lo recalculamos desde Birthdate
        if name == AGE_FIELD:
            self._update_age_from_birthdate(self._get_birthdate_from_form(form))
            self._show_all()
            return self.data

        # Fallback: aunque 'name' no sea ninguno de los anteriores, si hay DoB -> recalcular
        self._recalc_if_possible(form)
        return self.data

    # ----------------------
    # Helpers
    # ----------------------
    def _show_all(self):
        self.add_show_field(AGE_FIELD)
        self.add_show_field(BIRTHDATE_FIELDS[0])

    def _is_birthdate_field(self, name):
        """Devuelve True si el nombre corresponde a cualquier variante del widget de fecha."""
        if not name:
            return False
        if name in BIRTHDATE_FIELDS:
            return True
        # capta subcampos como birthdate-year/month/day/hour/minute, etc.
        return name.startswith("form.widgets.birthdate-")

    def _get_birthdate_from_form(self, form):
        """Obtiene la fecha desde las claves estándar o la reconstruye de year/month/day."""
        # 1) claves directas del widget
        bd = form.get(BIRTHDATE_FIELDS[0]) or form.get(BIRTHDATE_FIELDS[1])
        if bd:
            return bd

        # 2) reconstrucción desde partes year/month/day si existen
        y = form.get("form.widgets.birthdate-year")
        m = form.get("form.widgets.birthdate-month")
        d = form.get("form.widgets.birthdate-day")
        if y and m and d:
            try:
                y_i = int(str(y))
                m_i = int(str(m))
                d_i = int(str(d))
                return dtime.datetime(y_i, m_i, d_i).date()
            except Exception:
                return None

        return None

    def _update_age_from_birthdate(self, birthdate):
        # Mantiene el formato YMD nativo (e.g., '57y 4m 20d')
        age = dtime.get_ymd(birthdate)
        self.add_update_field(AGE_FIELD, age)

    def _sync_fields(self, form, estimated_flag):
        """Sincroniza visibilidad/valores (no oculta campos)."""
        # Siempre mostrar ambos campos
        self._show_all()

        # Si ya hay fecha, calcula Age
        birthdate = self._get_birthdate_from_form(form)
        if birthdate:
            self._update_age_from_birthdate(birthdate)
        # 'estimated_flag' queda disponible para que la plantilla muestre el aviso.

    def _recalc_if_possible(self, form):
        """Recalcula la edad si hay fecha disponible, aunque el evento no sea de DoB."""
        bd = self._get_birthdate_from_form(form)
        if bd:
            self._update_age_from_birthdate(bd)
            self._show_all()
