import vtkImageData from '@kitware/vtk.js/Common/DataModel/ImageData';
import vtkDataArray from '@kitware/vtk.js/Common/Core/DataArray';

/**
 * Sign-flip vectors for converting coordinate systems to LPS (VTK convention).
 *
 * LPS = Left-Posterior-Superior: +X=Left, +Y=Posterior, +Z=Superior
 * RAS = Right-Anterior-Superior: negate X and Y
 * LAS = Left-Anterior-Superior: negate Y
 */
const SPACE_SIGN_FLIPS = {
  'left-posterior-superior': [1, 1, 1],
  'right-anterior-superior': [-1, -1, 1],
  'left-anterior-superior': [1, -1, 1],
};

/**
 * Convert duckn metadata + raw data into a vtkImageData instance.
 *
 * @param {TypedArray} data - Flat typed array of voxel values (C-order from Zarr)
 * @param {number[]} shape - Array shape in C-order (slowest-first)
 * @param {object} attrs - Zarr array attributes (must contain `duckn` key)
 * @param {object} [options]
 * @param {string} [options.scalarArrayName='DucknScalars'] - Name for the scalar data array
 * @returns {vtkImageData}
 */
export function ducknToImageData(data, shape, attrs, options = {}) {
  const { scalarArrayName = 'DucknScalars' } = options;

  const duckn = attrs.duckn;
  if (!duckn) {
    throw new Error('duckn metadata not found: attrs.duckn is missing');
  }

  const axes = duckn.axes;
  if (!axes) {
    throw new Error('duckn metadata missing axes');
  }

  // Find spatial axes (those with space_direction)
  const spatialAxes = axes.filter((a) => a.space_direction != null);
  if (spatialAxes.length !== 3) {
    throw new Error(
      `Expected exactly 3 spatial axes, got ${spatialAxes.length}`
    );
  }
  if (shape.length !== 3) {
    throw new Error(
      `Expected 3D array shape, got ${shape.length}D`
    );
  }

  // Determine sign-flip for LPS conversion
  const spaceName = duckn.space;
  const flip = spaceName && SPACE_SIGN_FLIPS[spaceName]
    ? SPACE_SIGN_FLIPS[spaceName]
    : [1, 1, 1];

  // Apply sign flip to space_origin
  const rawOrigin = duckn.space_origin || [0, 0, 0];
  const origin = rawOrigin.map((v, i) => v * flip[i]);

  // Apply sign flip to space_directions, then reverse for C-order → VTK order
  const flippedDirs = spatialAxes.map((ax) =>
    ax.space_direction.map((v, i) => v * flip[i])
  );
  // Reverse: duckn C-order axes[0]=slowest → VTK dimension[2]=slowest
  const vtkDirs = [flippedDirs[2], flippedDirs[1], flippedDirs[0]];

  // Decompose each direction into spacing (magnitude) and unit direction
  const spacing = new Array(3);
  const dirCols = new Array(3);
  for (let i = 0; i < 3; i++) {
    const d = vtkDirs[i];
    const mag = Math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2]);
    if (mag === 0) {
      throw new Error(`Zero-length space_direction for VTK axis ${i}`);
    }
    spacing[i] = mag;
    dirCols[i] = [d[0] / mag, d[1] / mag, d[2] / mag];
  }

  // Build 9-element column-major direction matrix
  // vtk.js direction is column-major: [col0[0], col0[1], col0[2], col1[0], ...]
  const direction = [
    dirCols[0][0], dirCols[0][1], dirCols[0][2],
    dirCols[1][0], dirCols[1][1], dirCols[1][2],
    dirCols[2][0], dirCols[2][1], dirCols[2][2],
  ];

  // Dimensions: reverse shape for C-order → VTK order
  const dimensions = [shape[2], shape[1], shape[0]];

  // Create vtkImageData
  const imageData = vtkImageData.newInstance({
    origin,
    spacing,
  });
  imageData.setDimensions(dimensions);
  imageData.setDirection(direction);

  // Attach scalar data
  const scalars = vtkDataArray.newInstance({
    name: scalarArrayName,
    values: data,
    numberOfComponents: 1,
  });
  imageData.getPointData().setScalars(scalars);

  return imageData;
}
