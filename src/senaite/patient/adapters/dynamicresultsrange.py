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

from datetime import datetime

from bika.lims.adapters.dynamicresultsrange import DynamicResultsRange
from bika.lims.interfaces import IDynamicResultsRange
from senaite.core.api import dtime
from senaite.patient.api import get_birth_date  # (se mantiene por compat.)
from zope.interface import implementer
from plone.memoize.instance import memoize

# ADDED: logging (silencioso si no está disponible en algunos builds)
try:
    from bika.lims import logger
except Exception:
    import logging
    logger = logging.getLogger("senaite.patient.dynamicresultsrange")


def _norm(s):
    try:
        if s is None:
            return u""
        if isinstance(s, bytes):
            s = s.decode("utf-8", "ignore")
        return u" ".join(unicode(s).strip().lower().split())
    except Exception:
        try:
            return u"%s" % s
        except Exception:
            return u""


def _norm_sex(s):
    """Mapea valores comunes a 'm' o 'f' para comparación robusta."""
    v = _norm(s)
    if v in (u"m", u"male", u"masculino", u"hombre"):
        return u"m"
    if v in (u"f", u"female", u"femenino", u"mujer"):
        return u"f"
    # 'u', 'unknown', '', etc. serán tratados como comodín (fallback)
    if v in (u"u", u"unk", u"unknown", u"desconocido"):
        return u"u"
    return v  # deja pasar otros valores por compatibilidad


def _to_int_or_none(v):
    if v in (None, u"", ""):
        return None
    try:
        if isinstance(v, (int, long)):
            return int(v)
        if isinstance(v, basestring):
            v = v.strip()
            if v == "":
                return None
            return int(float(v.replace(",", ".")))
        return int(v)
    except Exception:
        return None


# NUEVO: util directo para edad en días (evita errores off-by-one con DOB)
def _age_in_days(dob, sampled):
    """Devuelve edad en días al momento de muestreo (int), o None si falta info."""
    try:
        if not dob or not sampled:
            return None
        dob_d = dtime.to_date(dob)
        smp_d = dtime.to_date(sampled)
        return (smp_d - dob_d).days
    except Exception:
        return None


