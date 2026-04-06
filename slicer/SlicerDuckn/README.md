# SlicerDuckn

A 3D Slicer extension that adds **duckn** Zarr volumes and **ZMP** manifests
to Slicer's standard *Add Data* dialog and *Save Data* dialog.

Once installed, `.zmp`, `.zarr.zip`, and Zarr directory stores load into
`vtkMRMLScalarVolumeNode`s with correct spacing, origin, and direction
matrix — indistinguishable from a NIfTI or NRRD load.

## Install

This is a scripted extension, so it's pure Python — no compilation required.

1. Clone or download this repository.
2. In Slicer, open *Edit → Application Settings → Modules* and add the
   path `<repo>/slicer/SlicerDuckn/SlicerDucknIO` to *Additional module paths*.
3. Restart Slicer.
4. Open the Python Console and install the runtime dependency:

   ```python
   slicer.util.pip_install("duckn[vtk] zarr-zmp")
   ```

That's it. The module is hidden in the module list (it only registers IO
handlers), but `.zmp` files now show up under *Add Data*.

## What it does

- **Reader plugin** — registers `SlicerDucknIOFileReader` with Slicer's IO
  manager. Handles `*.zmp`, `*.zarr.zip`, and Zarr directory stores
  (detected by the presence of `zarr.json`).
- **Writer plugin** — registers `SlicerDucknIOFileWriter` so you can
  *Save Data* a `vtkMRMLScalarVolumeNode` as `.zmp` or `.zarr.zip`.
- **Coordinate handling** — duckn's VTK adapter emits LPS world
  coordinates; the reader flips to RAS for Slicer's IJK→RAS matrix.

## Limitations

- **Scalar volumes only.** The duckn segmentation extension and
  multi-resolution / WSI ZMPs are not yet wired into MRML. A
  `vtkMRMLSegmentationNode` adapter is the natural next step.
- **No DICOM browser integration.** ZMPs derived from DICOM still load
  via *Add Data*, not the DICOM database browser.
- **In-memory load.** The reader fully materializes the volume; lazy /
  chunked access through Slicer's pipeline isn't plumbed yet.
