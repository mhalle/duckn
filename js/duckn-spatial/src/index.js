/**
 * Spatial coordinate transforms for duckn volumes.
 *
 * Port of Python duckn.spatial module. Provides conversions between
 * the canonical coordinate spaces defined by the duckn specification:
 *
 * - index: discrete array coordinates (0, 1, 2, ...)
 * - world: continuous physical coordinates (space_origin + space_direction)
 * - axis-aligned: rotation removed, same origin and scale
 * - axis-aligned-centered: axis-aligned with origin at volume center
 *
 * Plus named spaces from space_transforms metadata.
 */

// ---------------------------------------------------------------------------
// Sign flips for LPS conversion (used by adapters)
// ---------------------------------------------------------------------------

export const SPACE_SIGN_FLIPS = {
  // Medical / patient-based
  'left-posterior-superior': [1, 1, 1],
  'right-anterior-superior': [-1, -1, 1],
  'left-anterior-superior': [1, -1, 1],
  // General 3D
  'right-up-back': [-1, -1, 1],
  'right-up-forward': [-1, -1, -1],
  'right-forward-up': [-1, -1, 1],
  'right-down-forward': [-1, 1, -1],
  'forward-right-up': [-1, -1, 1],
  'east-north-up': [1, 1, 1],
  // Scanner
  'scanner-xyz': [1, 1, 1],
  // Generic
  '3D-right-handed': [1, 1, 1],
  '3D-left-handed': [1, 1, 1],
};

// ---------------------------------------------------------------------------
// Linear algebra helpers (no external dependencies)
// ---------------------------------------------------------------------------

function dot(a, b) {
  // Vector dot product
  let s = 0;
  for (let i = 0; i < a.length; i++) s += a[i] * b[i];
  return s;
}

function matVec(M, v) {
  // Matrix × vector (M is array of rows)
  const n = v.length;
  const result = new Array(n);
  for (let i = 0; i < n; i++) {
    let s = 0;
    for (let j = 0; j < n; j++) s += M[i][j] * v[j];
    result[i] = s;
  }
  return result;
}

function matMul(A, B) {
  // Matrix × matrix (both are arrays of rows)
  const n = A.length;
  const result = [];
  for (let i = 0; i < n; i++) {
    result[i] = new Array(n);
    for (let j = 0; j < n; j++) {
      let s = 0;
      for (let k = 0; k < n; k++) s += A[i][k] * B[k][j];
      result[i][j] = s;
    }
  }
  return result;
}

function transpose(M) {
  const n = M.length;
  const result = [];
  for (let i = 0; i < n; i++) {
    result[i] = new Array(n);
    for (let j = 0; j < n; j++) result[i][j] = M[j][i];
  }
  return result;
}

function invert3x3(M) {
  // Invert a 3×3 matrix
  const [a, b, c] = M[0];
  const [d, e, f] = M[1];
  const [g, h, k] = M[2];
  const det = a * (e * k - f * h) - b * (d * k - f * g) + c * (d * h - e * g);
  if (Math.abs(det) < 1e-15) throw new Error('Singular matrix');
  const inv = 1 / det;
  return [
    [(e * k - f * h) * inv, (c * h - b * k) * inv, (b * f - c * e) * inv],
    [(f * g - d * k) * inv, (a * k - c * g) * inv, (c * d - a * f) * inv],
    [(d * h - e * g) * inv, (b * g - a * h) * inv, (a * e - b * d) * inv],
  ];
}

function vecMag(v) {
  return Math.sqrt(v.reduce((s, x) => s + x * x, 0));
}

function vecScale(v, s) {
  return v.map((x) => x * s);
}

function vecSub(a, b) {
  return a.map((x, i) => x - b[i]);
}

function vecAdd(a, b) {
  return a.map((x, i) => x + b[i]);
}

// Simple polar decomposition for 3×3: D = R @ S
// Uses iterative algorithm (Newton's method on orthogonal polar factor)
function polarDecomposition(D) {
  let R = D.map((row) => [...row]);
  for (let iter = 0; iter < 100; iter++) {
    const Rinv = invert3x3(R);
    const RinvT = transpose(Rinv);
    const Rnext = R.map((row, i) =>
      row.map((v, j) => 0.5 * (v + RinvT[i][j]))
    );
    let diff = 0;
    for (let i = 0; i < 3; i++)
      for (let j = 0; j < 3; j++)
        diff += Math.abs(Rnext[i][j] - R[i][j]);
    R = Rnext;
    if (diff < 1e-10) break;
  }
  const S = matMul(transpose(R), D);
  return { R, S };
}

// ---------------------------------------------------------------------------
// Affine helpers
// ---------------------------------------------------------------------------

/** Apply an N×(N+1) affine matrix to a point */
function applyAffine(affine, point) {
  const n = point.length;
  const result = new Array(n);
  for (let i = 0; i < n; i++) {
    let s = affine[i][n]; // translation
    for (let j = 0; j < n; j++) s += affine[i][j] * point[j];
    result[i] = s;
  }
  return result;
}

