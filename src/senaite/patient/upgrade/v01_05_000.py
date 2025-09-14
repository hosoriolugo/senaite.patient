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

from bika.lims import api
try:
    # Disponible desde 1.5.x
    from senaite.patient.catalog.patient_catalog import PatientCatalog
except Exception:
    PatientCatalog = None

version = "1.5.0"
profile = "profile-{0}:default".format(PRODUCT_NAME)


def _sync_patient_catalog(portal):
    """Asegura índices/columnas del catálogo de pacientes."""
    setup_catalogs(portal)  # declara índices/columnas definidos por el add-on
    if PatientCatalog is not None:
        # idempotente: agrega columnas faltantes sin borrar existentes
        cat = api.get_tool("senaite_catalog_patient")
        PatientCatalog().setup(cat)
    logger.info("Patient catalog synchronized (indexes/columns)")


def _update_catalog_mappings(portal):
    """Actualiza mapeos registro↔catálogos en senaite.core."""
    setup_catalog_mappings(portal)
    logger.info("Catalog mappings updated")


def _reindex_patients_metadata_only(portal):
    """Reindexa SOLO metadata de Patient (sin manage_catalogRebuild)."""
    patients_folder = getattr(portal, "patients", None)
    if patients_folder:
        for obj in patients_folder.objectValues():
            try:
                obj.reindexObject(idxs=[], update_metadata=True)
            except Exception:
                pass
    else:
        pc = api.get_tool("portal_catalog")
        for brain in pc(portal_type="Patient"):
            try:
                brain.getObject().reindexObject(idxs=[], update_metadata=True)
            except Exception:
                pass
    logger.info("Patient objects reindexed (metadata only)")


# ---- Handlers referenciados por ZCML (necesarios para arrancar) ----

def upgrade_catalog_indexes(tool):
    """Handler ZCML: añade/sincroniza índices y metadata del catálogo."""
    portal = tool.aq_inner.aq_parent
    _sync_patient_catalog(portal)


def import_registry(tool):
    """Handler ZCML: reimporta plone.app.registry del perfil de producto."""
    portal = tool.aq_inner.aq_parent
    setup = portal.portal_setup
    setup.runImportStepFromProfile(profile, "plone.app.registry")
    logger.info("plone.app.registry reimported")


def update_catalog_mappings(tool):
    """Handler ZCML: actualiza mapeos de catálogos (nombre exacto requerido)."""
    portal = tool.aq_inner.aq_parent
    _update_catalog_mappings(portal)


# ---- Upgrade “todo en uno” (si lo usas desde portal_setup) ----

@upgradestep(PRODUCT_NAME, version)
def upgrade(tool):
    """Upgrade principal 1.5.0: sincroniza catálogo, mappings y reindexa pacientes."""
    portal = tool.aq_inner.aq_parent
    ut = UpgradeUtils(portal)
    ver_from = ut.getInstalledVersion(PRODUCT_NAME)

    if ut.isOlderVersion(PRODUCT_NAME, version):
        logger.info("Skipping upgrade of %s: %s > %s", PRODUCT_NAME, ver_from, version)
        return True

    logger.info("Upgrading %s: %s -> %s", PRODUCT_NAME, ver_from, version)

    try:
        _sync_patient_catalog(portal)
    except Exception as e:
        logger.warn("Could not synchronize patient catalog: %r", e)

    try:
        _update_catalog_mappings(portal)
    except Exception as e:
        logger.warn("Could not update catalog mappings: %r", e)

    try:
        _reindex_patients_metadata_only(portal)
    except Exception as e:
        logger.warn("Could not reindex Patient objects: %r", e)

    logger.info("%s upgraded to version %s", PRODUCT_NAME, version)
    return True
