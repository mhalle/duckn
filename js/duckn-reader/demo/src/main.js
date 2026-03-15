import '@kitware/vtk.js/Rendering/Profiles/Volume';
import '@kitware/vtk.js/Rendering/Profiles/Geometry';

import vtkFullScreenRenderWindow from '@kitware/vtk.js/Rendering/Misc/FullScreenRenderWindow';
import vtkImageData from '@kitware/vtk.js/Common/DataModel/ImageData';
import vtkDataArray from '@kitware/vtk.js/Common/Core/DataArray';
import vtkVolume from '@kitware/vtk.js/Rendering/Core/Volume';
import vtkVolumeMapper from '@kitware/vtk.js/Rendering/Core/VolumeMapper';
import vtkColorTransferFunction from '@kitware/vtk.js/Rendering/Core/ColorTransferFunction';
import vtkPiecewiseFunction from '@kitware/vtk.js/Common/DataModel/PiecewiseFunction';
import vtkImageSlice from '@kitware/vtk.js/Rendering/Core/ImageSlice';
import vtkImageMapper from '@kitware/vtk.js/Rendering/Core/ImageMapper';
import vtkColorMaps from '@kitware/vtk.js/Rendering/Core/ColorTransferFunction/ColorMaps';

import * as zarr from 'zarrita';
import FetchStore from '@zarrita/storage/fetch';
import ZipFileStore from '@zarrita/storage/zip';
import * as nifti from 'nifti-reader-js';
import { niftiToImageData } from './niftiToImageData.js';

// ---- ducknToImageData ----

const SPACE_SIGN_FLIPS = {
  'left-posterior-superior': [1, 1, 1],
  'right-anterior-superior': [-1, -1, 1],
  'left-anterior-superior': [1, -1, 1],
};

function ducknToImageData(data, shape, attrs) {
  const nrrd = attrs.nrrd;
  if (!nrrd) throw new Error('attrs.nrrd is missing');
  const axes = nrrd.axes;
  if (!axes) throw new Error('duckn metadata missing axes');

  const spatialAxes = axes.filter((a) => a.space_direction != null);
  if (spatialAxes.length !== 3) throw new Error(`Expected 3 spatial axes, got ${spatialAxes.length}`);
  if (shape.length !== 3) throw new Error(`Expected 3D shape, got ${shape.length}D`);

  const flip = nrrd.space && SPACE_SIGN_FLIPS[nrrd.space]
    ? SPACE_SIGN_FLIPS[nrrd.space] : [1, 1, 1];

  const rawOrigin = nrrd.space_origin || [0, 0, 0];
  const origin = rawOrigin.map((v, i) => v * flip[i]);

  const flippedDirs = spatialAxes.map((ax) =>
    ax.space_direction.map((v, i) => v * flip[i])
  );
  const vtkDirs = [flippedDirs[2], flippedDirs[1], flippedDirs[0]];

  const spacing = new Array(3);
  const dirCols = new Array(3);
  for (let i = 0; i < 3; i++) {
    const d = vtkDirs[i];
    const mag = Math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2]);
    if (mag === 0) throw new Error(`Zero-length space_direction for axis ${i}`);
    spacing[i] = mag;
    dirCols[i] = [d[0] / mag, d[1] / mag, d[2] / mag];
  }

  const direction = [
    dirCols[0][0], dirCols[0][1], dirCols[0][2],
    dirCols[1][0], dirCols[1][1], dirCols[1][2],
    dirCols[2][0], dirCols[2][1], dirCols[2][2],
  ];

  const dimensions = [shape[2], shape[1], shape[0]];

  const imageData = vtkImageData.newInstance({ origin, spacing });
  imageData.setDimensions(dimensions);
  imageData.setDirection(direction);

  const scalars = vtkDataArray.newInstance({
    name: 'DucknScalars',
    values: data,
    numberOfComponents: 1,
  });
  imageData.getPointData().setScalars(scalars);

  return imageData;
}

// ---- colormaps ----

