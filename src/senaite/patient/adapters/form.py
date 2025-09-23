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

from datetime import date, datetime as pydatetime
from senaite.core.api import dtime
from senaite.core.browser.form.adapters import EditFormAdapterBase


ESTIMATED_BIRTHDATE_FIELDS = (
    "form.widgets.estimated_birthdate",
    "form.widgets.estimated_birthdate:list",
)
AGE_FIELD = "form.widgets.age"
BIRTHDATE_FIELDS = (
    "form.widgets.birthdate",
    "form.widgets.birthdate-date",
)

TRUTHY = set([True, "selected", "on", "true", "1", u"on", u"true", u"1"])


class PatientEditForm(EditFormAdapterBase):
    """Edit form for Patient content type

    Lógica:
    - Siempre se muestran Birthdate y Age.
    - Age se calcula desde Birthdate (formato YMD nativo de SENAITE: '45y 3m 20d', etc.).
    - El flag 'estimated_birthdate' solo afecta la UI (aviso) pero NO el cálculo.
    """

    # ----------------------
    # Hooks AJAX
    # ----------------------
    def initialized(self, data):
        form = data.get("form") or {}
        estimated = form.get(ESTIMATED_BIRTHDATE_FIELDS[1])
        if estimated is None:
            estimated = form.get(ESTIMATED_BIRTHDATE_FIELDS[0])
        self._sync_fields(form, estimated)
        self._recalc_if_possible(form)
        return self.data

    def added(self, data):
        """Algunos widgets disparan 'added' al inicializar/cambiar internamente.
        Recalculamos aquí también para no depender solo de 'modified'."""
        form = data.get("form") or {}
        estimated = form.get(ESTIMATED_BIRTHDATE_FIELDS[1])
        if estimated is None:
            estimated = form.get(ESTIMATED_BIRTHDATE_FIELDS[0])
        self._sync_fields(form, estimated)
        self._recalc_if_possible(form)
        return self.data

    def modified(self, data):
        name = (data.get("name") or "").strip()
        form = data.get("form") or {}
        value = data.get("value")

        # Cambio en el checkbox de estimado -> sincroniza y (si hay fecha) recalcula
        if name in ESTIMATED_BIRTHDATE_FIELDS:
            self._sync_fields(form, value)
            self._recalc_if_possible(form)
            return self.data

        # Cambios relacionados con DoB (incluye subcampos -year/-month/-day)
        if self._is_birthdate_field(name):
            birthdate = self._get_birthdate_from_form(form, name=name, value=value)
            if birthdate:
                self._update_age_from_birthdate(birthdate)
            # si aún no está completa la fecha, no toques AGE (evita parpadeos)
            self._show_all()
            return self.data

        # Si el usuario intenta editar Age manualmente, lo ignoramos y recalc al vuelo
        if name == AGE_FIELD:
            birthdate = self._get_birthdate_from_form(form)
            if birthdate:
                self._update_age_from_birthdate(birthdate)
            self._show_all()
            return self.data

        # Fallback: aunque 'name' no sea de DoB, si ya hay fecha -> recalcular
        self._recalc_if_possible(form)
        return self.data

    # ----------------------
    # Helpers de UI
    # ----------------------
    def _show_all(self):
        self.add_show_field(AGE_FIELD)
        self.add_show_field(BIRTHDATE_FIELDS[0])

    def _sync_fields(self, form, estimated_flag):
        """Sincroniza visibilidad/valores (no oculta campos)."""
        self._show_all()
        birthdate = self._get_birthdate_from_form(form)
        if birthdate:
            self._update_age_from_birthdate(birthdate)
        # 'estimated_flag' solo lo usa la plantilla para mostrar aviso.

    # ----------------------
    # Helpers de fecha/edad
    # ----------------------
    def _is_birthdate_field(self, name):
        """True si 'name' corresponde a cualquier variante del widget de fecha."""
        if not name:
            return False
        if name in BIRTHDATE_FIELDS:
            return True
        # capta subcampos como birthdate-year/month/day/hour/minute, etc.
        return name.startswith("form.widgets.birthdate-")

    def _pad2(self, v):
        if v is None:
            return None
        s = unicode(v).strip()
        if not s:
            return None
        try:
            return "%02d" % int(s)
        except Exception:
            return None

    def _coerce_date(self, y, m, d):
        try:
            y = int(unicode(y))
            m = int(unicode(m))
            d = int(unicode(d))
            return date(y, m, d)
        except Exception:
            return None

    def _parse_date_string(self, val):
        """Acepta 'YYYY-MM-DD' y 'DD/MM/YYYY'."""
        if not val:
            return None
        s = unicode(val).strip()
        if "-" in s:
            parts = s.split("-")
            if len(parts) == 3:
                y, m, d = parts
                return self._coerce_date(y, m, d)
        if "/" in s:
            parts = s.split("/")
            if len(parts) == 3:
                d, m, y = parts
                return self._coerce_date(y, m, d)
        return None

    def _coerce_to_date(self, obj):
        """Convierte distintos tipos a 'date' si es posible (compatible Py2)."""
        # date nativa
        if isinstance(obj, date) and not isinstance(obj, pydatetime):
            return obj
        # datetime nativa
        if isinstance(obj, pydatetime):
            try:
                return obj.date()
            except Exception:
                pass
        # strings comunes del widget
        if isinstance(obj, basestring):
            parsed = self._parse_date_string(obj)
            if parsed:
                return parsed
        # objetos con atributos year/month/day
        has_attrs = True
        for attr in ("year", "month", "day"):
            if not hasattr(obj, attr):
                has_attrs = False
                break
        if has_attrs:
            try:
                return date(int(obj.year), int(obj.month), int(obj.day))
            except Exception:
                pass
        return None

    def _get_birthdate_from_form(self, form, name=None, value=None):
        """Obtiene la fecha desde las claves estándar o la reconstruye de year/month/day.

        - Si 'name' es el campo completo de fecha, intenta parsear 'value'.
        - Si 'name' es uno de los subcampos, sobreescribe esa parte con 'value'
          y reconstruye cuando las 3 partes están presentes.
        """
        # 1) si el evento es sobre el campo completo y 'value' trae la fecha
        if name in BIRTHDATE_FIELDS:
            bd = self._coerce_to_date(value)
            if bd:
                return bd
            # si value no fue parseable, intenta con el form (quizá el widget ya lo guardó)
            bd_form = form.get(BIRTHDATE_FIELDS[0]) or form.get(BIRTHDATE_FIELDS[1])
            bd = self._coerce_to_date(bd_form)
            if bd:
                return bd

        # 2) claves directas que ya existan en form
        bd = form.get(BIRTHDATE_FIELDS[0]) or form.get(BIRTHDATE_FIELDS[1])
        bd = self._coerce_to_date(bd)
        if bd:
            return bd

        # 3) reconstrucción desde partes year/month/day
        y = form.get("form.widgets.birthdate-year")
        m = form.get("form.widgets.birthdate-month")
        d = form.get("form.widgets.birthdate-day")

        # override inmediato con la parte que está llegando en este evento
        if name == "form.widgets.birthdate-year":
            y = value
        elif name == "form.widgets.birthdate-month":
            m = value
        elif name == "form.widgets.birthdate-day":
            d = value

        y = (unicode(y).strip() if y is not None and unicode(y).strip() != "" else None)
        m = self._pad2(m)
        d = self._pad2(d)

        if y and m and d:
            return self._coerce_date(y, m, d)

        return None

    def _update_age_from_birthdate(self, birthdate):
        """Calcula edad en formato YMD nativo y actualiza el widget AGE."""
        if not birthdate:
            return
        age = dtime.get_ymd(birthdate)
        self.add_update_field(AGE_FIELD, age)

    def _recalc_if_possible(self, form):
        """Recalcula la edad si hay fecha disponible, aunque el evento no sea de DoB."""
        bd = self._get_birthdate_from_form(form)
        if bd:
            self._update_age_from_birthdate(bd)
            self._show_all()