/** Invert an N×(N+1) affine matrix */
function invertAffine(affine) {
  const n = affine.length;
  const A = affine.map((row) => row.slice(0, n));
  const t = affine.map((row) => row[n]);
  const Ainv = invert3x3(A); // only 3D for now
  const tInv = matVec(Ainv, t).map((v) => -v);
  return Ainv.map((row, i) => [...row, tInv[i]]);
}

// ---------------------------------------------------------------------------
// VolumeGeometry
// ---------------------------------------------------------------------------

/**
 * Spatial geometry of a uniformly sampled duckn volume.
 *
 * Constructed from duckn metadata attrs + array shape.
 */
export class VolumeGeometry {
  /**
   * @param {Object} duckn - The duckn attributes object (attrs.duckn)
   * @param {number[]} shape - Array shape (C-order, e.g. [190, 512, 512])
   */
  constructor(duckn, shape) {
    const axes = duckn.axes || [];
    const spatialAxes = axes.filter((a) => a.space_direction != null);
    const ndim = spatialAxes.length;

    if (ndim === 0) throw new Error('No spatial axes found');
    if (ndim !== 3) throw new Error(`Only 3D supported, got ${ndim}D`);

    // Spatial shape (in axis order)
    const spatialIndices = [];
    for (let i = 0; i < axes.length; i++) {
      if (axes[i].space_direction != null) spatialIndices.push(i);
    }
    this.shape = spatialIndices.map((i) => shape[i]);
    this.ndim = ndim;
    this._spatialIndices = spatialIndices;

    // Direction matrix: column j = space_direction[j]
    this.D = [
      [0, 0, 0],
      [0, 0, 0],
      [0, 0, 0],
    ];
    for (let j = 0; j < ndim; j++) {
      const sd = spatialAxes[j].space_direction;
      for (let i = 0; i < ndim; i++) this.D[i][j] = sd[i];
    }

    // Origin
    this.origin = duckn.space_origin || [0, 0, 0];

    // Centering
    this.centering = spatialAxes.map((ax) =>
      ax.centering === 'cell' || ax.centering == null ? 0.5 : 0.0
    );

    // Spacing
    this.spacing = [];
    this.directionCosines = [
      [0, 0, 0],
      [0, 0, 0],
      [0, 0, 0],
    ];
    for (let j = 0; j < ndim; j++) {
      const col = [this.D[0][j], this.D[1][j], this.D[2][j]];
      const mag = vecMag(col);
      this.spacing.push(mag);
      for (let i = 0; i < ndim; i++) {
        this.directionCosines[i][j] = mag > 0 ? col[i] / mag : 0;
      }
    }

    // Polar decomposition
    const { R, S } = polarDecomposition(this.D);
    this.R = R;
    this.S = S;

    // Affine: world = D @ index + o
    // space_origin is the position of the first sample (index 0).
    // Centering affects extent calculations, not the transform.
    this.affine = this.D.map((row, i) => [
      ...row,
      this.origin[i],
    ]);

    // Inverse affine: index = D^{-1} @ (world - o)
    const Dinv = invert3x3(this.D);
    const negDinvO = matVec(Dinv, this.origin).map((v) => -v);
    this.affineInv = Dinv.map((row, i) => [
      ...row,
      negDinvO[i],
    ]);

    // Space name for flip lookups
    this.spaceName = duckn.space || null;

    // Parse named transforms
    this._namedTransforms = {};
    if (duckn.space_transforms) {
      for (const entry of duckn.space_transforms) {
        const toRef = entry.to;
        if (!toRef || !toRef.name) continue;
        const name = toRef.name;
        const fromSpace =
          entry.from && entry.from.space ? entry.from.space : 'world';

        let forward = null;
        let inverse = null;
        let isIdentity = false;

        if (entry.forward) {
          if (entry.forward.identity) {
            isIdentity = true;
            forward = [
              [1, 0, 0, 0],
              [0, 1, 0, 0],
              [0, 0, 1, 0],
            ];
            inverse = [
              [1, 0, 0, 0],
              [0, 1, 0, 0],
              [0, 0, 1, 0],
            ];
          } else if (entry.forward.affine) {
            forward = entry.forward.affine;
          }
        }
        if (entry.inverse) {
          if (entry.inverse.identity) {
            isIdentity = true;
          } else if (entry.inverse.affine) {
            inverse = entry.inverse.affine;
          }
        }

        if (!isIdentity) {
          if (forward && !inverse) inverse = invertAffine(forward);
          if (inverse && !forward) forward = invertAffine(inverse);
        }

        this._namedTransforms[name] = {
          name,
          fromSpace,
          forward,
          inverse,
          isIdentity,
        };
      }
    }
  }

  // --- Properties ---

  get voxelSize() {
    return this.spacing;
  }

  get volumeSize() {
    return this.spacing.map((s, i) => s * this.shape[i]);
  }

  get isIsotropic() {
    const s = this.spacing;
    return s.every((v) => Math.abs(v - s[0]) / s[0] < 1e-6);
  }

  get isAxisAligned() {
    for (let i = 0; i < this.ndim; i++)
      for (let j = 0; j < this.ndim; j++) {
        const expected = i === j ? 1 : 0;
        if (Math.abs(this.R[i][j] - expected) > 1e-6) return false;
      }
    return true;
  }

