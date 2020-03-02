# Copyright 2020 MONAI Consortium
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
A collection of dictionary-based wrappers around the "vanilla" transforms
defined in `monai.transforms.transforms`.
"""

from collections.abc import Hashable

import monai
from monai.transforms.compose import Randomizable, Transform
from monai.transforms.transforms import Rotate90, SpatialCrop
from monai.utils.misc import ensure_tuple
from monai.transforms.utils import generate_pos_neg_label_crop_centers

export = monai.utils.export("monai.transforms")


class MapTransform(Transform):
    """
    A subclass of ``monai.transforms.compose.Transform`` with an assumption
    that the ``data`` input of ``self.__call__`` is a MutableMapping such as ``dict``.

    The ``keys`` parameter will be used to get and set the actual data
    item to transform.  That is, the callable of this transform should
    follow the pattern:
    ```
        def __call__(self, data):
            for key in self.keys:
                if key in data:
                    update output data with some_transform_function(data[key]).
                else:
                    do nothing or some exceptions handling.
            return data
    ```
    """

    def __init__(self, keys):
        self.keys = ensure_tuple(keys)
        if not self.keys:
            raise ValueError('keys unspecified')
        for key in self.keys:
            if not isinstance(key, Hashable):
                raise ValueError('keys should be a hashable or a sequence of hashables, got {}'.format(type(key)))


@export
class Rotate90d(MapTransform):
    """
    dictionary-based wrapper of Rotate90.
    """

    def __init__(self, keys, k=1, axes=(1, 2)):
        """
        Args:
            k (int): number of times to rotate by 90 degrees.
            axes (2 ints): defines the plane to rotate with 2 axes.
        """
        MapTransform.__init__(self, keys)
        self.k = k
        self.plane_axes = axes

        self.rotator = Rotate90(self.k, self.plane_axes)

    def __call__(self, data):
        d = dict(data)
        for key in self.keys:
            d[key] = self.rotator(d[key])
        return d


@export
class RandRotate90d(Randomizable, MapTransform):
    """
    With probability `prob`, input arrays are rotated by 90 degrees
    in the plane specified by `axes`.
    """

    def __init__(self, keys, prob=0.1, max_k=3, axes=(1, 2)):
        """
        Args:
            keys (hashable items): keys of the corresponding items to be transformed.
                See also: monai.transform.composables.MapTransform
            prob (float): probability of rotating.
                (Default 0.1, with 10% probability it returns a rotated array.)
            max_k (int): number of rotations will be sampled from `np.random.randint(max_k) + 1`.
                (Default 3)
            axes (2 ints): defines the plane to rotate with 2 axes.
                (Default to (1, 2))
        """
        MapTransform.__init__(self, keys)

        self.prob = min(max(prob, 0.0), 1.0)
        self.max_k = max_k
        self.axes = axes

        self._do_transform = False
        self._rand_k = 0

    def randomise(self):
        self._rand_k = self.R.randint(self.max_k) + 1
        self._do_transform = self.R.random() < self.prob

    def __call__(self, data):
        self.randomise()
        if not self._do_transform:
            return data

        rotator = Rotate90(self._rand_k, self.axes)
        d = dict(data)
        for key in self.keys:
            d[key] = rotator(d[key])
        return d


@export
class RandCropByPosNegLabeld(Randomizable, MapTransform):
    """
    Crop random fixed sized regions with the center being a foreground or background voxel
    based on the Pos Neg Ratio.
    And will return a list of dictionaries for all the cropped images.

    Args:
        keys (list): parameter will be used to get and set the actual data item to transform.
        label_key (str): name of key for label image, this will be used for finding foreground/background.
        size (list, tuple): the size of the crop region e.g. [224,224,128]
        pos (int, float): used to calculate the ratio ``pos / (pos + neg)`` for the probability to pick a
          foreground voxel as a center rather than a background voxel.
        neg (int, float): used to calculate the ratio ``pos / (pos + neg)`` for the probability to pick a
          foreground voxel as a center rather than a background voxel.
        num_samples (int): number of samples (crop regions) to take in each list.
    """

    def __init__(self, keys, label_key, size, pos=1, neg=1, num_samples=1):
        MapTransform.__init__(self, keys)
        assert isinstance(label_key, str), 'label_key must be a string.'
        assert isinstance(size, (list, tuple)), 'size must be list or tuple.'
        assert all(isinstance(x, int) and x > 0 for x in size), 'all elements of size must be positive integers.'
        assert float(pos) >= 0 and float(neg) >= 0, "pos and neg must be greater than or equal to 0."
        assert float(pos) + float(neg) > 0, "pos and neg cannot both be 0."
        assert isinstance(num_samples, int), \
            "invalid samples number: {}. num_samples must be an integer.".format(num_samples)
        assert num_samples >= 0, 'num_samples must be greater than or equal to 0.'
        self.label_key = label_key
        self.size = size
        self.pos_ratio = float(pos) / (float(pos) + float(neg))
        self.num_samples = num_samples
        self.centers = None

    def randomise(self, label):
        self.centers = generate_pos_neg_label_crop_centers(label, self.size, self.num_samples, self.pos_ratio, self.R)

    def __call__(self, data):
        d = dict(data)
        label = d[self.label_key]
        self.randomise(label)
        results = [dict()] * self.num_samples
        for key in data.keys():
            if key in self.keys:
                img = d[key]
                image_samples = list()
                for i, center in enumerate(self.centers):
                    cropper = SpatialCrop(roi_center=tuple(center), roi_size=self.size)
                    results[i][key] = cropper(img)
            else:
                for i in range(self.num_samples):
                    results[i][key] = data[key]

        # also need to crop label field
        for i, center in enumerate(self.centers):
            cropper = SpatialCrop(roi_center=tuple(center), roi_size=self.size)
            results[i][self.label_key] = cropper(label)
        return results


# if __name__ == "__main__":
#     import numpy as np
#     data = {
#         'img': np.array((1, 2, 3, 4)).reshape((1, 2, 2)),
#         'seg': np.array((1, 2, 3, 4)).reshape((1, 2, 2)),
#         'affine': 3,
#         'dtype': 4,
#         'unused': 5,
#     }
#     rotator = RandRotate90d(keys=['img', 'seg'], prob=0.8)
#     # rotator.set_random_state(1234)
#     data_result = rotator(data)
#     print(data_result.keys())
#     print(data_result['img'], data_result['seg'])
