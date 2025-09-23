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

# ==========================================================
# COMPLEMENTO PARA AR: SOLO EDAD (NO TOCA FECHA DE NACIMIENTO)
# Pegar desde aquí hasta el final del archivo
# ==========================================================

# Import local por si no existe arriba (no pasa nada si ya está importado)
try:
    from bika.lims import api  # resolver paciente por UID
except Exception:
    api = None  # si tu entorno no tiene bika.lims aquí, ajusta el import según tu stack

# Candidatos de widgets en AR (ajusta si tus IDs difieren)
_AR_PATIENT_WIDGETS = [
    "form.widgets.Patient",
    "form.widgets.patient",
]

_AR_SAMPLING_DATE_WIDGETS = [
    "form.widgets.SamplingDate",
    "form.widgets.SamplingDate-date",
    "form.widgets.DateSampled",
    "form.widgets.DateSampled-date",
]

# Campo de edad unificado (si existe en tu AR)
_AR_AGE_SINGLE_WIDGETS = [
    "form.widgets.patient_age",
    "form.widgets.Age",
]

# Campos de edad separados (Years / Months / Days)
_AR_AGE_Y_WIDGETS = [
    "form.widgets.patient_age_years",
    "form.widgets.PatientAgeYears",
]
_AR_AGE_M_WIDGETS = [
    "form.widgets.patient_age_months",
    "form.widgets.PatientAgeMonths",
]
_AR_AGE_D_WIDGETS = [
    "form.widgets.patient_age_days",
    "form.widgets.PatientAgeDays",
]


def _first_present_key(form, keys):
    """Devuelve la primera clave presente en el dict form de una lista de candidatos."""
    for k in keys:
        if k in form and form.get(k) not in (None, u"", ""):
            return k
    return None


def _get_value_by_any(form, keys):
    """Devuelve el primer valor no vacío de las claves candidatas."""
    k = _first_present_key(form, keys)
    return form.get(k) if k else None


def _split_age_YMD(age_txt):
    """Convierte '20Y 2M 14D', '67Y', '8M 2D' en (Y, M, D) como enteros o None."""
    if not age_txt:
        return (None, None, None)
    txt = safe_unicode(age_txt).upper().strip()
    parts = [p for p in txt.replace(u"  ", u" ").split(u" ") if p]
    y = m = d = None
    for token in parts:
        if token.endswith(u"Y"):
            try:
                y = int(token[:-1])
            except Exception:
                pass
        elif token.endswith(u"M"):
            try:
                m = int(token[:-1])
            except Exception:
                pass
        elif token.endswith(u"D"):
            try:
                d = int(token[:-1])
            except Exception:
                pass
    return (y, m, d)


def _get_sampling_date_from_form(form):
    """Intenta leer la fecha de muestreo del form, o reconstruir de year/month/day."""
    sd = _get_value_by_any(form, _AR_SAMPLING_DATE_WIDGETS)
    if sd:
        try:
            if dtime.is_dt(sd):
                return sd.date()
            if isinstance(sd, basestring):
                return dtime.to_DT(sd).date()
            return sd  # date ya es válido
        except Exception:
            pass

    # Reconstrucción por partes
    for base in ("form.widgets.SamplingDate", "form.widgets.DateSampled"):
        y = form.get(base + "-year")
        m = form.get(base + "-month")
        d = form.get(base + "-day")
        if y and m and d:
            try:
                return dtime.datetime(int(str(y)), int(str(m)), int(str(d))).date()
            except Exception:
                return None
    return None


def _get_patient_obj_from_form(form):
    """Obtiene el objeto Paciente desde un RelationChoice del form (valor suele ser UID)."""
    uid = _get_value_by_any(form, _AR_PATIENT_WIDGETS)
    if not uid:
        return None

    # Algunas configuraciones envían el objeto directamente
    if getattr(uid, "portal_type", "").lower().endswith("patient"):
        return uid

    # Intentar por UID (requiere api)
    if api is not None:
        try:
            obj = api.get_object_by_uid(uid)
            if obj and getattr(obj, "portal_type", "").lower().endswith("patient"):
                return obj
        except Exception:
            return None
    return None


class AnalysisRequestEditForm(EditFormAdapterBase):
    """Adapter para AR: SOLO EDAD. No escribe fecha de nacimiento."""

    def initialized(self, data):
        form = data.get("form", {})
        self._refresh_age_only(form)
        return self.data

    def added(self, data):
        form = data.get("form", {})
        self._refresh_age_only(form)
        return self.data

    def modified(self, data):
        name = data.get("name")
        form = data.get("form", {})

        # Cambio de Paciente
        if name in _AR_PATIENT_WIDGETS:
            self._refresh_age_only(form)
            return self.data

        # Cambio en fecha de muestreo (campo completo o subcampos -year/-month/-day)
        if (name in _AR_SAMPLING_DATE_WIDGETS) or (name or "").startswith("form.widgets.SamplingDate-") \
           or (name or "").startswith("form.widgets.DateSampled-"):
            self._refresh_age_only(form)
            return self.data

        # Fallback
        self._refresh_age_only(form)
        return self.data

    # ------------------
    # Lógica: solo Edad
    # ------------------
    def _refresh_age_only(self, form):
        patient = _get_patient_obj_from_form(form)
        if not patient:
            return

        # Fecha de referencia: SamplingDate si existe; si no, hoy
        ref_date = _get_sampling_date_from_form(form) or dtime.today().date()

        # Edad del paciente a la fecha de referencia
        # (usa la API del objeto Patient; devuelve algo como "20Y 2M 14D" o "67Y")
        try:
            age_txt = patient.getAgeAt(ref_date=ref_date)
        except Exception:
            # fallback defensivo
            dob = getattr(patient, "getBirthdate", lambda *a, **k: None)()
            age_txt = dtime.get_ymd(dob, ref_date=ref_date) if dob else u""

        # Escribir en campo unificado si existe
        age_single_key = _first_present_key(form, _AR_AGE_SINGLE_WIDGETS)
        if age_single_key:
            self.add_update_field(age_single_key, age_txt or u"")

        # Escribir en campos separados si existen
        y, m, d = _split_age_YMD(age_txt)
        y_key = _first_present_key(form, _AR_AGE_Y_WIDGETS)
        m_key = _first_present_key(form, _AR_AGE_M_WIDGETS)
        d_key = _first_present_key(form, _AR_AGE_D_WIDGETS)
        if y_key:
            self.add_update_field(y_key, u"" if y is None else y)
        if m_key:
            self.add_update_field(m_key, u"" if m is None else m)
        if d_key:
            self.add_update_field(d_key, u"" if d is None else d)

