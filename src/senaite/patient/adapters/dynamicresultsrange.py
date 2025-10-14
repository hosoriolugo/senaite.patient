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

from datetime import datetime, date

from bika.lims.adapters.dynamicresultsrange import DynamicResultsRange
from bika.lims.interfaces import IDynamicResultsRange
from senaite.core.api import dtime
from senaite.patient.api import get_birth_date  # se mantiene por compatibilidad, aunque ahora calculamos edad directa
from zope.interface import implementer
from plone.memoize.instance import memoize

# ADDED: logging (silencioso si no está disponible en algunos builds)
try:
    from bika.lims import logger
except Exception:
    import logging
    logger = logging.getLogger("senaite.patient.dynamicresultsrange")

# Compat Py2
try:
    basestring
except NameError:
    basestring = str
try:
    unicode
except NameError:
    unicode = str

# Compat Zope DateTime (opcional)
try:
    import DateTime as ZDT  # Zope DateTime module
except Exception:
    ZDT = None


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
    """Mapea valores comunes a 'm' o 'f' (y 'u' como comodín)."""
    v = _norm(s)
    if v in (u"m", u"male", u"masculino", u"hombre", u"varón", u"varon"):
        return u"m"
    if v in (u"f", u"female", u"femenino", u"mujer", u"hembra"):
        return u"f"
    if v in (u"u", u"unk", u"unknown", u"desconocido", u"na", u"n/a", u"all", u"todos", u"todas"):
        return u"u"
    return v


def _to_int_or_none(v):
    if v in (None, u"", ""):
        return None
    try:
        # ya entero
        if isinstance(v, int):
            return int(v)
        # Py2 long
        try:
            long  # noqa
            if isinstance(v, long):  # type: ignore  # noqa
                return int(v)
        except Exception:
            pass
        # string
        if isinstance(v, basestring):
            v = v.strip()
            if v == "":
                return None
            v = v.replace(",", ".")
            return int(float(v))
        # otro numérico
        return int(v)
    except Exception:
        return None


def _to_date(obj):
    """Convierte Zope DateTime / datetime / date a date (naive)."""
    try:
        if ZDT is not None:
            try:
                if isinstance(obj, ZDT.DateTime):
                    return obj.asdatetime().date()
            except Exception:
                try:
                    if hasattr(obj, "asdatetime"):
                        return obj.asdatetime().date()
                except Exception:
                    pass
        if isinstance(obj, datetime):
            return obj.date()
        if isinstance(obj, date):
            return obj
    except Exception:
        pass
    return None


