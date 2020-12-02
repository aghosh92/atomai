from typing import Dict, Tuple, Type, Union

import numpy as np
import torch

from ..utils import (get_downsample_factor, torch_format_image,
                     torch_format_spectra)
from .predictor import BasePredictor


class EnsemblePredictor(BasePredictor):

    def __init__(self,
                 skeleton: Type[torch.nn.Module],
                 ensemble: Dict[int, Dict[str, torch.Tensor]],
                 data_type: str = "image",
                 output_type: str = "image",
                 nb_classes: int = None,
                 in_dim: Tuple[int] = None,
                 out_dim: Tuple[int] = None,
                 **kwargs: Union[str, Tuple[int]]) -> None:
        """
        Initialize ensemble predictor
        """
        super(EnsemblePredictor, self).__super__()
        if output_type not in ["image", "spectra"]:
            raise TypeError("Supported output types are 'image' and 'spectra'")
        inout = [data_type, output_type]
        inout_d = not all(in_dim, out_dim)
        if inout in (["image", "spectra"], ["spectra", "image"]) and inout_d:
            raise TypeError(
                "Specify input (in_dim) & output (out_dim) dimensions")
        self.device = "cpu"
        if kwargs.get("use_gpu", True) and torch.cuda.is_available():
            if kwargs.get("device") is None:
                self.device = "cuda"
            else:
                self.device = kwargs.get("device")
        self.model = skeleton
        self.ensemble = ensemble
        self.data_type = data_type
        self.output_type = output_type
        self.nb_classes = nb_classes
        self.in_dim, self.out_dim = in_dim, out_dim
        self.downsample_factor = None
        self.output_shape = kwargs.get("output_shape")
        verbose = kwargs.get("verbose", 1)
        if verbose:
            self.everbose = True
            self.verbose = True if verbose > 1 else False

    def _set_output_shape(self, data: np.ndarray) -> None:
        """
        Sets output shape
        """
        if self.data_type == self.output_type == "image":
            if self.nb_classes:  # semantic segmentation
                out_shape = (*data.shape, self.nb_classes)
            else:  # image cleaning
                out_shape = (*data.shape)
        elif self.data_type == "spectra" and self.output_type == "image":
            if self.nb_classes:
                out_shape = (len(data), *self.out_dim, self.nb_classes)
            else:
                out_shape = (len(data), *self.out_dim)
        elif self.data_type == "image" and self.output_type == "spectra":
            out_shape = (len(data), *self.out_dim)
        elif self.data_type == self.output_type == "spectra":
            out_shape = (*data.shape)
        else:
            raise TypeError("Data not understood")

        self.output_shape = out_shape

    def preprocess_data(self,
                        data: np.ndarray,
                        ) -> torch.Tensor:
        """
        Preprocesses data depending on type (image or spectra)
        """
        if self.data_type == "image":
            if data.ndim == 2:
                data = data[np.newaxis, ...]
            data = torch_format_image(data)
        elif self.data_type == "spectra":
            if data.ndim == 1:
                data = data[np.newaxis, ...]
            data = torch_format_spectra(data)
        else:
            data = self.preprocess(data)
        return data

    def ensemble_forward_(self,
                          data: torch.Tensor,
                          out_shape: Tuple[int]
                          ) -> Tuple[np.ndarray]:
        """
        Computes mean and variance of prediction with ensemble models
        """
        eprediction = np.zeros(
            (len(self.ensemble), *out_shape))
        for i, m in enumerate(self.ensemble.values()):
            self.model.load_state_dict(m)
            self._model2device()
            eprediction[i] = self.forward_(data).cpu().numpy()
        return np.mean(eprediction, axis=0), np.var(eprediction, axis=0) 
    
    def ensemble_batch_predict(self,
                               data: np.ndarray,
                               num_batches: int = 10
                               ) -> Tuple[np.ndarray]:
        """
        Batch-by-batch prediction with ensemble models
        """
        batch_size = len(data) // num_batches
        if batch_size < 1:
            num_batches = batch_size = 1
        prediction_mean = np.zeros(shape=self.output_shape)
        prediction_var = np.zeros(shape=self.output_shape)
        for i in range(num_batches):
            if self.everbose:
                print("\rBatch {}/{}".format(i+1, num_batches), end="")
            data_i = data[i*batch_size:(i+1)*batch_size]
            pred_mean, pred_var = self.ensemble_forward_(
                data_i, (batch_size, *self.output_shape[1:]))
            prediction_mean[i*batch_size:(i+1)*batch_size] = pred_mean
            prediction_var[i*batch_size:(i+1)*batch_size] = pred_var
        data_i = data[(i+1)*batch_size:]
        if len(data_i) > 0:
            pred_mean, pred_var = self.ensemble_forward_(
                data_i, (len(data_i, *self.output_shape[1:])))
            prediction_mean[(i+1)*batch_size:] = pred_mean
            prediction_var[(i+1)*batch_size:] = pred_var
        return prediction_mean, prediction_var
  
    def ensemble_predict(self,
                         data: np.ndarray,
                         num_batches: int = 10,
                         ) -> Tuple[np.ndarray]:
        """
        Predict mean and variance for all the data points
        with ensemble of models
        """
        if not self.output_shape:
            self._set_output_shape(data)
        data = self.preprocess(data)

        if (self.data_type == self.output_type == "image"
           and self.downsample_factor is None):
            self.downsample_factor = get_downsample_factor(self.model)
        
        prediction = self.ensemble_batch_predict(data, num_batches)
        prediction_mean, prediction_var = prediction
        
        return prediction_mean, prediction_var 
