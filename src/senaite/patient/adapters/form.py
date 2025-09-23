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


def _to_ascii_age(age_text):
    """Normaliza cualquier formato de edad (localizado) a ASCII: '12y 3m 4d'.

    Evita problemas de indexación en Py2 con cadenas UTF-8 ('años', 'días', etc.).
    """
    txt = safe_unicode(age_text).lower()

    # Normalizaciones comunes de locales
    # español
    txt = txt.replace(u"a\u00F1os", u"y")      # años -> y
    txt = txt.replace(u"años", u"y")
    txt = txt.replace(u"meses", u"m")
    txt = txt.replace(u"mes", u"m")
    txt = txt.replace(u"d\u00EDas", u"d")     # días -> d
    txt = txt.replace(u"dias", u"d")
    # portugués / italiano / francés abreviado (por si acaso)
    txt = txt.replace(u"anos", u"y")
    txt = txt.replace(u"anni", u"y")
    txt = txt.replace(u"ans", u"y")
    txt = txt.replace(u"giorni", u"d")
    txt = txt.replace(u"jours", u"d")

    # Abreviaturas potencialmente con espacios o puntos
    # (dejamos y/m/d como objetivo)
    replacements = {
        u" a ": u" y ",
        u" m ": u" m ",
        u" d ": u" d ",
        u" y ": u" y ",
        u" y": u" y",
        u" m": u" m",
        u" d": u" d",
        u"years": u"y",
        u"year": u"y",
        u"months": u"m",
        u"month": u"m",
        u"days": u"d",
        u"day": u"d",
    }
    for k, v in replacements.items():
        txt = txt.replace(k, v)

    # Dejar solo dígitos, espacios y y/m/d
    allowed = u"0123456789 ymd"
    txt = u"".join([c for c in txt if c in allowed])

    # Compactar espacios múltiples
    txt = u" ".join(txt.split())

    # Patrones típicos que podrían quedar: "12y 3m 4d", "12y", "8m 2d"...
    return txt


class PatientEditForm(EditFormAdapterBase):
    """Edit form for Patient content type

    Reglas:
    - La Edad SIEMPRE se calcula desde la Fecha de Nacimiento (estimada o exacta).
    - En cuanto hay fecha -> calcular y mostrar Edad automáticamente.
    - 'Edad' se trata como solo-lectura lógica: se ignoran ediciones manuales.
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
        self._enforce_visibility(estimated_birthdate, form)
        return self.data

    def added(self, data):
        """Algunos widgets disparan 'added' al cerrar el datepicker o setear partes."""
        form = data.get("form")
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

        # Fallback
        self._recalc_if_possible(form)
        return self.data

    # ----------------------
    # Cálculo y seteo de Edad
    # ----------------------
    def update_age_field_from_birthdate(self, birthdate):
        """Calcula la Edad desde la fecha y la deja en ASCII seguro ('12y 3m 4d')."""
        raw_age = dtime.get_ymd(birthdate)
        safe_age = _to_ascii_age(raw_age)  # <- clave para evitar UnicodeDecodeError
        self.add_update_field(AGE_FIELD, safe_age)

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
        """Con fecha -> ambos visibles; sin fecha -> Edad solo si 'estimada'."""
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
