"""SlicerDucknIO — file reader/writer for duckn Zarr volumes and ZMP manifests.

Registers handlers with Slicer's IO manager so that ``.zmp``, ``.zarr``,
and ``.zarr.zip`` files appear in the standard *Add Data* dialog and can
be loaded into ``vtkMRMLScalarVolumeNode`` (and, for the segmentation
extension, ``vtkMRMLSegmentationNode``).

Requires the ``duckn`` Python package (and ``zarr_zmp`` for ZMP files)
to be installed in Slicer's Python environment, e.g.::

    slicer.util.pip_install("duckn[vtk] zarr-zmp")
"""

from __future__ import annotations

import logging
import os

import qt
import slicer
import vtk
from slicer.ScriptedLoadableModule import (
    ScriptedLoadableModule,
    ScriptedLoadableModuleWidget,
    ScriptedLoadableModuleLogic,
)
from slicer.parameterNodeWrapper import parameterNodeWrapper


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------


class SlicerDucknIO(ScriptedLoadableModule):
    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        parent.title = "Duckn / ZMP I/O"
        parent.categories = ["IO"]
        parent.dependencies = []
        parent.contributors = ["duckn contributors"]
        parent.helpText = (
            "Reads and writes duckn Zarr volumes (.zarr, .zarr.zip) and "
            "ZMP manifests (.zmp). Adds these formats to the standard "
            "Add Data dialog."
        )
        parent.acknowledgementText = ""
        # Hidden — this module exists only to register IO handlers.
        parent.hidden = True


# ---------------------------------------------------------------------------
# Widget (empty — module is hidden)
# ---------------------------------------------------------------------------


class SlicerDucknIOWidget(ScriptedLoadableModuleWidget):
    def setup(self):
        ScriptedLoadableModuleWidget.setup(self)


# ---------------------------------------------------------------------------
# Logic — shared loading code
# ---------------------------------------------------------------------------


class SlicerDucknIOLogic(ScriptedLoadableModuleLogic):
    """Loads a duckn source (path to .zmp / .zarr / .zarr.zip) into MRML."""

    @staticmethod
    def _ensure_duckn():
        try:
            import duckn  # noqa: F401
            from duckn.vtk_adapter import to_vtk  # noqa: F401
            return True
        except ImportError as e:
            slicer.util.errorDisplay(
                "The 'duckn' Python package is not installed in Slicer.\n\n"
                "Install it from the Python Console:\n"
                "    slicer.util.pip_install('duckn[vtk] zarr-zmp')\n\n"
                f"Original error: {e}"
            )
            return False

    @staticmethod
    def load_volume(path: str, node_name: str | None = None):
        """Load a duckn source as a vtkMRMLScalarVolumeNode.

        Returns the created node, or None on failure.
        """
        if not SlicerDucknIOLogic._ensure_duckn():
            return None

        from duckn.io import read  # unified reader handles .zmp + .zarr
        from duckn.vtk_adapter import to_vtk

        try:
            vol = read(path)
        except Exception as e:
            slicer.util.errorDisplay(f"Failed to read duckn source:\n{path}\n\n{e}")
            logging.exception("duckn read failed")
            return None

        # Ask the duckn VTK adapter for an image already in RAS — Slicer's
        # native convention — so no manual flipping is needed here.
        try:
            image = to_vtk(vol, space="world", convention="ras")
        except Exception as e:
            slicer.util.errorDisplay(f"Failed to convert volume to VTK:\n{e}")
            logging.exception("duckn to_vtk failed")
            return None

        if node_name is None:
            node_name = os.path.splitext(os.path.basename(path))[0]

        node = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLScalarVolumeNode", node_name
        )

        # Slicer's volume node stores spacing/origin/direction on the node,
        # not on the underlying image. Transfer them across and zero the
        # image's own metadata to avoid double-application.
        spacing = image.GetSpacing()
        origin = image.GetOrigin()
        ijk_to_ras = vtk.vtkMatrix4x4()
        ijk_to_ras.Identity()
        if hasattr(image, "GetDirectionMatrix"):
            dm3 = image.GetDirectionMatrix()
            for i in range(3):
                for j in range(3):
                    # IJKToRAS = Direction (already RAS) * diag(spacing)
                    ijk_to_ras.SetElement(i, j, dm3.GetElement(i, j) * spacing[j])
        else:
            for j in range(3):
                ijk_to_ras.SetElement(j, j, spacing[j])
        for i in range(3):
            ijk_to_ras.SetElement(i, 3, origin[i])

        image.SetSpacing(1.0, 1.0, 1.0)
        image.SetOrigin(0.0, 0.0, 0.0)
        if hasattr(image, "SetDirectionMatrix"):
            identity = vtk.vtkMatrix3x3()
            identity.Identity()
            image.SetDirectionMatrix(identity)

        node.SetAndObserveImageData(image)
        node.SetIJKToRASMatrix(ijk_to_ras)

        # Pick a sensible display node and show it in slice views.
        node.CreateDefaultDisplayNodes()
        slicer.util.setSliceViewerLayers(background=node, fit=True)

        return node

    @staticmethod
    def save_volume(node, path: str) -> bool:
        """Write a vtkMRMLScalarVolumeNode out as a .zmp or duckn Zarr."""
        if not SlicerDucknIOLogic._ensure_duckn():
            return False

        from duckn.io import write
        from duckn.vtk_adapter import from_vtk

        try:
            image = node.GetImageData()
            # Reconstruct a self-contained vtkImageData carrying the
            # node's spacing/origin/direction in RAS, so from_vtk(...,
            # convention="ras") interprets it correctly without any
            # manual axis flipping here.
            tmp = vtk.vtkImageData()
            tmp.ShallowCopy(image)
            ijk_to_ras = vtk.vtkMatrix4x4()
            node.GetIJKToRASMatrix(ijk_to_ras)
            spacing = node.GetSpacing()
            origin = (
                ijk_to_ras.GetElement(0, 3),
                ijk_to_ras.GetElement(1, 3),
                ijk_to_ras.GetElement(2, 3),
            )
            tmp.SetSpacing(*spacing)
            tmp.SetOrigin(*origin)
            if hasattr(tmp, "SetDirectionMatrix"):
                dm3 = vtk.vtkMatrix3x3()
                for i in range(3):
                    for j in range(3):
                        # Strip scale to recover pure rotation in RAS
                        dm3.SetElement(i, j, ijk_to_ras.GetElement(i, j) / spacing[j])
                tmp.SetDirectionMatrix(dm3)

            vol = from_vtk(tmp, convention="ras")
            write(vol, path, overwrite=True)
            return True
        except Exception as e:
            slicer.util.errorDisplay(f"Failed to write duckn file:\n{path}\n\n{e}")
            logging.exception("duckn write failed")
            return False