// vtk.js preset name mapping
const VTK_PRESET_COLORMAPS = {
  viridis: 'Viridis (matplotlib)',
  plasma: 'Plasma (matplotlib)',
  inferno: 'Inferno (matplotlib)',
  magma: 'Magma (matplotlib)',
  coolwarm: 'Cool to Warm',
  rainbow: 'Rainbow Desaturated',
};

// Custom colormaps defined as normalized [t, r, g, b] control points
const CUSTOM_COLORMAPS = {
  grayscale: [
    [0.0, 0.0, 0.0, 0.0],
    [1.0, 1.0, 1.0, 1.0],
  ],
  'ct-bone': [
    [0.0, 0.0, 0.0, 0.0],       // air = black
    [0.2, 0.15, 0.08, 0.05],    // soft tissue dark
    [0.4, 0.5, 0.3, 0.2],       // muscle/organ
    [0.7, 0.85, 0.75, 0.55],    // bone edge
    [1.0, 1.0, 0.95, 0.85],     // dense bone
  ],
  'ct-soft': [
    [0.0, 0.0, 0.0, 0.0],
    [0.15, 0.1, 0.0, 0.0],
    [0.3, 0.5, 0.15, 0.1],
    [0.5, 0.8, 0.5, 0.3],
    [0.7, 0.9, 0.8, 0.6],
    [1.0, 1.0, 1.0, 0.9],
  ],
};

function applyColormap(ctfun, name, range) {
  ctfun.removeAllPoints();
  const [lo, hi] = range;
  const span = hi - lo || 1;

  const presetName = VTK_PRESET_COLORMAPS[name];
  if (presetName) {
    const preset = vtkColorMaps.getPresetByName(presetName);
    if (preset) {
      ctfun.applyColorMap(preset);
      ctfun.setMappingRange(lo, hi);
      ctfun.updateRange();
      return;
    }
  }

  const points = CUSTOM_COLORMAPS[name] || CUSTOM_COLORMAPS.grayscale;
  for (const [t, r, g, b] of points) {
    ctfun.addRGBPoint(lo + t * span, r, g, b);
  }
}

// ---- opacity presets ----

function applyOpacity(ofun, name, range) {
  ofun.removeAllPoints();
  const [lo, hi] = range;
  const span = hi - lo || 1;

  if (name === 'ct-bone' || name === 'ct-soft') {
    // CT-style: transparent air/low density, ramp up for tissue
    const threshold = name === 'ct-soft' ? 0.25 : 0.35;
    ofun.addPoint(lo, 0.0);
    ofun.addPoint(lo + threshold * span, 0.0);
    ofun.addPoint(lo + (threshold + 0.15) * span, 0.15);
    ofun.addPoint(lo + 0.7 * span, 0.4);
    ofun.addPoint(hi, 0.8);
  } else {
    // General purpose: gentle ramp
    ofun.addPoint(lo, 0.0);
    ofun.addPoint(lo + 0.15 * span, 0.0);
    ofun.addPoint(lo + 0.4 * span, 0.3);
    ofun.addPoint(hi, 0.8);
  }
}

// ---- UI ----

const urlInput = document.getElementById('url-input');
const loadBtn = document.getElementById('load-btn');
const viewMode = document.getElementById('view-mode');
const colormapSelect = document.getElementById('colormap');
const sidebar = document.getElementById('sidebar');
const statusEl = document.getElementById('status');
const viewport = document.getElementById('viewport');

let fullScreenRenderer = null;
let currentActor = null;
let currentImageData = null;
let currentRenderWindow = null;

function fmt(arr, prec = 4) {
  return '[' + arr.map((v) => typeof v === 'number' ? v.toFixed(prec) : v).join(', ') + ']';
}

