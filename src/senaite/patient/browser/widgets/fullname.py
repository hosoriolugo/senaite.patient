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

import six

from AccessControl import ClassSecurityInfo
from Products.Archetypes.Registry import registerWidget
from Products.Archetypes.Widget import TypesWidget


class FullnameWidget(TypesWidget):
    """Widget para nombre completo en partes (4 campos):
       firstname, middlename, lastname, maternal_lastname
    """
    security = ClassSecurityInfo()

    _properties = TypesWidget._properties.copy()
    _properties.update({
        "macro": "senaite_patient_widgets/fullnamewidget",
        "entry_mode": "parts",
        # IMPORTANTE: incluir la 4ª parte para vista/listados
        "view_format": "%(firstname)s %(middlename)s %(lastname)s %(maternal_lastname)s",
        "size": "15",
    })

    # --- Helpers -------------------------------------------------------------

    def _strip(self, v):
        return (v or "").strip()

    def _normalize_parts(self, data):
        """Devuelve SIEMPRE un dict con las 4 claves como strings."""
        return {
            "firstname": self._strip(data.get("firstname")),
            "middlename": self._strip(data.get("middlename")),
            "lastname": self._strip(data.get("lastname")),
            "maternal_lastname": self._strip(data.get("maternal_lastname")),
        }

    # --- Guardado desde el formulario ---------------------------------------

    def process_form(self, instance, field, form,
                     empty_marker=None, emptyReturnsMarker=False, validating=True):
        """Lee el valor del request y devuelve un dict normalizado con 4 claves."""
        name = field.getName()
        value = form.get(name)

        # Archetypes a veces envía listas
        if isinstance(value, (list, tuple)):
            value = value[0] if value else None

        parts = {
            "firstname": "",
            "middlename": "",
            "lastname": "",
            "maternal_lastname": "",
        }

        # Modo "texto plano": repartir tokens de forma razonable
        if isinstance(value, six.string_types):
            text = self._strip(value)
            if text:
                tokens = [t for t in text.split(" ") if t]
                if len(tokens) == 1:
                    parts["firstname"] = tokens[0]
                elif len(tokens) == 2:
                    parts["firstname"], parts["lastname"] = tokens
                elif len(tokens) >= 3:
                    parts["firstname"] = tokens[0]
                    parts["lastname"] = tokens[-1]
                    parts["middlename"] = " ".join(tokens[1:-1])
            # maternal_lastname queda vacío salvo que venga en dict

        # Modo "parts": viene un dict con subcampos
        elif isinstance(value, dict):
            parts["firstname"] = self._strip(value.get("firstname", ""))
            parts["middlename"] = self._strip(value.get("middlename", ""))
            parts["lastname"] = self._strip(value.get("lastname", ""))
            # CLAVE: antes se solía ignorar; aquí sí lo recogemos
            parts["maternal_lastname"] = self._strip(value.get("maternal_lastname", ""))

        # Permitir campos no requeridos: si no hay nada significativo, no guardar
        if not any([parts["firstname"], parts["lastname"], parts["maternal_lastname"]]):
            return None, {}

        return self._normalize_parts(parts), {}

    # --- Renderizado para vista/listados ------------------------------------

    security = ClassSecurityInfo()
    security.declarePublic("render_view_value")
    def render_view_value(self, value, **kwargs):
        """Formatea para vista usando view_format incluyendo la 4ª parte.
           Si llega un string (datos antiguos), lo devuelve tal cual.
        """
        if isinstance(value, basestring):
            return value

        value = value or {}
        parts = self._normalize_parts(value)
        view_format = self._properties.get("view_format") or \
            "%(firstname)s %(middlename)s %(lastname)s %(maternal_lastname)s"

        rendered = view_format % parts
        # Compactar espacios en blanco por subcampos vacíos
        rendered = " ".join([t for t in rendered.split(" ") if t])
        return rendered


registerWidget(
    FullnameWidget,
    title="FullnameWidget",
)