@implementer(IDynamicResultsRange)
class PatientDynamicResultsRange(DynamicResultsRange):
    """Dynamic Results Range Adapter con soporte de edad y sexo:

    - MinAge/MaxAge  o  age_min_days/age_max_days: edad mínima/máxima (en días, inclusivo)
    - Sex  o  gender: 'f'/'m' (acepta variantes). 'U' solo aplica si el sexo del paciente es desconocido.
    """

    # ---------- Helpers para obtener datos del AR/Paciente (seguros) ----------

    @property
    @memoize
    def patient(self):
        try:
            getPatient = getattr(self.analysisrequest, "getPatient", None)
            return getPatient() if callable(getPatient) else None
        except Exception:
            return None

    @property
    @memoize
    def sampled_date(self):
        """Fecha de muestreo como date (naive)."""
        try:
            sampled = getattr(self.analysisrequest, "getDateSampled", lambda: None)()
            # algunos builds devuelven None: usar DateReceived o CreationDate como último recurso
            if not sampled:
                sampled = getattr(self.analysisrequest, "getDateReceived", lambda: None)() or getattr(
                    self.analysisrequest, "created", None
                )
            return _to_date(sampled)
        except Exception:
            return None

    @property
    @memoize
    def dob_date(self):
        """DOB del paciente como date (naive). Busca en AR y en el objeto Paciente."""
        # 1) AR field helpers
        try:
            dob_field = self.analysisrequest.getField("DateOfBirth")
            dob = dob_field.get_date_of_birth(self.analysisrequest)
            d = _to_date(dob)
            if d:
                return d
        except Exception:
            pass
        try:
            dob = getattr(self.analysisrequest, "getDateOfBirth", lambda: None)()
            d = _to_date(dob)
            if d:
                return d
        except Exception:
            pass

        # 2) Objeto Patient
        p = self.patient
        if p:
            for getter_name in ("getDateOfBirth", "getBirthDate", "DateOfBirth", "BirthDate"):
                try:
                    val = getattr(p, getter_name, None)
                    if callable(val):
                        val = val()
                    d = _to_date(val)
                    if d:
                        return d
                except Exception:
                    continue

        # 3) Nada encontrado
        return None

    @property
    @memoize
    def ansi_dob(self):
        """DOB en ANSI (para compatibilidad con superclases, por si acaso)."""
        d = self.dob_date
        return dtime.to_ansi(d) if d else None

    @property
    @memoize
    def patient_gender(self):
        """Género del paciente asociado al AR, normalizado."""
        gender = None
        # AR primero
        try:
            getter = None
            for name in ("getGender", "getSex", "Gender", "Sex"):
                if hasattr(self.analysisrequest, name):
                    getter = getattr(self.analysisrequest, name)
                    gender = getter() if callable(getter) else getter
                    if gender not in (None, u""):
                        break
        except Exception:
            gender = None

        # Paciente si no estaba en AR
        if gender in (None, u""):
            try:
                p = self.patient
                if p:
                    getter = None
                    for name in ("getGender", "getSex", "Gender", "Sex"):
                        if hasattr(p, name):
                            getter = getattr(p, name)
                            gender = getter() if callable(getter) else getter
                            if gender not in (None, u""):
                                break
            except Exception:
                gender = None

        return _norm_sex(gender)

    @property
    @memoize
    def patient_flags(self):
        """Opcional: banderas comunes si existen (no rompen si faltan)."""
        flags = {"is_fasting": None, "is_pregnant": None}
        try:
            p = self.patient
            if p:
                if hasattr(p, "getIsFasting"):
                    flags["is_fasting"] = bool(p.getIsFasting())
                if hasattr(p, "getIsPregnant"):
                    flags["is_pregnant"] = bool(p.getIsPregnant())
        except Exception:
            pass
        return flags

    @property
    @memoize
    def patient_weight(self):
        """Opcional: peso del paciente si existe (float) o None."""
        try:
            p = self.patient
            if not p:
                return None
            if hasattr(p, "getWeight"):
                val = p.getWeight()
            else:
                val = getattr(p, "Weight", None)
            if val in (None, u"", ""):
                return None
            if isinstance(val, basestring):
                val = val.replace(",", ".")
            return float(val)
        except Exception:
            return None

    # ------------------------------- MATCH ------------------------------------

    def match(self, dynamic_range):
        """Decide si la fila dinámica aplica al contexto actual.

        1) Lógica base de core (servicio, método, sample type, etc.)
        2) Filtros por edad (MinAge/MaxAge o age_min_days/age_max_days) — inclusivo
        3) Filtro por sexo (Sex o gender): si paciente es M/F, NO usar filas 'U'
        """
        # 1) Lógica base
        is_match = super(PatientDynamicResultsRange, self).match(dynamic_range)
        if not is_match:
            return False

        # ---------------------- 2) EDAD --------------------------------------
        # Soporta ambos esquemas de columnas:
        #   - Clásico: MinAge / MaxAge (en días)
        #   - Excel DX: age_min_days / age_max_days (en días)
        min_age = dynamic_range.get("MinAge")
        max_age = dynamic_range.get("MaxAge")
        if min_age in (None, u"", "") and max_age in (None, u"", ""):
            min_age = dynamic_range.get("age_min_days")
            max_age = dynamic_range.get("age_max_days")

        min_age = _to_int_or_none(min_age)
        max_age = _to_int_or_none(max_age)

        if min_age is not None or max_age is not None:
            # cuando la fila trae límites de edad, NECESITAMOS DOB + sampled
            dob_d = self.dob_date
            smp_d = self.sampled_date
            if not dob_d or not smp_d:
                return False

            # edad en días (inclusivo)
            age_days = (smp_d - dob_d).days
            if age_days < 0:
                # DOB futuro improbable → no aplica
                return False

            if min_age is not None and age_days < min_age:
                return False
            if max_age is not None and age_days > max_age:
                return False

        # ---------------------- 3) SEXO --------------------------------------
        # Acepta 'Sex' (clásico) o 'gender' (DX/Excel, en minúsculas).
        sex_required = dynamic_range.get("Sex")
        if sex_required in (None, u"", ""):
            sex_required = dynamic_range.get("gender")

        if sex_required not in (None, u"", ""):
            required = _norm_sex(sex_required)
            actual = self.patient_gender

            # PRIORIDAD ESPECÍFICA:
            # - Si el paciente tiene M/F, NO debemos aceptar una fila 'U' (unknown/all).
            if required in (u"u", u"unknown"):
                if actual in (u"m", u"f"):
                    return False
                # si el sexo del paciente es desconocido, 'U' sí aplica
                return True

            # - Si la fila exige m/f, debe coincidir exactamente
            if required in (u"m", u"f"):
                if actual not in (u"m", u"f") or actual != required:
                    return False
            else:
                # otro texto libre (por compatibilidad): comparar normalizado
                if _norm(actual) != _norm(required):
                    return False

        # ---------- (OPCIONALES) Si añades estas columnas, descomenta ----------
        # Fasting (True/False)
        # fasting_required = dynamic_range.get("Fasting")
        # if fasting_required not in (None, u"", ""):
        #     val = self.patient_flags.get("is_fasting")
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
        #         pass

        return True
