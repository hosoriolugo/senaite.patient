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

from senaite.core.upgrade import upgradestep
from senaite.core.upgrade.utils import UpgradeUtils
from senaite.patient import logger
from senaite.patient.config import PRODUCT_NAME
from senaite.patient.setuphandlers import setup_catalog_mappings
from senaite.patient.setuphandlers import setup_catalogs

# ⇓ añadidos para sincronizar columnas e indexar pacientes
from bika.lims import api
try:
    # Disponible desde 1.5.x
    from senaite.patient.catalog.patient_catalog import PatientCatalog
except Exception:
    PatientCatalog = None

version = "1.5.0"
profile = "profile-{0}:default".format(PRODUCT_NAME)


@upgradestep(PRODUCT_NAME, version)
def upgrade(tool):
    """Upgrade principal a 1.5.0:
    - Sincroniza el catálogo de pacientes (indexes + metadata columns)
    - Actualiza los mapeos de catálogos
    - Reindexa únicamente objetos Patient (sin manage_catalogRebuild)
    """
    portal = tool.aq_inner.aq_parent
    ut = UpgradeUtils(portal)
    ver_from = ut.getInstalledVersion(PRODUCT_NAME)

    if ut.isOlderVersion(PRODUCT_NAME, version):
        logger.info("Skipping upgrade of {0}: {1} > {2}".format(
            PRODUCT_NAME, ver_from, version))
        return True

    logger.info("Upgrading {0}: {1} -> {2}".format(
        PRODUCT_NAME, ver_from, version))

    # 1) (Re)configura el catálogo de pacientes e índices
    try:
        setup_catalogs(portal)  # asegura índices/columnas declaradas por el add-on
        if PatientCatalog is not None:
            cat = api.get_tool("senaite_catalog_patient")
            # idempotente: agrega columnas faltantes sin borrar existentes
            PatientCatalog().setup(cat)
        logger.info("Patient catalog synchronized (indexes/columns)")
    except Exception as e:
        logger.warn("Could not synchronize patient catalog: %r", e)

    # 2) Actualiza los mapeos de registros ↔ catálogos
    try:
        setup_catalog_mappings(portal)
    except Exception as e:
        logger.warn("Could not update catalog mappings: %r", e)

    # 3) Reindexa SOLO pacientes (evita encolar herramientas/catálogos)
    try:
        patients_folder = getattr(portal, "patients", None)
        if patients_folder:
            for obj in patients_folder.objectValues():
                try:
                    # solo metadata; los índices se recalculan si hace falta
                    obj.reindexObject(idxs=[], update_metadata=True)
                except Exception:
                    pass
        else:
            # fallback: busca por portal_type Patient
            pc = api.get_tool("portal_catalog")
            for brain in pc(portal_type="Patient"):
                try:
                    brain.getObject().reindexObject(idxs=[], update_metadata=True)
                except Exception:
                    pass
        logger.info("Patient objects reindexed")
    except Exception as e:
        logger.warn("Could not reindex Patient objects: %r", e)

    logger.info("{0} upgraded to version {1}".format(PRODUCT_NAME, version))
    return True