  get namedSpaces() {
    return Object.keys(this._namedTransforms);
  }

  // --- Index ↔ World ---

  indexToWorld(index) {
    return applyAffine(this.affine, index);
  }

  worldToIndex(world) {
    return applyAffine(this.affineInv, world);
  }

  // --- World ↔ Axis-Aligned ---

  worldToAxisAligned(world) {
    const o = this.origin;
    const diff = vecSub(world, o);
    return vecAdd(matVec(transpose(this.R), diff), o);
  }

  axisAlignedToWorld(aa) {
    const o = this.origin;
    const diff = vecSub(aa, o);
    return vecAdd(matVec(this.R, diff), o);
  }

  // --- Axis-Aligned ↔ Centered ---

  get _aaCenter() {
    // Center of volume = origin + (n-1)/2 * spacing per axis
    const halfExtent = this.spacing.map((s, i) => (this.shape[i] - 1) / 2.0 * s);
    return vecAdd(this.origin, halfExtent);
  }

  axisAlignedToCentered(aa) {
    return vecSub(aa, this._aaCenter);
  }

  centeredToAxisAligned(aac) {
    return vecAdd(aac, this._aaCenter);
  }

  // --- General transforms ---

  _toBuiltin(coords, space) {
    if (space === 'world') return coords;
    if (space === 'axis-aligned') return this.axisAlignedToWorld(coords);
    if (space === 'axis-aligned-centered') {
      const aa = this.centeredToAxisAligned(coords);
      return this.axisAlignedToWorld(aa);
    }
    if (space === 'index') return this.indexToWorld(coords);
    throw new Error(`Unknown built-in space: ${space}`);
  }

  _fromBuiltin(world, space) {
    if (space === 'world') return world;
    if (space === 'axis-aligned') return this.worldToAxisAligned(world);
    if (space === 'axis-aligned-centered') {
      const aa = this.worldToAxisAligned(world);
      return this.axisAlignedToCentered(aa);
    }
    if (space === 'index') return this.worldToIndex(world);
    throw new Error(`Unknown built-in space: ${space}`);
  }

  /**
   * Convert coordinates from any space to index.
   * @param {number[]} coords
   * @param {string} space - "world", "axis-aligned", "axis-aligned-centered", "index", or named
   * @param {Object} [opts] - { round: false, clamp: false }
   */
  toIndex(coords, space = 'world', opts = {}) {
    const builtins = new Set([
      'world',
      'axis-aligned',
      'axis-aligned-centered',
      'index',
    ]);
    let idx;

    if (builtins.has(space)) {
      if (space === 'index') {
        idx = [...coords];
      } else {
        const world = this._toBuiltin(coords, space);
        idx = this.worldToIndex(world);
      }
    } else if (space in this._namedTransforms) {
      const nt = this._namedTransforms[space];
      if (!nt.inverse) throw new Error(`No inverse for space ${space}`);
      const builtinCoords = applyAffine(nt.inverse, coords);
      const world = this._toBuiltin(builtinCoords, nt.fromSpace);
      idx = this.worldToIndex(world);
    } else {
      throw new Error(`Unknown space: ${space}`);
    }

    if (opts.clamp) {
      for (let j = 0; j < this.ndim; j++) {
        idx[j] = Math.max(0, Math.min(idx[j], this.shape[j] - 1));
      }
    }
    if (opts.round) {
      idx = idx.map(Math.round);
    }
    return idx;
  }

  /**
   * Convert index coordinates to any space.
   * @param {number[]} index
   * @param {string} space
   */
  fromIndex(index, space = 'world') {
    const builtins = new Set([
      'world',
      'axis-aligned',
      'axis-aligned-centered',
      'index',
    ]);

    if (builtins.has(space)) {
      if (space === 'index') return [...index];
      const world = this.indexToWorld(index);
      return this._fromBuiltin(world, space);
    } else if (space in this._namedTransforms) {
      const nt = this._namedTransforms[space];
      if (!nt.forward) throw new Error(`No forward for space ${space}`);
      const world = this.indexToWorld(index);
      const builtinCoords = this._fromBuiltin(world, nt.fromSpace);
      return applyAffine(nt.forward, builtinCoords);
    } else {
      throw new Error(`Unknown space: ${space}`);
    }
  }

  /** Transform between any two spaces. */
  transform(coords, fromSpace, toSpace) {
    if (fromSpace === toSpace) return [...coords];
    const idx = this.toIndex(coords, fromSpace);
    return this.fromIndex(idx, toSpace);
  }

  /** Check if coordinates are within volume bounds. */
  inBounds(coords, space = 'world') {
    const idx = this.toIndex(coords, space);
    return idx.every((v, j) => v >= 0 && v < this.shape[j]);
  }

  /**
   * Get the LPS sign flip for this volume's coordinate space.
   * Used by adapters (VTK, Cornerstone) for coordinate conversion.
   */
  getLpsFlip() {
    return SPACE_SIGN_FLIPS[this.spaceName] || [1, 1, 1];
  }
}
