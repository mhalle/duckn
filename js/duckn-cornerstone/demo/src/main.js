import {
  init,
  RenderingEngine,
  Enums,
  EVENTS,
  volumeLoader,
  metaData,
  setVolumesForViewports,
  registerImageLoader,
  eventTarget,
  cache as csCache,
} from '@cornerstonejs/core';
import * as cornerstoneTools from '@cornerstonejs/tools';
import * as zarr from 'zarrita';
import FetchStore from '@zarrita/storage/fetch';
import { ZMPStore, HttpResolver } from 'zarr-zmp-ts';

const {
  WindowLevelTool,
  StackScrollTool,
  ZoomTool,
  PanTool,
  CrosshairsTool,
  ReferenceLinesTool,
  TrackballRotateTool,
  ToolGroupManager,
  Enums: csToolsEnums,
  synchronizers,
} = cornerstoneTools;

// ---- DICOMweb endpoint (provided externally, not in the ZMP) ----

const IDC_DICOMWEB_URL = 'https://proxy.imaging.datacommons.cancer.gov/current/viewer-only-no-downloads-see-tinyurl-dot-com-slash-3j3d9jyp/dicomWeb';

// ---- CORS proxy ----

const PROXY_PREFIX = window.location.origin + '/cors-proxy/';

function proxyUrl(url) {
  if (url.startsWith('http://') || url.startsWith('https://')) {
    return PROXY_PREFIX + encodeURIComponent(url);
  }
  return url;
}

class ProxiedHttpResolver {
  async resolve(params, bases) {
    let url = params.url ?? '';
    if (bases?.length) {
      let effectiveBase;
      for (const base of bases) {
        const baseUrl = base.url;
        if (baseUrl == null) continue;
        if (effectiveBase == null || baseUrl.includes('://') || baseUrl.startsWith('/')) {
          effectiveBase = baseUrl;
        } else if (effectiveBase.startsWith('http://') || effectiveBase.startsWith('https://')) {
          effectiveBase = new URL(baseUrl, effectiveBase).href;
        }
      }
      if (effectiveBase && url) {
        if (!url.includes('://') && !url.startsWith('/')) {
          if (effectiveBase.startsWith('http://') || effectiveBase.startsWith('https://')) {
            url = new URL(url, effectiveBase).href;
          }
        }
      } else if (effectiveBase && !url) {
        url = effectiveBase;
      }
    }

    const headers = {};
    const offset = params.offset;
    const length = params.length;
    if (offset != null && length != null) {
      headers['Range'] = `bytes=${offset}-${offset + length - 1}`;
    }

    // Request raw bytes for WADO-RS frame URLs (avoids multipart/related)
    if (url.includes('/frames/')) {
      headers['Accept'] = 'application/octet-stream';
    }

    const resp = await fetch(proxyUrl(url), { headers });
    if (resp.status === 200 || resp.status === 206) {
      return new Uint8Array(await resp.arrayBuffer());
    }
    return undefined;
  }
}

/**
 * Extract the first part's body from a multipart/related response.
 * Returns the raw bytes unchanged if not multipart.
 */
function stripMultipart(data, contentType) {
  if (!contentType || !contentType.includes('multipart/related')) return data;

  const match = contentType.match(/boundary=([^\s;]+)/);
  if (!match) return data;
  const boundary = match[1].replace(/^"(.*)"$/, '$1');
  const sep = new TextEncoder().encode('--' + boundary);

  // Find first boundary
  let start = 0;
  outer: for (let i = 0; i <= data.length - sep.length; i++) {
    for (let j = 0; j < sep.length; j++) {
      if (data[i + j] !== sep[j]) continue outer;
    }
    start = i + sep.length;
    break;
  }

  // Skip past \r\n after boundary
  while (start < data.length && (data[start] === 0x0d || data[start] === 0x0a)) start++;

  // Find blank line (end of part headers)
  for (let i = start; i < data.length - 3; i++) {
    if (data[i] === 0x0d && data[i+1] === 0x0a && data[i+2] === 0x0d && data[i+3] === 0x0a) {
      start = i + 4;
      break;
    }
    if (data[i] === 0x0a && data[i+1] === 0x0a) {
      start = i + 2;
      break;
    }
  }

  // Find next boundary (end of part body)
  let end = data.length;
  outer2: for (let i = start; i <= data.length - sep.length; i++) {
    for (let j = 0; j < sep.length; j++) {
      if (data[i + j] !== sep[j]) continue outer2;
    }
    // Back up past \r\n before boundary
    end = i;
    if (end >= 2 && data[end-2] === 0x0d && data[end-1] === 0x0a) end -= 2;
    else if (end >= 1 && data[end-1] === 0x0a) end -= 1;
    break;
  }

  return new Uint8Array(data.buffer.slice(data.byteOffset + start, data.byteOffset + end));
}

