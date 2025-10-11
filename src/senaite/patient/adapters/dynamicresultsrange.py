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
from senaite.patient.api import get_birth_date
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
    return v  # deja pasar otros valores por compatibilidad


@implementer(IDynamicResultsRange)
class PatientDynamicResultsRange(DynamicResultsRange):
    """Dynamic Results Range Adapter que añade soporte a variables del paciente:

    - MinAge/MaxAge: edad mínima/máxima (formato ymd) para que aplique la fila
    - Sex: 'f' (female), 'm' (male) — soporta variantes ('female', 'femenino', etc.)

    Nota: si una clave no está presente en la fila dinámica, no filtra por ella.
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
        # Intentos comunes: método getGender en el AR o en el objeto paciente
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

        1) Usa la lógica base de core (servicio, método, sample type, etc.)
        2) Aplica filtros por edad (MinAge/MaxAge) y sexo (Sex)
        3) (Opcional) Puedes habilitar más filtros abajo si añades columnas
        """
        # 1) Lógica base: si falla ya no seguimos
        is_match = super(PatientDynamicResultsRange, self).match(dynamic_range)
        if not is_match:
            return False

        # 2) Edad (usa tus utilidades existentes)
        min_age = dynamic_range.get("MinAge")
        max_age = dynamic_range.get("MaxAge")

        if not self.ansi_dob:
            # Sin DOB: si la fila exige edades, no aplica; si no, sí aplica
            if any([min_age, max_age]):
                return False
        else:
            # Fecha de muestreo
            sampled = self.analysisrequest.getDateSampled()
            # Umbral superior de edad => DOB mínima (paciente más joven permitido)
            max_dob = get_birth_date(min_age, sampled, default=datetime.max)
            # Umbral inferior de edad => DOB máxima (paciente más viejo permitido)
            min_dob = get_birth_date(max_age, sampled, default=datetime.min)

            dob_ansi = self.ansi_dob
            if dob_ansi <= dtime.to_ansi(min_dob):
                # paciente es MAYOR de lo permitido (nació antes o igual a min_dob)
                return False
            if dob_ansi > dtime.to_ansi(max_dob):
                # paciente es MÁS JOVEN de lo permitido
                return False

        # 3) Sexo (nuevo): si la fila trae 'Sex', debe coincidir
        sex_required = dynamic_range.get("Sex")
        if sex_required not in (None, u"", ""):
            required = _norm_sex(sex_required)
            actual = self.patient_gender  # ya normalizado
            if required in (u"m", u"f"):
                if actual not in (u"m", u"f"):
                    # Paciente sin género definido pero la fila lo exige
                    return False
                if actual != required:
                    return False
            else:
                # Si especifican un valor no estándar, comparamos normalizado
                if _norm(actual) != _norm(required):
                    return False

        # ---------- (OPCIONALES) Habilita si agregas estas columnas ----------
        # Fasting (True/False)
        # fasting_required = dynamic_range.get("Fasting")
        # if fasting_required not in (None, u"", ""):
        #     val = self.patient_flags.get("is_fasting")
        #     # Acepta 'true/false', '1/0', 'sí/no'
        #     req = _norm(fasting_required)
        #     req_bool = req in (u"true", u"1", u"si", u"sí", u"yes")
        #     if val is None or bool(val) != req_bool:
        #         return False
        #
        # Pregnant (True/False)
        # pregnant_required = dynamic_range.get("Pregnant")
        # if pregnant_required not in (None, u"", ""):
        #     val = self.patient_flags.get("is_pregnant")
        #     req = _norm(pregnant_required)
        #     req_bool = req in (u"true", u"1", u"si", u"sí", u"yes")
        #     if val is None or bool(val) != req_bool:
        #         return False
        #
        # WeightMin / WeightMax (kg)
        # wmin = dynamic_range.get("WeightMin")
        # wmax = dynamic_range.get("WeightMax")
        # if wmin not in (None, u"", "") or wmax not in (None, u"", ""):
        #     w = self.patient_weight
        #     if w is None:
        #         return False
        #     try:
        #         if wmin not in (None, u"", "") and w < float(unicode(wmin).replace(",", ".")):
        #             return False
        #         if wmax not in (None, u"", "") and w > float(unicode(wmax).replace(",", ".")):
        #             return False
        #     except Exception:
        #         # Si los límites son inválidos, no bloqueamos
        #         pass

        # Si pasa todos los filtros, la fila dinámica aplica
        return True
