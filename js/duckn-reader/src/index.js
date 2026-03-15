import macro from '@kitware/vtk.js/macros';
import * as zarr from 'zarrita';
import FetchStore from '@zarrita/storage/fetch';

import { ducknToImageData } from './ducknToImageData.js';

function vtkDucknReader(publicAPI, model) {
  model.classHierarchy.push('vtkDucknReader');

  publicAPI.setUrl = (url) => {
    model.url = url;
    return publicAPI.loadData();
  };

  publicAPI.loadData = async () => {
    if (!model.url) {
      throw new Error('No URL set on vtkDucknReader');
    }
    const store = new FetchStore(model.url);
    const arr = await zarr.open(store, { kind: 'array' });
    const result = await zarr.get(arr);
    const imageData = ducknToImageData(result.data, result.shape, arr.attrs, {
      scalarArrayName: model.arrayName,
    });
    model.output[0] = imageData;
    publicAPI.modified();
  };

  publicAPI.requestData = () => {
    publicAPI.loadData();
  };
}

const DEFAULT_VALUES = {
  url: '',
  arrayName: 'DucknScalars',
};

export function extend(publicAPI, model, initialValues = {}) {
  Object.assign(model, DEFAULT_VALUES, initialValues);
  macro.obj(publicAPI, model);
  macro.algo(publicAPI, model, 0, 1);
  macro.setGet(publicAPI, model, ['url', 'arrayName']);
  vtkDucknReader(publicAPI, model);
}

export const newInstance = macro.newInstance(extend, 'vtkDucknReader');
export default { newInstance, extend };
export { ducknToImageData } from './ducknToImageData.js';