/**
 * DICOMweb resolver for zarr-zmp-ts.
 *
 * Constructs WADO-RS frame URLs from DICOM UIDs stored in the ZMP:
 *   base_resolve: {"dicomweb": {"study": "...", "series": "..."}}
 *   resolve:      {"dicomweb": {"instance": "...", "frame": 1}}
 *
 * The endpoint URL is provided at construction time, keeping ZMPs
 * portable across DICOMweb servers.
 */
class DICOMwebResolver {
  constructor(endpointUrl) {
    // Strip trailing slash
    this.endpointUrl = endpointUrl.replace(/\/+$/, '');
  }

  async resolve(params, bases) {
    const instance = params.instance;
    const frame = params.frame ?? 1;
    if (!instance) return undefined;

    // Get study/series from base_resolve chain
    let study, series;
    if (bases?.length) {
      for (const base of bases) {
        if (base.study) study = base.study;
        if (base.series) series = base.series;
      }
    }
    if (!study || !series) return undefined;

    const url = `${this.endpointUrl}/studies/${study}/series/${series}/instances/${instance}/frames/${frame}`;

    const resp = await fetch(url);
    if (resp.status !== 200) return undefined;

    const raw = new Uint8Array(await resp.arrayBuffer());
    return stripMultipart(raw, resp.headers.get('content-type'));
  }
}

// ---- duckn store + metadata cache ----

const SPACE_SIGN_FLIPS = {
  'left-posterior-superior': [1, 1, 1],
  'right-anterior-superior': [-1, -1, 1],
  'left-anterior-superior': [1, -1, 1],
};

// Cache: storeUrl → { duckn, shape, array }
let ducknCache = null;

async function openDucknStore(url) {
  const fullUrl = new URL(url, window.location.origin).href;
  let store;
  if (fullUrl.endsWith('.zmp')) {
    const resolvers = { http: new HttpResolver() };
    if (IDC_DICOMWEB_URL) {
      resolvers.dicomweb = new DICOMwebResolver(IDC_DICOMWEB_URL);
    }
    store = await ZMPStore.fromUrl(fullUrl, { resolvers });
  } else {
    store = new FetchStore(fullUrl);
  }

  const arr = await zarr.open(store, { kind: 'array' });
  const duckn = arr.attrs.duckn;
  if (!duckn) throw new Error('No duckn metadata found');

  const axes = duckn.axes.filter(a => a.space_direction != null);
  const flip = duckn.space && SPACE_SIGN_FLIPS[duckn.space]
    ? SPACE_SIGN_FLIPS[duckn.space] : [1, 1, 1];

  // Precompute per-slice metadata
  const sliceAxis = axes[0];
  const rowAxis = axes[1];
  const colAxis = axes[2];

  const sliceDir = sliceAxis.space_direction.map((v, i) => v * flip[i]);
  const rowDir = rowAxis.space_direction.map((v, i) => v * flip[i]);
  const colDir = colAxis.space_direction.map((v, i) => v * flip[i]);

  const rowSpacing = Math.hypot(...rowDir);
  const colSpacing = Math.hypot(...colDir);
  const sliceSpacing = Math.hypot(...sliceDir);

  const rowCosines = colSpacing > 0
    ? colDir.map(v => v / colSpacing) : [1, 0, 0];
  const colCosines = rowSpacing > 0
    ? rowDir.map(v => v / rowSpacing) : [0, 1, 0];

  const rawOrigin = duckn.space_origin || [0, 0, 0];

  let slope = 1, intercept = 0;
  if (duckn.value_transforms) {
    for (const vt of duckn.value_transforms) {
      if (vt.name === 'linear' && vt.parameters) {
        slope = vt.parameters.slope ?? 1;
        intercept = vt.parameters.intercept ?? 0;
        break;
      }
    }
  }

  // Window from per-slice metadata, extension tags, or leave undefined
  let windowCenter, windowWidth;
  if (sliceAxis.samples?.[0]?.metadata?.dicom) {
    const d = sliceAxis.samples[0].metadata.dicom;
    windowCenter = Array.isArray(d.WindowCenter) ? d.WindowCenter[0] : d.WindowCenter;
    windowWidth = Array.isArray(d.WindowWidth) ? d.WindowWidth[0] : d.WindowWidth;
  }
  if (windowCenter == null) {
    const dicomTags = duckn.extensions?.dicom?.tags;
    if (dicomTags) {
      const wc = dicomTags.WindowCenter;
      const ww = dicomTags.WindowWidth;
      windowCenter = Array.isArray(wc) ? wc[0] : wc;
      windowWidth = Array.isArray(ww) ? ww[0] : ww;
    }
  }

  const nSlices = arr.shape[0];
  const rows = arr.shape[1];
  const columns = arr.shape[2];

  ducknCache = {
    array: arr,
    duckn,
    nSlices,
    rows,
    columns,
    rowSpacing,
    colSpacing,
    sliceSpacing,
    rowCosines,
    colCosines,
    sliceDir,
    rawOrigin,
    flip,
    sliceAxis,
    slope,
    intercept,
    windowCenter,
    windowWidth,
  };

  // Update auto preset from DICOM metadata
  if (ducknCache.windowCenter != null && ducknCache.windowWidth != null) {
    CT_PRESETS.auto = { center: ducknCache.windowCenter, width: ducknCache.windowWidth };
  }

  return ducknCache;
}

