/**
 * VTK.js adapter for duckn volumes.
 *
 * Converts duckn metadata + array data into vtkImageData with
 * correct spatial embedding. VTK uses LPS convention.
 *
 * Usage:
 *   import { ducknToImageData } from '@duckn/spatial/vtk-adapter';
 *   const imageData = ducknToImageData(data, shape, attrs, { space: 'world' });
 */

import { VolumeGeometry } from './index.js';
import { planCalibration } from './valueTransforms.js';

/**
 * Convert duckn array data to a vtkImageData.
 *
 * @param {TypedArray} data - Flat typed array of voxel values
 * @param {number[]} shape - Array shape (C-order, e.g. [190, 512, 512])
 * @param {Object} attrs - Zarr attributes (must contain attrs.duckn)
 * @param {Object} [opts] - Options
 * @param {string} [opts.space='world'] - Coordinate space
 * @param {Function} opts.vtkImageData - vtkImageData constructor (e.g. vtkImageData.newInstance)
 * @param {Function} opts.vtkDataArray - vtkDataArray constructor (e.g. vtkDataArray.newInstance)
 * @returns {Object} vtkImageData instance
 */
export function ducknToImageData(data, shape, attrs, opts = {}) {
  const {
    space = 'world',
    vtkImageData,
    vtkDataArray,
  } = opts;

  if (!vtkImageData || !vtkDataArray) {
    throw new Error(
      'Must provide vtkImageData and vtkDataArray constructors in opts'
    );
  }

  const duckn = attrs.duckn;
  if (!duckn) throw new Error('attrs.duckn is missing');

  const geom = new VolumeGeometry(duckn, shape);
  const flip = geom.getLpsFlip();
  const ndim = geom.ndim;

  let origin, spacing, directionCols;

  if (space === 'world') {
    origin = geom.origin.map((v, i) => v * flip[i]);
    spacing = [...geom.spacing];

    // Direction cosines in LPS, reversed for VTK (fastest-first)
    directionCols = [];
    for (let j = ndim - 1; j >= 0; j--) {
      const col = [];
      for (let i = 0; i < ndim; i++) {
        col.push(geom.directionCosines[i][j] * flip[i]);
      }
      directionCols.push(col);
    }
    spacing.reverse();

  } else if (space === 'axis-aligned') {
    const p = geom.origin.map(
      (o, i) => o + geom.D[i].reduce((s, d, j) => s + d * geom.centering[j], 0)
    );
    origin = p.map((v, i) => v * flip[i]);
    spacing = geom.spacing.slice().reverse();
    directionCols = [];
    for (let j = ndim - 1; j >= 0; j--) {
      const col = [0, 0, 0];
      col[j] = flip[j];
      directionCols.push(col);
    }

  } else if (space === 'axis-aligned-centered') {
    const p = geom.origin.map(
      (o, i) => o + geom.D[i].reduce((s, d, j) => s + d * geom.centering[j], 0)
    );
    const extent = geom.spacing.map((s, i) => s * geom.shape[i]);
    const center = p.map((v, i) => v + extent[i] / 2);
    origin = p.map((v, i) => (v - center[i]) * flip[i]);
    spacing = geom.spacing.slice().reverse();
    directionCols = [];
    for (let j = ndim - 1; j >= 0; j--) {
      const col = [0, 0, 0];
      col[j] = flip[j];
      directionCols.push(col);
    }

  } else {
    throw new Error(`Unsupported space for VTK adapter: ${space}`);
  }

  // VTK dimensions are fastest-first (xyz)
  const dimensions = shape.slice().reverse();

  // Build direction flat array (column-major for VTK)
  const direction = [];
  for (let col = 0; col < ndim; col++) {
    for (let row = 0; row < ndim; row++) {
      direction.push(directionCols[col][row]);
    }
  }

  const imageData = vtkImageData({ origin, spacing });
  imageData.setDimensions(dimensions);
  imageData.setDirection(direction);

  // Calibrate before handing values to vtk.js, which has no rescale of its
  // own: raw stored values would read ~1024 HU low for a CT, silently.
  // Mirrors @duckn/reader's ducknToImageData — the two are exported under
  // the same name and must not differ by whether they calibrate.
  const calibration = planCalibration(attrs?.duckn?.value_transforms);
  let values = data;
  if (calibration.materialize) {
    values = calibration.materialize(data);
  } else if (calibration.slope !== 1 || calibration.intercept !== 0) {
    const scaled = new Float32Array(data.length);
    for (let k = 0; k < data.length; k++) {
      scaled[k] = calibration.slope * data[k] + calibration.intercept;
    }
    values = scaled;
  }

  const scalars = vtkDataArray({
    name: 'DucknScalars',
    values,
    numberOfComponents: 1,
  });
  imageData.getPointData().setScalars(scalars);

  return imageData;
}

/**
 * Get the affine value transform from duckn metadata.
 *
 * Returns `{ slope, intercept }`, or null when the metadata declares no
 * transforms at all.
 *
 * Delegates to planCalibration rather than scanning for the first `linear`
 * entry, which composed a multi-step chain wrongly and silently ignored a
 * `lut`. A chain that is not affine has no slope/intercept form, so this
 * throws rather than returning a partial answer — use planCalibration
 * directly if you need to handle that case (duckn-spec section 4.3).
 */
export function getValueTransform(attrs) {
  const duckn = attrs.duckn;
  if (!duckn || !duckn.value_transforms || duckn.value_transforms.length === 0) {
    return null;
  }

  const calibration = planCalibration(duckn.value_transforms);
  if (calibration.materialize) {
    throw new Error(
      "duckn: value_transforms cannot be reduced to slope/intercept " +
        "(the chain contains a lut); use planCalibration and apply its " +
        "materialize() to the stored values",
    );
  }
  return { slope: calibration.slope, intercept: calibration.intercept };
}
