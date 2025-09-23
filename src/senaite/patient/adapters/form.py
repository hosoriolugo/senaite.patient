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


def safe_unicode(value, encoding="utf-8"):
    """Convierte a unicode en Py2 evitando UnicodeDecodeError al indexar."""
    try:
        if isinstance(value, unicode):
            return value
        if isinstance(value, str):
            return value.decode(encoding, "ignore")
        return unicode(value)
    except Exception:
        try:
            return unicode(str(value), encoding, "ignore")
        except Exception:
            return u""


class PatientEditForm(EditFormAdapterBase):
    """Edit form for Patient content type

    Reglas:
    - La Edad SIEMPRE se calcula desde la Fecha de Nacimiento (estimada o exacta).
    - En cuanto hay fecha -> mostrar Edad y recalcular.
    - Si marcan "fecha estimada" sin fecha aún -> mostrar Edad (vacía hasta que haya fecha).
    - Edad es de "solo lectura lógica": se ignoran ediciones manuales.
    """

    # ----------------------
    # Eventos del formulario
    # ----------------------
    def initialized(self, data):
        form = data.get("form")
        estimated_birthdate = form.get(ESTIMATED_BIRTHDATE_FIELDS[1])
        if estimated_birthdate is None:
            estimated_birthdate = form.get(ESTIMATED_BIRTHDATE_FIELDS[0])

        # Ajusta visibilidad y, si ya hay fecha, calcula
        self.toggle_and_update_fields(form, estimated_birthdate)
        self._recalc_if_possible(form)
        # Reforzar visibilidad coherente tras posibles recálculos
        self._enforce_visibility(estimated_birthdate, form)
        return self.data

    def added(self, data):
        """Algunos widgets disparan 'added' al cerrar el datepicker o al setear partes."""
        form = data.get("form")
        # recalcular si ya hay fecha y asegurar visibilidad
        self._recalc_if_possible(form)
        estimated_birthdate = form.get(ESTIMATED_BIRTHDATE_FIELDS[1])
        if estimated_birthdate is None:
            estimated_birthdate = form.get(ESTIMATED_BIRTHDATE_FIELDS[0])
        self._enforce_visibility(estimated_birthdate, form)
        return self.data

    def modified(self, data):
        name = data.get("name")
        form = data.get("form")
        value = data.get("value")

        # Cambio en checkbox "fecha estimada"
        if name in ESTIMATED_BIRTHDATE_FIELDS:
            self.toggle_and_update_fields(form, value)
            self._recalc_if_possible(form)
            self._enforce_visibility(value, form)
            return self.data

        # Cualquier cambio en fecha de nacimiento (campo o subcampos)
        if self._is_birthdate_field(name):
            bd = self._get_birthdate_from_form(form)
            if bd:
                self.update_age_field_from_birthdate(bd)
            # Respetar visibilidad según estado actual de "estimada"
            estimated_birthdate = form.get(ESTIMATED_BIRTHDATE_FIELDS[1])
            if estimated_birthdate is None:
                estimated_birthdate = form.get(ESTIMATED_BIRTHDATE_FIELDS[0])
            self._enforce_visibility(estimated_birthdate, form)
            return self.data

        # Intento de editar la Edad manualmente -> ignorar y recalcular desde la fecha
        if name == AGE_FIELD:
            bd = self._get_birthdate_from_form(form)
            if bd:
                self.update_age_field_from_birthdate(bd)
            estimated_birthdate = form.get(ESTIMATED_BIRTHDATE_FIELDS[1])
            if estimated_birthdate is None:
                estimated_birthdate = form.get(ESTIMATED_BIRTHDATE_FIELDS[0])
            self._enforce_visibility(estimated_birthdate, form)
            return self.data

        # Fallback: si no entra en ninguno, recalcular si hay fecha
        self._recalc_if_possible(form)
        return self.data

    # ----------------------
    # API de cálculo
    # ----------------------
    def update_age_field_from_birthdate(self, birthdate):
        """Calcula y setea Edad desde la fecha (formato Y/M/D)."""
        age = dtime.get_ymd(birthdate)
        age = safe_unicode(age)
        self.add_update_field(AGE_FIELD, age)

    # ----------------------
    # Visibilidad y sincronización
    # ----------------------
    def toggle_and_update_fields(self, form, estimated_birthdate):
        """Visibilidad coherente en función de si hay fecha y/o si está marcada 'estimada'."""
        is_estimated = estimated_birthdate in TRUTHY
        bd = self._get_birthdate_from_form(form)

        if bd:
            # Si hay fecha: calcular y mostrar ambos campos
            self.update_age_field_from_birthdate(bd)
            self.add_show_field(AGE_FIELD)
            self.add_show_field(BIRTHDATE_FIELDS[0])
            return

        # No hay fecha aún:
        if is_estimated:
            # con estimada, mostramos Edad (quedará vacía hasta que carguen fecha)
            self.add_show_field(AGE_FIELD)
        else:
            self.add_hide_field(AGE_FIELD)

        # Birthdate siempre visible para poder cargarla
        self.add_show_field(BIRTHDATE_FIELDS[0])

    # ----------------------
    # Helpers internos
    # ----------------------
    def _is_birthdate_field(self, name):
        if not name:
            return False
        if name in BIRTHDATE_FIELDS:
            return True
        # subcampos del datepicker: -year, -month, -day, etc.
        return name.startswith("form.widgets.birthdate-")

    def _get_birthdate_from_form(self, form):
        """Obtiene la fecha desde las claves estándar o la reconstruye de year/month/day."""
        bd = form.get(BIRTHDATE_FIELDS[0]) or form.get(BIRTHDATE_FIELDS[1])
        if bd:
            return bd

        # Reconstrucción desde partes si el widget las usa
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

    def _recalc_if_possible(self, form):
        bd = self._get_birthdate_from_form(form)
        if bd:
            self.update_age_field_from_birthdate(bd)
            # Mostrar ambos cuando hay fecha
            self.add_show_field(AGE_FIELD)
            self.add_show_field(BIRTHDATE_FIELDS[0])

    def _enforce_visibility(self, estimated_birthdate, form=None):
        """Aplica visibilidad final: con fecha -> ambos; sin fecha -> Edad solo si 'estimada'."""
        bd = self._get_birthdate_from_form(form) if form else None
        if bd:
            self.add_show_field(AGE_FIELD)
            self.add_show_field(BIRTHDATE_FIELDS[0])
            return

        if estimated_birthdate in TRUTHY:
            self.add_show_field(AGE_FIELD)
        else:
            self.add_hide_field(AGE_FIELD)

        self.add_show_field(BIRTHDATE_FIELDS[0])