function updateSidebarVtk(imageData, source) {
  const dims = imageData.getDimensions();
  const sp = imageData.getSpacing();
  const orig = imageData.getOrigin();
  const dir = imageData.getDirection();
  const range = imageData.getPointData().getScalars().getRange();

  return `
    <h2>vtk.js output (LPS)</h2>
    <span class="label">source:</span> <span class="val">${source}</span><br>
    <span class="label">dimensions:</span> <span class="val">${fmt(dims, 0)}</span><br>
    <span class="label">spacing:</span> <span class="val">${fmt(sp)}</span><br>
    <span class="label">origin:</span> <span class="val">${fmt(orig)}</span><br>
    <span class="label">direction:</span><br>
    <div class="matrix-row"><span class="val">${fmt(dir.slice(0, 3))}</span></div>
    <div class="matrix-row"><span class="val">${fmt(dir.slice(3, 6))}</span></div>
    <div class="matrix-row"><span class="val">${fmt(dir.slice(6, 9))}</span></div>
    <span class="label">scalar range:</span> <span class="val">[${range[0].toFixed(2)}, ${range[1].toFixed(2)}]</span>
  `;
}

function updateSidebarDuckn(nrrd, shape, imageData) {
  sidebar.innerHTML = `
    <h2>duckn metadata</h2>
    <span class="label">space:</span> <span class="val">${nrrd.space || 'not specified'}</span><br>
    <span class="label">space_origin:</span> <span class="val">${fmt(nrrd.space_origin || [])}</span><br>
    <span class="label">axes:</span><br>
    ${nrrd.axes.map((a, i) =>
      `&nbsp;&nbsp;[${i}] <span class="val">${fmt(a.space_direction)}</span>` +
      (a.kind ? ` <span class="label">(${a.kind})</span>` : '')
    ).join('<br>')}

    <h2>zarr array</h2>
    <span class="label">shape (C-order):</span> <span class="val">[${shape.join(', ')}]</span><br>
    <span class="label">voxels:</span> <span class="val">${imageData.getNumberOfPoints().toLocaleString()}</span>

    ${updateSidebarVtk(imageData, 'duckn')}

    <h2>coordinate conversion</h2>
    <span class="label">from:</span> <span class="val">${nrrd.space || 'unknown'}</span><br>
    <span class="label">to:</span> <span class="val">LPS (VTK convention)</span>
  `;
}

function updateSidebarNifti(header, imageData) {
  const affine = header.affine;
  sidebar.innerHTML = `
    <h2>NIfTI header</h2>
    <span class="label">dims:</span> <span class="val">[${header.dims[1]}, ${header.dims[2]}, ${header.dims[3]}]</span><br>
    <span class="label">pixDims:</span> <span class="val">[${fmt([header.pixDims[1], header.pixDims[2], header.pixDims[3]])}]</span><br>
    <span class="label">datatype:</span> <span class="val">${header.datatypeCode} (${header.numBitsPerVoxel}bit)</span><br>
    <span class="label">sform_code:</span> <span class="val">${header.sform_code}</span><br>
    <span class="label">qform_code:</span> <span class="val">${header.qform_code}</span><br>
    <span class="label">description:</span> <span class="val">${header.description || '—'}</span><br>
    <span class="label">affine (RAS):</span><br>
    ${affine.map((row) =>
      `<div class="matrix-row"><span class="val">${fmt(row)}</span></div>`
    ).join('')}

    ${updateSidebarVtk(imageData, 'NIfTI')}

    <h2>coordinate conversion</h2>
    <span class="label">from:</span> <span class="val">RAS (NIfTI convention)</span><br>
    <span class="label">to:</span> <span class="val">LPS (VTK convention)</span>
  `;
}

