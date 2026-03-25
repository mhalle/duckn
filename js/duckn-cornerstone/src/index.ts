/**
 * Cornerstone3D image loader for duckn Zarr/ZMP stores.
 *
 * Registers a "duckn:" scheme with Cornerstone's image loader registry.
 * Accepts both Zarr directory stores (via FetchStore) and ZMP manifests
 * (via zarr-zmp-ts ZMPStore).
 *
 * Image IDs:
 *   duckn:https://example.com/scan.zarr#slice=42
 *   duckn:https://example.com/scan.zmp#slice=42
 */

import * as zarr from "zarrita";
import FetchStore from "@zarrita/storage/fetch";
import { ZMPStore } from "zarr-zmp-ts";
import type { Types } from "@cornerstonejs/core";

// ---------------------------------------------------------------------------
// Coordinate conversion
// ---------------------------------------------------------------------------

const SPACE_SIGN_FLIPS: Record<string, [number, number, number]> = {
  "left-posterior-superior": [1, 1, 1],
  "right-anterior-superior": [-1, -1, 1],
  "left-anterior-superior": [1, -1, 1],
};

interface DucknAxis {
  kind?: string;
  space_direction?: number[];
  centering?: string;
  unit?: string;
  thickness?: number;
  samples?: Array<{
    position?: number;
    origin?: number[];
    thickness?: number;
    metadata?: Record<string, unknown>;
  }>;
}