# ---------------------------------------------------------------------------
# File reader plugin
# ---------------------------------------------------------------------------


class SlicerDucknIOFileReader:
    """Slicer file reader plugin for duckn Zarr / ZMP sources."""

    def __init__(self, parent):
        self.parent = parent

    def description(self):
        return "Duckn volume (Zarr / ZMP)"

    def fileType(self):
        return "DucknVolume"

    def extensions(self):
        return ["Duckn volume (*.zmp)", "Duckn volume (*.zarr.zip)"]

    def canLoadFile(self, filePath: str) -> bool:
        p = filePath.lower()
        if p.endswith(".zmp") or p.endswith(".zarr.zip"):
            return True
        # Directory-style Zarr store
        if os.path.isdir(filePath) and os.path.exists(
            os.path.join(filePath, "zarr.json")
        ):
            return True
        return False

    def load(self, properties: dict) -> bool:
        path = properties["fileName"]
        name = properties.get("name")
        node = SlicerDucknIOLogic.load_volume(path, node_name=name)
        if node is None:
            return False
        # Hand the loaded node back to Slicer's IO manager.
        self.parent.loadedNodes = [node.GetID()]
        return True


# ---------------------------------------------------------------------------
# File writer plugin
# ---------------------------------------------------------------------------


class SlicerDucknIOFileWriter:
    def __init__(self, parent):
        self.parent = parent

    def description(self):
        return "Duckn volume (ZMP)"

    def fileType(self):
        return "DucknVolume"

    def extensions(self, obj):
        if obj is not None and obj.IsA("vtkMRMLScalarVolumeNode"):
            return ["Duckn ZMP (*.zmp)", "Duckn Zarr Zip (*.zarr.zip)"]
        return []

    def canWriteObject(self, obj) -> bool:
        return obj is not None and obj.IsA("vtkMRMLScalarVolumeNode")

    def write(self, properties: dict) -> bool:
        node = slicer.mrmlScene.GetNodeByID(properties["nodeID"])
        path = properties["fileName"]
        ok = SlicerDucknIOLogic.save_volume(node, path)
        if ok:
            self.parent.writtenNodes = [node.GetID()]
        return ok