function render(imageData) {
  currentImageData = imageData;

  // Clean up previous renderer
  if (fullScreenRenderer) {
    fullScreenRenderer.getInteractor().delete();
    fullScreenRenderer.delete();
  }

  fullScreenRenderer = vtkFullScreenRenderWindow.newInstance({
    rootContainer: viewport,
    containerStyle: { height: '100%', width: '100%', position: 'absolute', top: 0, left: 0 },
    background: [0.1, 0.1, 0.15],
  });

  const renderer = fullScreenRenderer.getRenderer();
  currentRenderWindow = fullScreenRenderer.getRenderWindow();
  const range = imageData.getPointData().getScalars().getRange();
  const sp = imageData.getSpacing();
  const cmapName = colormapSelect.value;

  if (viewMode.value === 'volume') {
    const mapper = vtkVolumeMapper.newInstance();
    mapper.setInputData(imageData);
    mapper.setMaximumSamplesPerRay(4000);
    const minSpacing = Math.min(...sp);
    mapper.setSampleDistance(minSpacing);

    const ctfun = vtkColorTransferFunction.newInstance();
    applyColormap(ctfun, cmapName, range);

    const ofun = vtkPiecewiseFunction.newInstance();
    applyOpacity(ofun, cmapName, range);

    const actor = vtkVolume.newInstance();
    actor.setMapper(mapper);
    actor.getProperty().setRGBTransferFunction(0, ctfun);
    actor.getProperty().setScalarOpacity(0, ofun);
    actor.getProperty().setInterpolationTypeToLinear();

    currentActor = actor;
    renderer.addVolume(actor);
  } else {
    const dims = imageData.getDimensions();
    const mapper = vtkImageMapper.newInstance();
    mapper.setInputData(imageData);
    mapper.setSlicingMode(2);
    mapper.setSlice(Math.floor(dims[2] / 2));

    const actor = vtkImageSlice.newInstance();
    actor.setMapper(mapper);
    actor.getProperty().setColorWindow(range[1] - range[0]);
    actor.getProperty().setColorLevel((range[0] + range[1]) / 2);

    currentActor = actor;
    renderer.addActor(actor);
  }

  renderer.resetCamera();
  currentRenderWindow.render();
}

async function loadStore(url) {
  statusEl.textContent = 'Loading...';
  statusEl.className = '';
  sidebar.innerHTML = '';

  try {
    const fullUrl = new URL(url, window.location.origin).href;
    const store = fullUrl.endsWith('.zip')
      ? await ZipFileStore.fromUrl(fullUrl)
      : new FetchStore(fullUrl);
    const arr = await zarr.open(store, { kind: 'array' });
    const result = await zarr.get(arr);

    statusEl.textContent = 'Converting...';
    const imageData = ducknToImageData(result.data, result.shape, arr.attrs);

    updateSidebarDuckn(arr.attrs.nrrd, result.shape, imageData);
    render(imageData);
    statusEl.textContent = '';
  } catch (err) {
    statusEl.textContent = err.message;
    statusEl.className = 'error';
    console.error(err);
  }
}

loadBtn.addEventListener('click', () => {
  const url = urlInput.value.trim();
  if (url) loadStore(url);
});

urlInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') loadBtn.click();
});

// Re-render when colormap or view mode changes
colormapSelect.addEventListener('change', () => {
  if (currentImageData) render(currentImageData);
});

viewMode.addEventListener('change', () => {
  if (currentImageData) render(currentImageData);
});

// NIfTI file loading
const niftiInput = document.getElementById('nifti-file');
niftiInput.addEventListener('change', async (e) => {
  const file = e.target.files[0];
  if (!file) return;

  statusEl.textContent = `Loading ${file.name}...`;
  statusEl.className = '';
  sidebar.innerHTML = '';

  try {
    let buf = await file.arrayBuffer();
    if (nifti.isCompressed(buf)) {
      statusEl.textContent = 'Decompressing...';
      buf = nifti.decompress(buf);
    }
    if (!nifti.isNIFTI(buf)) {
      throw new Error('Not a valid NIfTI file');
    }
    const header = nifti.readHeader(buf);
    const imageBuffer = nifti.readImage(header, buf);

    statusEl.textContent = 'Converting...';
    const imageData = niftiToImageData(header, imageBuffer);

    updateSidebarNifti(header, imageData);
    render(imageData);
    statusEl.textContent = '';
  } catch (err) {
    statusEl.textContent = err.message;
    statusEl.className = 'error';
    console.error(err);
  }
});

// Auto-load on start
loadStore(urlInput.value);