interface DucknMetadata {
  version?: string;
  space?: string;
  space_origin?: number[];
  axes?: DucknAxis[];
  value_transforms?: Array<{
    name: string;
    parameters?: { slope?: number; intercept?: number };
  }>;
  extensions?: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Store cache — reuse opened stores across slice loads
// ---------------------------------------------------------------------------

interface CachedStore {
  store: zarr.AsyncReadable;
  array: zarr.Array<zarr.DataType, zarr.AsyncReadable>;
  duckn: DucknMetadata;
  shape: number[];
}

const storeCache = new Map<string, Promise<CachedStore>>();

async function openStore(storeUrl: string): Promise<CachedStore> {
  let cached = storeCache.get(storeUrl);
  if (cached) return cached;

  const promise = (async () => {
    let store: zarr.AsyncReadable;

    if (storeUrl.endsWith(".zmp")) {
      store = await ZMPStore.fromUrl(storeUrl);
    } else {
      store = new FetchStore(storeUrl);
    }

    const arr = await zarr.open(store, { kind: "array" });
    const attrs = arr.attrs as Record<string, unknown>;
    const duckn = attrs.duckn as DucknMetadata;

    if (!duckn) {
      throw new Error(`No duckn metadata found at ${storeUrl}`);
    }

    return { store, array: arr, duckn, shape: arr.shape };
  })();

  storeCache.set(storeUrl, promise);
  return promise;
}

// ---------------------------------------------------------------------------
// Metadata extraction from duckn
// ---------------------------------------------------------------------------

function extractSliceMetadata(
  duckn: DucknMetadata,
  shape: number[],
  sliceIndex: number,
) {
  const axes = duckn.axes;
  if (!axes) throw new Error("duckn metadata missing axes");

  // Find spatial axes (those with space_direction)
  const spatialAxes = axes.filter((a) => a.space_direction != null);
  if (spatialAxes.length !== 3) {
    throw new Error(`Expected 3 spatial axes, got ${spatialAxes.length}`);
  }

  // Sign flip for LPS conversion
  const spaceName = duckn.space;
  const flip =
    spaceName && SPACE_SIGN_FLIPS[spaceName]
      ? SPACE_SIGN_FLIPS[spaceName]
      : ([1, 1, 1] as [number, number, number]);

  // C-order: axes[0]=slice, axes[1]=row, axes[2]=col
  const sliceAxis = spatialAxes[0];
  const rowAxis = spatialAxes[1]; // col_cosines * row_spacing
  const colAxis = spatialAxes[2]; // row_cosines * col_spacing

  const sliceDir = sliceAxis.space_direction!.map((v, i) => v * flip[i]);
  const rowDir = rowAxis.space_direction!.map((v, i) => v * flip[i]);
  const colDir = colAxis.space_direction!.map((v, i) => v * flip[i]);

  const rowSpacing = Math.sqrt(
    rowDir[0] ** 2 + rowDir[1] ** 2 + rowDir[2] ** 2,
  );
  const colSpacing = Math.sqrt(
    colDir[0] ** 2 + colDir[1] ** 2 + colDir[2] ** 2,
  );

  // Row and column cosines (DICOM convention)
  // duckn axes[2] = row_cosines * col_spacing (col index → row direction)
  // duckn axes[1] = col_cosines * row_spacing (row index → col direction)
  const rowCosines =
    colSpacing > 0
      ? [colDir[0] / colSpacing, colDir[1] / colSpacing, colDir[2] / colSpacing]
      : [1, 0, 0];
  const colCosines =
    rowSpacing > 0
      ? [rowDir[0] / rowSpacing, rowDir[1] / rowSpacing, rowDir[2] / rowSpacing]
      : [0, 1, 0];

  // Image position — per-slice if samples available, otherwise computed
  const rawOrigin = duckn.space_origin || [0, 0, 0];
  let imagePosition: number[];

  if (sliceAxis.samples && sliceIndex < sliceAxis.samples.length) {
    const sample = sliceAxis.samples[sliceIndex];
    if (sample.origin) {
      imagePosition = sample.origin.map((v, i) => v * flip[i]);
    } else if (sample.position != null) {
      // Scalar position along slice direction
      const sliceMag = Math.sqrt(
        sliceDir[0] ** 2 + sliceDir[1] ** 2 + sliceDir[2] ** 2,
      );
      const norm =
        sliceMag > 0
          ? [sliceDir[0] / sliceMag, sliceDir[1] / sliceMag, sliceDir[2] / sliceMag]
          : [0, 0, 1];
      imagePosition = rawOrigin.map(
        (v, i) => v * flip[i] + sample.position! * norm[i],
      );
    } else {
      // Default: linear from space_direction
      imagePosition = rawOrigin.map(
        (v, i) => v * flip[i] + sliceIndex * sliceDir[i],
      );
    }
  } else {
    imagePosition = rawOrigin.map(
      (v, i) => v * flip[i] + sliceIndex * sliceDir[i],
    );
  }

  // Window/level from per-slice metadata
  let windowCenter: number | undefined;
  let windowWidth: number | undefined;

  if (sliceAxis.samples && sliceIndex < sliceAxis.samples.length) {
    const sampleMeta = sliceAxis.samples[sliceIndex].metadata;
    if (sampleMeta) {
      const dicom = sampleMeta.dicom as Record<string, unknown> | undefined;
      if (dicom) {
        const wc = dicom.WindowCenter;
        const ww = dicom.WindowWidth;
        if (typeof wc === "number") windowCenter = wc;
        else if (Array.isArray(wc) && wc.length > 0) windowCenter = wc[0] as number;
        if (typeof ww === "number") windowWidth = ww;
        else if (Array.isArray(ww) && ww.length > 0) windowWidth = ww[0] as number;
      }
    }
  }

  // Value transforms (slope/intercept)
  let slope = 1;
  let intercept = 0;
  if (duckn.value_transforms) {
    for (const vt of duckn.value_transforms) {
      if (vt.name === "linear" && vt.parameters) {
        slope = vt.parameters.slope ?? 1;
        intercept = vt.parameters.intercept ?? 0;
        break;
      }
    }
  }

  // Rows/columns from shape (C-order: shape[1]=rows, shape[2]=cols)
  const rows = shape[shape.length - 2];
  const columns = shape[shape.length - 1];

  return {
    rows,
    columns,
    rowPixelSpacing: rowSpacing,
    columnPixelSpacing: colSpacing,
    rowCosines,
    columnCosines: colCosines,
    imagePositionPatient: imagePosition,
    windowCenter,
    windowWidth,
    slope,
    intercept,
    sliceThickness: sliceAxis.thickness,
  };
}

// ---------------------------------------------------------------------------
// Image loader
// ---------------------------------------------------------------------------

function parseImageId(imageId: string): { storeUrl: string; sliceIndex: number } {
  // duckn:https://example.com/scan.zarr#slice=42
  const withoutScheme = imageId.replace(/^duckn:/, "");
  const hashIdx = withoutScheme.indexOf("#");

  let storeUrl: string;
  let sliceIndex = 0;

  if (hashIdx >= 0) {
    storeUrl = withoutScheme.slice(0, hashIdx);
    const fragment = withoutScheme.slice(hashIdx + 1);
    const params = new URLSearchParams(fragment);
    const sliceStr = params.get("slice");
    if (sliceStr != null) sliceIndex = parseInt(sliceStr, 10);
  } else {
    storeUrl = withoutScheme;
  }

  return { storeUrl, sliceIndex };
}

async function loadImage(imageId: string): Promise<Types.IImage> {
  const { storeUrl, sliceIndex } = parseImageId(imageId);
  const { array, duckn, shape } = await openStore(storeUrl);

  // Read single slice
  const result = await zarr.get(array, [sliceIndex, null, null]);
  const pixelData = result.data;

  const meta = extractSliceMetadata(duckn, shape, sliceIndex);

  const image: Types.IImage = {
    imageId,
    minPixelValue: 0,
    maxPixelValue: 0,
    slope: meta.slope,
    intercept: meta.intercept,
    windowCenter: meta.windowCenter,
    windowWidth: meta.windowWidth,
    rows: meta.rows,
    columns: meta.columns,
    height: meta.rows,
    width: meta.columns,
    color: false,
    rgba: false,
    numComps: 1,
    columnPixelSpacing: meta.columnPixelSpacing,
    rowPixelSpacing: meta.rowPixelSpacing,
    sliceThickness: meta.sliceThickness,
    imagePositionPatient: meta.imagePositionPatient,
    imageOrientationPatient: [
      ...meta.rowCosines,
      ...meta.columnCosines,
    ],
    sizeInBytes: pixelData.byteLength,
    getPixelData: () => pixelData,
  };

  // Compute min/max
  let min = Infinity;
  let max = -Infinity;
  for (let i = 0; i < pixelData.length; i++) {
    const v = pixelData[i];
    if (v < min) min = v;
    if (v > max) max = v;
  }
  image.minPixelValue = min;
  image.maxPixelValue = max;

  return image;
}

// ---------------------------------------------------------------------------
// Registration
// ---------------------------------------------------------------------------

/**
 * Register the duckn image loader with Cornerstone3D.
 *
 * After calling this, image IDs starting with "duckn:" will be handled
 * by this loader.
 *
 * @param cornerstone - The @cornerstonejs/core module
 */
export function registerDucknImageLoader(
  cornerstone: { imageLoader: { registerImageLoader: (scheme: string, loader: unknown) => void } },
): void {
  cornerstone.imageLoader.registerImageLoader("duckn", (imageId: string) => {
    const promise = loadImage(imageId);
    return { promise };
  });
}

/**
 * Generate image IDs for all slices in a duckn store.
 *
 * @param storeUrl - URL to a Zarr store or ZMP manifest
 * @returns Array of image IDs, one per slice
 */
export async function getDucknImageIds(storeUrl: string): Promise<string[]> {
  const { shape } = await openStore(storeUrl);
  const nSlices = shape[0];
  return Array.from({ length: nSlices }, (_, i) => `duckn:${storeUrl}#slice=${i}`);
}

export { openStore, extractSliceMetadata, parseImageId };
