import vtkImageData from '@kitware/vtk.js/Common/DataModel/ImageData';
import vtkDataArray from '@kitware/vtk.js/Common/Core/DataArray';
import { NIFTI1 } from 'nifti-reader-js';

/**
 * Map NIfTI datatype code to a TypedArray constructor.
 */
const DTYPE_MAP = {
  [NIFTI1.TYPE_UINT8]: Uint8Array,
  [NIFTI1.TYPE_INT8]: Int8Array,
  [NIFTI1.TYPE_INT16]: Int16Array,
  [NIFTI1.TYPE_UINT16]: Uint16Array,
  [NIFTI1.TYPE_INT32]: Int32Array,
  [NIFTI1.TYPE_UINT32]: Uint32Array,
  [NIFTI1.TYPE_FLOAT32]: Float32Array,
  [NIFTI1.TYPE_FLOAT64]: Float64Array,
};

/**
 * Convert a parsed NIfTI header + image buffer into a vtkImageData.
 *
 * NIfTI stores data in Fortran order (first dim fastest) which matches VTK,
 * so no axis reversal is needed — unlike duckn/Zarr (C order).
 *
 * NIfTI affine is RAS; VTK expects LPS → negate rows 0 and 1.
 */
export function niftiToImageData(header, imageBuffer) {
  const dims = [header.dims[1], header.dims[2], header.dims[3]];

  // Get the affine (4x4, RAS)
  // sform takes priority when available
  const affine = header.affine;

  // RAS → LPS: negate first two rows
  const lpsAffine = affine.map((row, i) =>
    i < 2 ? row.map((v) => -v) : [...row]
  );

  // Use pixDims for spacing — more reliable than decomposing the affine,
  // since some files have sform column magnitudes that don't match pixDims.
  const spacing = [
    Math.abs(header.pixDims[1]) || 1,
    Math.abs(header.pixDims[2]) || 1,
    Math.abs(header.pixDims[3]) || 1,
  ];

  // Extract direction as normalized affine columns
  const dirCols = new Array(3);
  for (let col = 0; col < 3; col++) {
    const vec = [lpsAffine[0][col], lpsAffine[1][col], lpsAffine[2][col]];
    const mag = Math.sqrt(vec[0] * vec[0] + vec[1] * vec[1] + vec[2] * vec[2]);
    dirCols[col] = mag > 0
      ? [vec[0] / mag, vec[1] / mag, vec[2] / mag]
      : [col === 0 ? 1 : 0, col === 1 ? 1 : 0, col === 2 ? 1 : 0];
  }

  // Origin from the translation column (already LPS-flipped)
  const origin = [lpsAffine[0][3], lpsAffine[1][3], lpsAffine[2][3]];

  // Column-major direction matrix
  const direction = [
    dirCols[0][0], dirCols[0][1], dirCols[0][2],
    dirCols[1][0], dirCols[1][1], dirCols[1][2],
    dirCols[2][0], dirCols[2][1], dirCols[2][2],
  ];

  // Typed data array
  const TypedArrayCtor = DTYPE_MAP[header.datatypeCode];
  if (!TypedArrayCtor) {
    throw new Error(`Unsupported NIfTI datatype code: ${header.datatypeCode}`);
  }
  const data = new TypedArrayCtor(imageBuffer);

  // Build vtkImageData
  const imageData = vtkImageData.newInstance({ origin, spacing });
  imageData.setDimensions(dims);
  imageData.setDirection(direction);

  const scalars = vtkDataArray.newInstance({
    name: 'NIfTIScalars',
    values: data,
    numberOfComponents: 1,
  });
  imageData.getPointData().setScalars(scalars);

  return imageData;
}
