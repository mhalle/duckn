/**
 * Cornerstone3D adapter for duckn volumes.
 *
 * Provides metadata extraction for Cornerstone's metadata providers
 * and per-slice spatial parameters. Cornerstone uses LPS convention.
 *
 * Usage:
 *   import { DucknMetadataProvider } from '@duckn/spatial/cornerstone-adapter';
 *   const provider = new DucknMetadataProvider(duckn, shape);
 *   metaData.addProvider(provider.getMetadata.bind(provider), 10000);
 */

import { VolumeGeometry } from './index.js';

/**
 * Metadata provider for Cornerstone3D backed by duckn metadata.
 *
 * Extracts DICOM-style metadata modules from duckn spatial embedding
 * and value transforms. Supports any duckn coordinate space.
 */
export class DucknMetadataProvider {
  /**
   * @param {Object} duckn - The duckn attributes object
   * @param {number[]} shape - Array shape (C-order)
   * @param {Object} [opts] - Options
   * @param {string} [opts.space='world'] - Coordinate space for positions
   */
  constructor(duckn, shape, opts = {}) {
    const { space = 'world' } = opts;

    this._duckn = duckn;
    this._shape = shape;
    this._space = space;
    this._geom = new VolumeGeometry(duckn, shape);

    const flip = this._geom.getLpsFlip();
    const axes = duckn.axes.filter((a) => a.space_direction != null);

    // Slice axis (axis 0 in C-order), row axis (1), col axis (2)
    const sliceDir = axes[0].space_direction.map((v, i) => v * flip[i]);
    const rowDir = axes[1].space_direction.map((v, i) => v * flip[i]);
    const colDir = axes[2].space_direction.map((v, i) => v * flip[i]);

    this._sliceDir = sliceDir;
    this._flip = flip;
    this._rawOrigin = duckn.space_origin || [0, 0, 0];

    const rowSpacing = Math.hypot(...rowDir);
    const colSpacing = Math.hypot(...colDir);
    const sliceSpacing = Math.hypot(...sliceDir);

    this._rowSpacing = rowSpacing;
    this._colSpacing = colSpacing;
    this._sliceSpacing = sliceSpacing;

    // DICOM row/col cosines
    this._rowCosines =
      colSpacing > 0
        ? colDir.map((v) => v / colSpacing)
        : [1, 0, 0];
    this._colCosines =
      rowSpacing > 0
        ? rowDir.map((v) => v / rowSpacing)
        : [0, 1, 0];

    this._rows = shape[shape.length - 2];
    this._columns = shape[shape.length - 1];
    this._nSlices = shape[0];

    // Value transforms
    this._slope = 1;
    this._intercept = 0;
    if (duckn.value_transforms) {
      for (const vt of duckn.value_transforms) {
        if (vt.name === 'linear' && vt.parameters) {
          this._slope = vt.parameters.slope ?? 1;
          this._intercept = vt.parameters.intercept ?? 0;
          break;
        }
      }
    }

    // Window center/width from samples or extension tags
    this._windowCenter = null;
    this._windowWidth = null;

    const sliceAxis = axes[0];
    if (sliceAxis.samples?.[0]?.metadata?.dicom) {
      const d = sliceAxis.samples[0].metadata.dicom;
      this._windowCenter = Array.isArray(d.WindowCenter)
        ? d.WindowCenter[0]
        : d.WindowCenter;
      this._windowWidth = Array.isArray(d.WindowWidth)
        ? d.WindowWidth[0]
        : d.WindowWidth;
    }
    if (this._windowCenter == null && duckn.extensions?.dicom?.tags) {
      const tags = duckn.extensions.dicom.tags;
      this._windowCenter = Array.isArray(tags.WindowCenter)
        ? tags.WindowCenter[0]
        : tags.WindowCenter;
      this._windowWidth = Array.isArray(tags.WindowWidth)
        ? tags.WindowWidth[0]
        : tags.WindowWidth;
    }

    // Modality from extensions
    this._modality =
      duckn.extensions?.dicom?.tags?.Modality ?? 'OT';
  }

  /**
   * Get the image position for a specific slice.
   * @param {number} sliceIndex
   * @returns {number[]} LPS position
   */
  getSlicePosition(sliceIndex) {
    const axes = this._duckn.axes.filter((a) => a.space_direction != null);
    const sliceAxis = axes[0];
    const flip = this._flip;
    const rawOrigin = this._rawOrigin;
    const sliceDir = this._sliceDir;

    if (sliceAxis.samples && sliceIndex < sliceAxis.samples.length) {
      const sample = sliceAxis.samples[sliceIndex];
      if (sample.origin) {
        return sample.origin.map((v, i) => v * flip[i]);
      }
      if (sample.position != null) {
        const mag = Math.hypot(...sliceDir);
        const norm =
          mag > 0
            ? sliceDir.map((v) => v / mag)
            : [0, 0, 1];
        return rawOrigin.map(
          (v, i) => v * flip[i] + sample.position * norm[i]
        );
      }
    }
    return rawOrigin.map(
      (v, i) => v * flip[i] + sliceIndex * sliceDir[i]
    );
  }

  /**
   * Cornerstone metadata provider function.
   * Register with: metaData.addProvider(provider.getMetadata.bind(provider), 10000)
   *
   * @param {string} type - Metadata module type
   * @param {string} imageId - Cornerstone image ID (must contain #slice=N)
   * @returns {Object|undefined}
   */
  getMetadata(type, imageId) {
    const match = imageId.match(/#slice=(\d+)/);
    if (!match) return undefined;
    const sliceIndex = parseInt(match[1], 10);

    switch (type) {
      case 'imagePixelModule':
        return {
          bitsAllocated: 16,
          bitsStored: 16,
          highBit: 15,
          pixelRepresentation: this._intercept < 0 ? 1 : 0,
          samplesPerPixel: 1,
          photometricInterpretation: 'MONOCHROME2',
        };

      case 'imagePlaneModule':
        return {
          imagePositionPatient: this.getSlicePosition(sliceIndex),
          imageOrientationPatient: [
            ...this._rowCosines,
            ...this._colCosines,
          ],
          pixelSpacing: [this._rowSpacing, this._colSpacing],
          rows: this._rows,
          columns: this._columns,
          columnPixelSpacing: this._colSpacing,
          rowPixelSpacing: this._rowSpacing,
          frameOfReferenceUID: '1.2.3.4.5',
          sliceThickness: this._sliceSpacing,
        };

      case 'generalSeriesModule':
        return {
          modality: this._modality,
          seriesInstanceUID: '1.2.3.4.5.6',
        };

      case 'voiLutModule':
        if (this._windowCenter != null && this._windowWidth != null) {
          return {
            windowCenter: this._windowCenter,
            windowWidth: this._windowWidth,
          };
        }
        return undefined;

      case 'modalityLutModule':
        return {
          rescaleSlope: this._slope,
          rescaleIntercept: this._intercept,
        };

      default:
        return undefined;
    }
  }
}
