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
    """

    # ----------------------
    # Eventos del formulario
    # ----------------------
    def initialized(self, data):
        form = data.get("form")
        estimated_birthdate = form.get(ESTIMATED_BIRTHDATE_FIELDS[1])
        if estimated_birthdate is None:
            estimated_birthdate = form.get(ESTIMATED_BIRTHDATE_FIELDS[0])
        self.toggle_and_update_fields(form, estimated_birthdate)
        # Si ya hay fecha, calcula edad para dejar el formulario consistente
        self._recalc_if_possible(form)
        return self.data

    def modified(self, data):
        name = data.get("name")
        form = data.get("form")
        value = data.get("value")

        # Cambio en checkbox de "fecha estimada"
        if name in ESTIMATED_BIRTHDATE_FIELDS:
            self.toggle_and_update_fields(form, value)
            # También recalcular por si ya hay fecha
            self._recalc_if_possible(form)
            return self.data

        # Cualquier cambio en fecha de nacimiento (campo o subcampos del widget)
        if self._is_birthdate_field(name):
            birthdate = form.get(BIRTHDATE_FIELDS[0]) or form.get(BIRTHDATE_FIELDS[1])
            if birthdate:
                self.update_age_field_from_birthdate(birthdate)
            # Respetar visibilidad vigente según "estimada"
            estimated_birthdate = form.get(ESTIMATED_BIRTHDATE_FIELDS[1])
            if estimated_birthdate is None:
                estimated_birthdate = form.get(ESTIMATED_BIRTHDATE_FIELDS[0])
            self._enforce_visibility(estimated_birthdate)
            return self.data

        # Si alguien edita la edad manualmente, regenerar fecha (si es YMD válido)
        if name == AGE_FIELD:
            age = form.get(AGE_FIELD)
            if dtime.is_ymd(age):
                self.update_birthdate_field_from_age(age)
            # Mantener visibilidad conforme a "estimada"
            estimated_birthdate = form.get(ESTIMATED_BIRTHDATE_FIELDS[1])
            if estimated_birthdate is None:
                estimated_birthdate = form.get(ESTIMATED_BIRTHDATE_FIELDS[0])
            self._enforce_visibility(estimated_birthdate)
            return self.data

        # Fallback: si no es ninguno de los anteriores, intenta recalcular si hay fecha
        self._recalc_if_possible(form)
        return self.data

    # ----------------------
    # API original + ajustes
    # ----------------------
    def update_age_field_from_birthdate(self, birthdate):
        # dtime.get_ymd devuelve la edad en formato 'Yy Mm Dd' (dependiendo de locale)
        age = dtime.get_ymd(birthdate)
        age = safe_unicode(age)  # asegurar unicode para evitar UnicodeDecodeError en Py2
        self.add_update_field(AGE_FIELD, age)

    def update_birthdate_field_from_age(self, age):
        birthdate = dtime.get_since_date(age)
        birthdate_str = dtime.date_to_string(birthdate)
        for field in BIRTHDATE_FIELDS:
            self.add_update_field(field, birthdate_str)

    def toggle_and_update_fields(self, form, estimated_birthdate):
        """Toggle age and birthdate fields that depend on estimated_birthdate"""
        is_estimated = estimated_birthdate in TRUTHY
        if is_estimated:
            # Con fecha estimada: mostrar AGE, ocultar DATE
            birthdate = form.get(BIRTHDATE_FIELDS[0]) or form.get(BIRTHDATE_FIELDS[1])
            if birthdate:
                self.update_age_field_from_birthdate(birthdate)
            self.add_show_field(AGE_FIELD)
            self.add_hide_field(BIRTHDATE_FIELDS[0])
        else:
            # Con fecha exacta: mostrar DATE, ocultar AGE
            age = form.get(AGE_FIELD)
            if dtime.is_ymd(age):
                self.update_birthdate_field_from_age(age)
            self.add_show_field(BIRTHDATE_FIELDS[0])
            self.add_hide_field(AGE_FIELD)

    # ----------------------
    # Helpers internos
    # ----------------------
    def _is_birthdate_field(self, name):
        if not name:
            return False
        if name in BIRTHDATE_FIELDS:
            return True
        # subcampos típicos del datepicker: -year, -month, -day, etc.
        return name.startswith("form.widgets.birthdate-")

    def _recalc_if_possible(self, form):
        bd = form.get(BIRTHDATE_FIELDS[0]) or form.get(BIRTHDATE_FIELDS[1])
        if bd:
            self.update_age_field_from_birthdate(bd)

    def _enforce_visibility(self, estimated_birthdate):
        """Aplica únicamente la visibilidad ya acordada, sin recalcular nada."""
        is_estimated = estimated_birthdate in TRUTHY
        if is_estimated:
            self.add_show_field(AGE_FIELD)
            self.add_hide_field(BIRTHDATE_FIELDS[0])
        else:
            self.add_show_field(BIRTHDATE_FIELDS[0])
            self.add_hide_field(AGE_FIELD)