function getSlicePosition(cache, sliceIndex) {
  const { sliceAxis, rawOrigin, flip, sliceDir } = cache;

  if (sliceAxis.samples && sliceIndex < sliceAxis.samples.length) {
    const sample = sliceAxis.samples[sliceIndex];
    if (sample.origin) {
      return sample.origin.map((v, i) => v * flip[i]);
    }
    if (sample.position != null) {
      const mag = Math.hypot(...sliceDir);
      const norm = mag > 0 ? sliceDir.map(v => v / mag) : [0, 0, 1];
      return rawOrigin.map((v, i) => v * flip[i] + sample.position * norm[i]);
    }
  }
  return rawOrigin.map((v, i) => v * flip[i] + sliceIndex * sliceDir[i]);
}

// ---- Cornerstone metadata provider ----

function ducknMetadataProvider(type, imageId) {
  if (!ducknCache) return;
  const match = imageId.match(/#slice=(\d+)/);
  if (!match) return;
  const sliceIndex = parseInt(match[1], 10);
  const c = ducknCache;

  switch (type) {
    case 'imagePixelModule':
      // signed if pre-scaling is active (produces Int16), unsigned otherwise
      const isSigned = (c.slope !== 1 || c.intercept !== 0) ? 1 : 0;
      return {
        bitsAllocated: 16,
        bitsStored: 16,
        highBit: 15,
        pixelRepresentation: isSigned,
        samplesPerPixel: 1,
        photometricInterpretation: 'MONOCHROME2',
      };

    case 'imagePlaneModule':
      return {
        imagePositionPatient: getSlicePosition(c, sliceIndex),
        imageOrientationPatient: [...c.rowCosines, ...c.colCosines],
        pixelSpacing: [c.rowSpacing, c.colSpacing],
        rows: c.rows,
        columns: c.columns,
        columnPixelSpacing: c.colSpacing,
        rowPixelSpacing: c.rowSpacing,
        frameOfReferenceUID: '1.2.3.4.5',
        sliceThickness: c.sliceSpacing,
      };

    case 'generalSeriesModule':
      return {
        modality: 'CT',
        seriesInstanceUID: '1.2.3.4.5.6',
      };

    case 'voiLutModule':
      return {
        windowCenter: c.windowCenter ?? 40,
        windowWidth: c.windowWidth ?? 400,
      };

    case 'modalityLutModule':
      // Data is pre-scaled in the image loader
      return {
        rescaleSlope: 1,
        rescaleIntercept: 0,
      };

    default:
      return undefined;
  }
}

// ---- duckn image loader for Cornerstone ----

async function loadDucknImage(imageId) {
  if (!ducknCache) throw new Error('Store not loaded');
  const match = imageId.match(/#slice=(\d+)/);
  const sliceIndex = parseInt(match[1], 10);

  const result = await zarr.get(ducknCache.array, [sliceIndex, null, null]);
  const rawData = result.data;

  // Pre-scale to calibrated values (HU for CT) so 3D presets work correctly
  const c = ducknCache;
  let pixelData;
  if (c.slope !== 1 || c.intercept !== 0) {
    const scaled = new Int16Array(rawData.length);
    for (let i = 0; i < rawData.length; i++) {
      scaled[i] = Math.round(rawData[i] * c.slope + c.intercept);
    }
    pixelData = scaled;
  } else {
    pixelData = rawData;
  }

  let min = Infinity, max = -Infinity;
  for (let i = 0; i < pixelData.length; i++) {
    if (pixelData[i] < min) min = pixelData[i];
    if (pixelData[i] > max) max = pixelData[i];
  }

  return {
    imageId,
    minPixelValue: min,
    maxPixelValue: max,
    slope: 1,
    intercept: 0,
    windowCenter: c.windowCenter ?? 40,
    windowWidth: c.windowWidth ?? 400,
    rows: c.rows,
    columns: c.columns,
    height: c.rows,
    width: c.columns,
    color: false,
    rgba: false,
    numComps: 1,
    columnPixelSpacing: c.colSpacing,
    rowPixelSpacing: c.rowSpacing,
    sliceThickness: c.sliceSpacing,
    imagePositionPatient: getSlicePosition(c, sliceIndex),
    imageOrientationPatient: [...c.rowCosines, ...c.colCosines],
    sizeInBytes: pixelData.byteLength,
    getPixelData: () => pixelData,
  };
}

// ---- CT window presets ----

const CT_PRESETS = {
  'auto':    { center: 40, width: 400 },  // updated on load from DICOM metadata
  'ct-bone': { center: 500, width: 2000 },
  'ct-soft': { center: 40, width: 400 },
  'ct-lung': { center: -600, width: 1500 },
  'ct-brain': { center: 40, width: 80 },
};

// ---- UI elements ----

const urlInput = document.getElementById('url-input');
const loadBtn = document.getElementById('load-btn');
const presetSelect = document.getElementById('preset');
const preset3dSelect = document.getElementById('preset3d');
const sidebar = document.getElementById('sidebar');
const statusEl = document.getElementById('status');

let renderingEngine = null;
let toolsInitialized = false;
const viewportIds = ['axial', 'sagittal', 'coronal', 'vol3d'];

function setupTools() {
  if (toolsInitialized) return;
  toolsInitialized = true;

  // Ortho viewports: crosshairs + 2D tools
  const orthoIds = ['axial', 'sagittal', 'coronal'];
  const orthoGroup = ToolGroupManager.createToolGroup('duckn-ortho');
  orthoGroup.addTool(WindowLevelTool.toolName);
  orthoGroup.addTool(StackScrollTool.toolName);
  orthoGroup.addTool(ZoomTool.toolName);
  orthoGroup.addTool(PanTool.toolName);
  orthoGroup.addTool(CrosshairsTool.toolName);
  orthoGroup.addTool(ReferenceLinesTool.toolName);
  orthoIds.forEach(id => orthoGroup.addViewport(id, 'duckn-engine'));

  // 3D viewport: rotate/zoom/pan
  const vol3dGroup = ToolGroupManager.createToolGroup('duckn-3d');
  vol3dGroup.addTool(TrackballRotateTool.toolName);
  vol3dGroup.addTool(ZoomTool.toolName);
  vol3dGroup.addTool(PanTool.toolName);
  vol3dGroup.addViewport('vol3d', 'duckn-engine');

  // Ortho tools
  orthoGroup.setToolActive(CrosshairsTool.toolName, {
    bindings: [{ mouseButton: csToolsEnums.MouseBindings.Primary }],
  });
  orthoGroup.setToolActive(WindowLevelTool.toolName, {
    bindings: [{ mouseButton: csToolsEnums.MouseBindings.Primary, modifierKey: csToolsEnums.KeyboardBindings.Meta }],
  });
  orthoGroup.setToolActive(PanTool.toolName, {
    bindings: [
      { mouseButton: csToolsEnums.MouseBindings.Auxiliary },
      { mouseButton: csToolsEnums.MouseBindings.Primary, modifierKey: csToolsEnums.KeyboardBindings.Shift },
    ],
  });
  orthoGroup.setToolActive(ZoomTool.toolName, {
    bindings: [
      { mouseButton: csToolsEnums.MouseBindings.Secondary },
      { mouseButton: csToolsEnums.MouseBindings.Primary, modifierKey: csToolsEnums.KeyboardBindings.Alt },
    ],
  });
  orthoGroup.setToolActive(StackScrollTool.toolName, {
    bindings: [{ mouseButton: csToolsEnums.MouseBindings.Wheel }],
  });
  orthoGroup.setToolEnabled(ReferenceLinesTool.toolName);

  // 3D tools: drag to rotate, shift+drag to pan, alt+drag to zoom
  vol3dGroup.setToolActive(TrackballRotateTool.toolName, {
    bindings: [{ mouseButton: csToolsEnums.MouseBindings.Primary }],
  });
  vol3dGroup.setToolActive(PanTool.toolName, {
    bindings: [{ mouseButton: csToolsEnums.MouseBindings.Primary, modifierKey: csToolsEnums.KeyboardBindings.Shift }],
  });
  vol3dGroup.setToolActive(ZoomTool.toolName, {
    bindings: [
      { mouseButton: csToolsEnums.MouseBindings.Secondary },
      { mouseButton: csToolsEnums.MouseBindings.Primary, modifierKey: csToolsEnums.KeyboardBindings.Alt },
    ],
  });
}
const VOLUME_ID = 'duckn-volume';

// ---- Main ----

async function loadAndDisplay(url) {
  statusEl.textContent = 'Opening store...';

  try {
    const cache = await openDucknStore(url);

    // Generate image IDs
    const imageIds = Array.from({ length: cache.nSlices }, (_, i) =>
      `duckn:${url}#slice=${i}`
    );

    statusEl.textContent = `Loading volume (${cache.nSlices} slices, ${cache.rows}x${cache.columns})...`;

    // Create and cache volume (uses built-in streaming loader via cornerstoneStreamingImageVolume: prefix)
    const volume = await volumeLoader.createAndCacheVolume(
      `cornerstoneStreamingImageVolume:${VOLUME_ID}`, { imageIds }
    );

    const fullVolumeId = `cornerstoneStreamingImageVolume:${VOLUME_ID}`;

    const orthoIds = ['axial', 'sagittal', 'coronal'];

    // Set volume on ortho viewports
    await setVolumesForViewports(
      renderingEngine,
      [{ volumeId: fullVolumeId }],
      orthoIds,
    );

    // Set volume on 3D viewport, then apply preset in the callback
    await setVolumesForViewports(
      renderingEngine,
      [{ volumeId: fullVolumeId }],
      ['vol3d'],
    ).then(() => {
      const vp = renderingEngine.getViewport('vol3d');
      vp.setProperties({ preset: preset3dSelect.value });
      vp.render();
    });

    // Load all slices, then finalize
    const loadComplete = new Promise((resolve) => {
      const handler = () => {
        eventTarget.removeEventListener(EVENTS.IMAGE_VOLUME_LOADING_COMPLETED, handler);
        resolve();
      };
      eventTarget.addEventListener(EVENTS.IMAGE_VOLUME_LOADING_COMPLETED, handler);
    });

    volume.load();
    await loadComplete;

    statusEl.textContent = 'Rendering...';

    // Set up tools now that viewports have volume data
    setupTools();

    // Re-apply 3D preset after all slices loaded
    const vol3dVp = renderingEngine.getViewport('vol3d');
    vol3dVp.setProperties({ preset: preset3dSelect.value });
    vol3dVp.render();

    // Apply 2D window preset if not auto
    if (presetSelect.value !== 'auto') {
      applyPreset(presetSelect.value);
    }

    // Update sidebar
    sidebar.innerHTML = `
      <h2>duckn metadata</h2>
      <span class="label">space:</span> <span class="val">${cache.duckn.space || 'n/a'}</span><br>
      <span class="label">shape:</span> <span class="val">[${cache.nSlices}, ${cache.rows}, ${cache.columns}]</span><br>
      <span class="label">spacing:</span> <span class="val">[${cache.colSpacing.toFixed(3)}, ${cache.rowSpacing.toFixed(3)}, ${cache.sliceSpacing.toFixed(3)}]</span><br>
      <span class="label">slope:</span> <span class="val">${cache.slope}</span><br>
      <span class="label">intercept:</span> <span class="val">${cache.intercept}</span><br>
      <h2>volume</h2>
      <span class="label">slices:</span> <span class="val">${cache.nSlices}</span><br>
      <span class="label">voxels:</span> <span class="val">${(cache.nSlices * cache.rows * cache.columns).toLocaleString()}</span>
    `;

    statusEl.textContent = '';
  } catch (err) {
    statusEl.textContent = err.message;
    statusEl.className = 'error';
    console.error(err);
  }
}

async function setup() {
  // Initialize Cornerstone
  await init();

  // Register metadata provider (highest priority)
  metaData.addProvider(ducknMetadataProvider, 10000);

  // Register duckn image loader
  registerImageLoader('duckn', (imageId) => {
    return { promise: loadDucknImage(imageId) };
  });

  // Init tools BEFORE creating viewports so ELEMENT_ENABLED events are caught
  cornerstoneTools.init();
  cornerstoneTools.addTool(WindowLevelTool);
  cornerstoneTools.addTool(StackScrollTool);
  cornerstoneTools.addTool(ZoomTool);
  cornerstoneTools.addTool(PanTool);
  cornerstoneTools.addTool(CrosshairsTool);
  cornerstoneTools.addTool(ReferenceLinesTool);
  cornerstoneTools.addTool(TrackballRotateTool);

  // Create rendering engine
  renderingEngine = new RenderingEngine('duckn-engine');
  window._re = renderingEngine; // debug

  const viewportInput = [
    {
      viewportId: 'axial',
      type: Enums.ViewportType.ORTHOGRAPHIC,
      element: document.getElementById('vp-axial'),
      defaultOptions: { orientation: Enums.OrientationAxis.AXIAL },
    },
    {
      viewportId: 'sagittal',
      type: Enums.ViewportType.ORTHOGRAPHIC,
      element: document.getElementById('vp-sagittal'),
      defaultOptions: { orientation: Enums.OrientationAxis.SAGITTAL },
    },
    {
      viewportId: 'coronal',
      type: Enums.ViewportType.ORTHOGRAPHIC,
      element: document.getElementById('vp-coronal'),
      defaultOptions: { orientation: Enums.OrientationAxis.CORONAL },
    },
    {
      viewportId: 'vol3d',
      type: Enums.ViewportType.VOLUME_3D,
      element: document.getElementById('vp-3d'),
      defaultOptions: {},
    },
  ];

  renderingEngine.setViewports(viewportInput);

  // Tools already registered in setup(); just need tool group + activation here

  // Slice position overlay
  function updateSliceInfo(vpId) {
    const infoEl = document.getElementById(`info-${vpId}`);
    if (!infoEl) return;
    const vp = renderingEngine.getViewport(vpId);
    if (!vp || !vp.getSliceIndex) return;
    const idx = vp.getSliceIndex();
    const total = vp.getNumberOfSlices?.() ?? '';
    infoEl.textContent = total ? `${idx + 1} / ${total}` : `${idx + 1}`;
  }

  ['axial', 'sagittal', 'coronal'].forEach(vpId => {
    const el = document.getElementById(`vp-${vpId}`);
    el.addEventListener(EVENTS.CAMERA_MODIFIED, () => updateSliceInfo(vpId));
  });

  statusEl.textContent = 'Ready — enter a URL and click Load';
}

setup();

loadBtn.addEventListener('click', () => {
  const url = urlInput.value.trim();
  if (url) loadAndDisplay(url);
});

urlInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') loadBtn.click();
});

function applyPreset(name) {
  if (!renderingEngine || !ducknCache) return;
  const preset = CT_PRESETS[name];
  if (!preset) return;

  // Data is pre-scaled to HU, so VOI range is directly in HU
  const lower = preset.center - preset.width / 2;
  const upper = preset.center + preset.width / 2;

  const orthoIds = ['axial', 'sagittal', 'coronal'];
  orthoIds.forEach(id => {
    const vp = renderingEngine.getViewport(id);
    if (!vp) return;

    if (vp.setProperties) {
      vp.setProperties({ voiRange: { lower, upper } });
    }
  });
  renderingEngine.renderViewports(orthoIds);
}

presetSelect.addEventListener('change', () => applyPreset(presetSelect.value));

let current3dPreset = 'CT-Bone';

function apply3dPreset(name) {
  if (!renderingEngine) return;
  current3dPreset = name;
  const vp = renderingEngine.getViewport('vol3d');
  if (!vp) return;
  vp.setProperties({ preset: name });
  vp.render();
}

preset3dSelect.addEventListener('change', () => apply3dPreset(preset3dSelect.value));
