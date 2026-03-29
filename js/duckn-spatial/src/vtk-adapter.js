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

  const scalars = vtkDataArray({
    name: 'DucknScalars',
    values: data,
    numberOfComponents: 1,
  });
  imageData.getPointData().setScalars(scalars);

  return imageData;
}

/**
 * Get value transforms from duckn metadata.
 * Returns { slope, intercept } or null if no linear transform.
 */
export function getValueTransform(attrs) {
  const duckn = attrs.duckn;
  if (!duckn || !duckn.value_transforms) return null;

  for (const vt of duckn.value_transforms) {
    if (vt.name === 'linear' && vt.parameters) {
      return {
        slope: vt.parameters.slope ?? 1,
        intercept: vt.parameters.intercept ?? 0,
      };
    }
  }
  return null;
}