@implementer(IDynamicResultsRange)
class PatientDynamicResultsRange(DynamicResultsRange):
    """Dynamic Results Range Adapter que añade soporte a variables del paciente:

    - MinAge/MaxAge  o  age_min_days/age_max_days: edad mínima/máxima (en días)
    - Sex  o  gender: 'f'/'m' (acepta variantes: 'female', 'femenino', etc.)
    """

    # ---------- Helpers para obtener datos del AR/Paciente (seguros) ----------

    @property
    @memoize
    def ansi_dob(self):
        """Fecha de nacimiento en ANSI (evita TZ issues)."""
        dob = None
        try:
            dob_field = self.analysisrequest.getField("DateOfBirth")
            dob = dob_field.get_date_of_birth(self.analysisrequest)
        except Exception:
            # Fallback: algunos builds exponen getDateOfBirth directamente
            try:
                dob = self.analysisrequest.getDateOfBirth()
            except Exception:
                dob = None
        return dtime.to_ansi(dob) if dob else None

    @property
    @memoize
    def patient_gender(self):
        """Obtiene el género del paciente asociado al AR, normalizado."""
        gender = None
        try:
            if hasattr(self.analysisrequest, "getGender"):
                gender = self.analysisrequest.getGender()
        except Exception:
            gender = None

        if gender in (None, u""):
            try:
                getPatient = getattr(self.analysisrequest, "getPatient", None)
                patient = getPatient() if getPatient else None
                if patient and hasattr(patient, "getGender"):
                    gender = patient.getGender()
            except Exception:
                gender = None

        return _norm_sex(gender)

    @property
    @memoize
    def patient_flags(self):
        """Opcional: banderas comunes si existen (no rompen si faltan)."""
        flags = {"is_fasting": None, "is_pregnant": None}
        try:
            getPatient = getattr(self.analysisrequest, "getPatient", None)
            patient = getPatient() if getPatient else None
            if patient:
                if hasattr(patient, "getIsFasting"):
                    flags["is_fasting"] = bool(patient.getIsFasting())
                if hasattr(patient, "getIsPregnant"):
                    flags["is_pregnant"] = bool(patient.getIsPregnant())
        except Exception:
            pass
        return flags

    @property
    @memoize
    def patient_weight(self):
        """Opcional: peso del paciente si existe (float) o None."""
        try:
            getPatient = getattr(self.analysisrequest, "getPatient", None)
            patient = getPatient() if getPatient else None
            if not patient:
                return None
            # Nombres típicos: Weight / getWeight
            if hasattr(patient, "getWeight"):
                val = patient.getWeight()
            else:
                val = getattr(patient, "Weight", None)
            if val in (None, u"", ""):
                return None
            # normaliza coma/punto
            if isinstance(val, basestring):
                val = val.replace(",", ".")
            return float(val)
        except Exception:
            return None

    # ------------------------------- MATCH ------------------------------------

    def match(self, dynamic_range):
        """Decide si la fila dinámica aplica al contexto actual.

        1) Lógica base de core (servicio, método, sample type, etc.)
        2) Filtros por edad (prioritario; min/max en días, inclusivo)
        3) Filtro por sexo (M/F prioriza sobre U/unknown)
        """
        # 1) Lógica base: si falla ya no seguimos
        if not super(PatientDynamicResultsRange, self).match(dynamic_range):
            return False

        # ---------------------- 2) EDAD (PRIORITARIA) ------------------------
        # Soporta ambos esquemas de columnas: MinAge/MaxAge o age_min_days/age_max_days
        min_age = dynamic_range.get("MinAge")
        max_age = dynamic_range.get("MaxAge")
        if min_age in (None, u"", "") and max_age in (None, u"", ""):
            min_age = dynamic_range.get("age_min_days")
            max_age = dynamic_range.get("age_max_days")

        min_age = _to_int_or_none(min_age)
        max_age = _to_int_or_none(max_age)

        # Edad del paciente en días al momento de muestreo
        sampled = getattr(self.analysisrequest, "getDateSampled", lambda: None)()

        # Recupera DOB real para el cálculo de edad (ansi_dob es string ANSI)
        dob = None
        try:
            dob_field = self.analysisrequest.getField("DateOfBirth")
            dob = dob_field.get_date_of_birth(self.analysisrequest)
        except Exception:
            try:
                dob = self.analysisrequest.getDateOfBirth()
            except Exception:
                dob = None

        age_days = _age_in_days(dob, sampled)

        # Si la fila define límites y no podemos calcular edad, no aplica
        if (min_age is not None or max_age is not None) and age_days is None:
            return False

        # Comparación inclusiva (min ≤ edad ≤ max)
        if age_days is not None:
            if min_age is not None and age_days < min_age:
                return False
            if max_age is not None and age_days > max_age:
                return False

        # ---------------------- 3) SEXO (PRIORITARIO) ------------------------
        # Acepta 'Sex' (clásico) o 'gender' (Excel)
        sex_required = dynamic_range.get("Sex") or dynamic_range.get("gender")
        if sex_required not in (None, u"", ""):
            required = _norm_sex(sex_required)
            actual = self.patient_gender  # ya normalizado

            # Si el paciente es M/F y la fila es U/unknown -> NO match
            if required in (u"u", u"unknown") and actual in (u"m", u"f"):
                return False

            # Si la fila exige M/F, debe coincidir exactamente
            if required in (u"m", u"f"):
                if actual not in (u"m", u"f") or actual != required:
                    return False
            else:
                # Valor no estándar: compara normalizado (excepto 'u' que es comodín)
                if required not in (u"u", u"unknown") and _norm(actual) != _norm(required):
                    return False

        # ---------- (OPCIONALES) Habilita si agregas estas columnas ----------
        # Fasting / Pregnant / WeightMin/WeightMax (idéntico a tu versión, omitido)

        # Si pasa todos los filtros, la fila dinámica aplica
        return True
