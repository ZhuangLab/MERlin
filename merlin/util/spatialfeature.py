from abc import abstractmethod
import numpy as np
import uuid
import cv2
from skimage import measure
from typing import List
from typing import Tuple
from typing import Dict
from shapely import geometry
import h5py
import pandas
import merlin

from merlin.core import dataset


class SpatialFeature(object):

    """
    A spatial feature is a collection of contiguous voxels.
    """

    def __init__(self, boundaryList: List[List[geometry.Polygon]], fov: int,
                 zCoordinates: np.ndarray=None, uniqueID: int=None,
                 label: int=-1) -> None:
        """Create a new feature specified by a list of pixels

        Args:
            boundaryList: a least of boundaries that define this feature.
                The first index of the list corresponds with the z index.
                The second index corresponds with the index of the shape since
                some regions might split in some z indexes.
            fov: the index of the field of view that this feature belongs to.
                The pixel list specifies pixel in the local fov reference
                frame.
            zCoordinates: the z position for each of the z indexes. If not
                specified, each z index is assumed to have unit height.
        """
        self._boundaryList = boundaryList
        self._fov = fov

        if uniqueID is None:
            self._uniqueID = uuid.uuid4().int
        else:
            self._uniqueID = uniqueID

        if zCoordinates is not None:
            self._zCoordinates = zCoordinates
        else:
            self._zCoordinates = np.arange(len(boundaryList))

    @staticmethod
    def feature_from_label_matrix(labelMatrix: np.ndarray, fov: int,
                                  transformationMatrix: np.ndarray=None,
                                  zCoordinates: np.ndarray=None,
                                  label: int=-1):
        """Generate a new feature from the specified label matrix.

        Args:
            labelMatrix: a 3d matrix indicating the z, x, y position
                of voxels that contain the feature. Voxels corresponding
                to the feature have a value of True while voxels outside of the
                feature should have a value of False.
            fov: the index of the field of view corresponding to the
                label matrix.
            transformationMatrix: a 3x3 numpy array specifying the
                transformation from fov to global coordinates. If None,
                the feature coordinates are not transformed.
            zCoordinates: the z position for each of the z indexes. If not
                specified, each z index is assumed to have unit height.
        Returns: the new feature
        """

        boundaries = [SpatialFeature._extract_boundaries(x)
                      for x in labelMatrix]

        if transformationMatrix is not None:
            boundaries = [SpatialFeature._transform_boundaries(
                x, transformationMatrix) for x in boundaries]

        return SpatialFeature([SpatialFeature._remove_interior_boundaries(
            [geometry.Polygon(x) for x in b if len(x) > 2])
                               for b in boundaries], fov, zCoordinates)

    @staticmethod
    def _extract_boundaries(labelMatrix: np.ndarray) -> List[np.ndarray]:
        """Determine the boundaries of the feature indicated in the
        label matrix.

        Args:
            labelMatrix: a 2 dimensional numpy array indicating the x, y
                position of pixels that contain the feature.
        Returns: a list of n x 2 numpy arrays indicating the x, y coordinates
            of the boundaries where n is the number of boundary coordinates
        """
        boundaries = measure.find_contours(np.transpose(labelMatrix), 0.9,
                                           fully_connected='high')
        return boundaries

    @staticmethod
    def _transform_boundaries(
            boundaries: List[np.ndarray],
            transformationMatrix: np.ndarray) -> List[np.ndarray]:

        transformedList = []
        for b in boundaries:
            reshapedBoundaries = np.reshape(
                b, (1, b.shape[0], 2)).astype(np.float)
            transformedBoundaries = cv2.transform(
                reshapedBoundaries, transformationMatrix)[0, :, :2]
            transformedList.append(transformedBoundaries)

        return transformedList

    @staticmethod
    def _remove_interior_boundaries(
            inPolygons: List[geometry.Polygon]) -> List[geometry.Polygon]:
        goodPolygons = []

        for p in inPolygons:
            if not any([pTest.contains(p)
                        for pTest in inPolygons if p != pTest]):
                goodPolygons.append(p)

        return goodPolygons

    def get_fov(self) -> int:
        return self._fov

    def get_boundaries(self) -> List[List[geometry.Polygon]]:
        return self._boundaryList

    def get_feature_id(self) -> int:
        return self._uniqueID

    def get_z_coordinates(self) -> np.ndarray:
        return self._zCoordinates

    def get_bounding_box(self) -> Tuple[float, float, float, float]:
        """Get the 2d box that contains all boundaries in all z plans of this
        feature.

        Returns:
            a tuple containing (x1, y1, x2, y2) coordinates of the bounding box
        """
        boundarySet = []
        for f in self.get_boundaries():
            for b in f:
                boundarySet.append(b)

        multiPolygon = geometry.MultiPolygon(boundarySet)
        return multiPolygon.bounds

    def get_volume(self) -> float:
        """Get the volume enclosed by this feature.

        Returns:
            the volume represented in global coordinates. If only one z
            slice is present for the feature, the z height is taken as 1.
        """
        boundaries = self.get_boundaries()
        totalVolume = 0

        if len(boundaries) > 1:
            for b, deltaZ in zip(boundaries[:1], np.diff(self._zCoordinates)):
                totalVolume += deltaZ*np.sum([x.area for x in b])
        else:
            totalVolume = np.sum([x.area for x in boundaries[0]])

        return totalVolume

    def is_contained_within_boundary(self, inFeature) -> bool:
        """Determine if any part of this feature is contained within the
        boundary of the specified feature.

        Args:
            inFeature: the feature whose boundary should be checked whether
                it contains this feature
        Returns:
            True if inFeature contains pixels that are within inFeature,
                otherwise False. This returns false if inFeature only shares
                a boundary with this feature.
        """
        if all([b1.disjoint(b2) for b1List, b2List in zip(
                    self.get_boundaries(), inFeature.get_boundaries())
                for b1 in b1List for b2 in b2List]):
            return False

        for b1List, b2List in zip(
                self.get_boundaries(), inFeature.get_boundaries()):
            for b1 in b1List:
                for b2 in b2List:
                    x, y = b1.exterior.coords.xy
                    for p in zip(x, y):
                        if geometry.Point(p).within(b2):
                            return True

        return False

    def equals(self, testFeature) -> bool:
        """Determine if this feature is equivalent to testFeature

        Args:
            testFeature: the feature to test equivalency
        Returns:
            True if this feature and testFeature are equivalent, otherwise
                false
        """
        if self.get_fov() != testFeature.get_fov():
            return False
        if self.get_feature_id() != testFeature.get_feature_id():
            return False
        if not np.array_equal(self.get_z_coordinates(),
                              testFeature.get_z_coordinates()):
            return False

        if len(self.get_boundaries()) != len(testFeature.get_boundaries()):
            return False
        for b, bIn in zip(self.get_boundaries(), testFeature.get_boundaries()):
            if len(b) != len(bIn):
                return False
            for x, y in zip(b, bIn):
                if not x.equals(y):
                    return False

        return True

    def to_json_dict(self) -> Dict:
        return {
            'fov': self._fov,
            'id': self._uniqueID,
            'z_coordinates': self._zCoordinates.tolist(),
            'boundaries': [[geometry.mapping(y) for y in x]
                           for x in self.get_boundaries()]
        }

    @staticmethod
    def from_json_dict(jsonIn: Dict):
        boundaries = [[geometry.shape(y) for y in x]
                      for x in jsonIn['boundaries']]

        return SpatialFeature(boundaries,
                              jsonIn['fov'],
                              np.array(jsonIn['z_coordinates']),
                              jsonIn['id'])


class SpatialFeatureDB(object):

    """A database for storing spatial features."""

    def __init__(self, dataSet, analysisTask):
        self._dataSet = dataSet
        self._analysisTask = analysisTask

    @abstractmethod
    def write_features(self, features: List[SpatialFeature], fov=None) -> None:
        """Write the features into this database.

        If features already exist in the database with feature IDs equal to
        those in the provided list, an exception is raised.

        Args:
            features: a list of features
            fov: the fov of the features if all feature correspond to the same
                fov. If the features correspond to different fovs, fov
                should be None
        """
        pass

    @abstractmethod
    def read_features(self, fov: int=None) -> List[SpatialFeature]:
        """Read the features in this database

        Args:
            fov: if not None, only the features associated with the specified
                fov are returned
        """
        pass

    @abstractmethod
    def empty_database(self, fov: int=None) -> None:
        """Remove all features from this database.

        Args:
            fov: index of the field of view. If specified, only features
                corresponding to the specified fov will be removed.
                Otherwise all barcodes will be removed.
        """
        pass


class HDF5SpatialFeatureDB(SpatialFeatureDB):

    """
    A data store for spatial features that uses a HDF5 file to store the feature
    information.
    """

    def __init__(self, dataSet: dataset.DataSet, analysisTask):
        super().__init__(dataSet, analysisTask)

    @staticmethod
    def _save_geometry_to_hdf5_group(h5Group: h5py.Group,
                                     polygon: geometry.Polygon) -> None:
        geometryDict = geometry.mapping(polygon)
        h5Group.attrs['type'] = np.string_(geometryDict['type'])
        h5Group['coordinates'] = np.array(geometryDict['coordinates'])

    @staticmethod
    def _save_feature_to_hdf5_group(h5Group: h5py.Group,
                                    feature: SpatialFeature) -> None:
        featureKey = str(feature.get_feature_id())
        featureGroup = h5Group.create_group(featureKey)
        featureGroup.attrs['id'] = np.string_(feature.get_feature_id())
        featureGroup.attrs['fov'] = feature.get_fov()
        featureGroup.attrs['bounding_box'] = \
            np.array(feature.get_bounding_box())
        featureGroup.attrs['volume'] = feature.get_volume()
        featureGroup['z_coordinates'] = feature.get_z_coordinates()

        for i, bSet in enumerate(feature.get_boundaries()):
            zBoundaryGroup = featureGroup.create_group('zIndex_' + str(i))
            for j, b in enumerate(bSet):
                geometryGroup = zBoundaryGroup.create_group('p_' + str(j))
                HDF5SpatialFeatureDB._save_geometry_to_hdf5_group(
                    geometryGroup, b)

    @staticmethod
    def _load_geometry_from_hdf5_group(h5Group: h5py.Group):
        geometryDict = {'type': h5Group.attrs['type'].decode(),
                        'coordinates': np.array(h5Group['coordinates'])}

        return geometry.shape(geometryDict)

    @staticmethod
    def _load_feature_from_hdf5_group(h5Group):
        zCount = len([x for x in h5Group.keys() if x.startswith('zIndex_')])
        boundaryList = []
        for z in range(zCount):
            zBoundaryList = []
            zGroup = h5Group['zIndex_' + str(z)]
            pCount = len([x for x in zGroup.keys() if x[:2] == 'p_'])
            for p in range(pCount):
                zBoundaryList.append(
                    HDF5SpatialFeatureDB._load_geometry_from_hdf5_group(
                        zGroup['p_' + str(p)]))
            boundaryList.append(zBoundaryList)

        loadedFeature = SpatialFeature(
            boundaryList,
            h5Group.attrs['fov'],
            np.array(h5Group['z_coordinates']),
            int(h5Group.attrs['id']))

        return loadedFeature

    def write_features(self, features: List[SpatialFeature], fov=None) -> None:
        if fov is None:
            uniqueFOVs = np.unique([f.get_fov() for f in features])
            for currentFOV in uniqueFOVs:
                currentFeatures = [f for f in features
                                   if f.get_fov() == currentFOV]
                self.write_features(currentFeatures, currentFOV)

        with self._dataSet.open_hdf5_file('a', 'feature_data',
                                          self._analysisTask, fov, 'features') \
                as f:
            featureGroup = f.require_group('featuredata')
            featureGroup.attrs['version'] = merlin.version()
            for currentFeature in features:
                self._save_feature_to_hdf5_group(featureGroup, currentFeature)

    def read_features(self, fov: int=None) -> List[SpatialFeature]:
        if fov is None:
            featureList = []
            for f in self._dataSet.get_fovs():
                featureList += self.read_features(f)
            return featureList

        featureList = []
        try:
            with self._dataSet.open_hdf5_file('r', 'feature_data',
                                              self._analysisTask, fov,
                                              'features') as f:
                featureGroup = f.require_group('featuredata')
                for k in featureGroup.keys():
                    featureList.append(
                        self._load_feature_from_hdf5_group(featureGroup[k]))
        except FileNotFoundError:
            pass

        return featureList

    def empty_database(self, fov: int=None) -> None:
        if fov is None:
            for f in self._dataSet.get_fovs():
                self.empty_database(f)

        self._dataSet.delete_hdf5_file('feature_data', self._analysisTask,
                                       fov, 'features')


class JSONSpatialFeatureDB(SpatialFeatureDB):

    """
    A database for storing spatial features with json serialization.
    """

    def __init__(self, dataSet: dataset.DataSet, analysisTask):
        super().__init__(dataSet, analysisTask)

    def write_features(self, features: List[SpatialFeature], fov=None) -> None:
        if fov is None:
            raise NotImplementedError

        try:
            existingFeatures = [SpatialFeature.from_json_dict(x)
                                for x in self._dataSet.load_json_analysis_result(
                    'feature_data', self._analysisTask, fov, 'features')]

            existingIDs = set([x.get_feature_id() for x in existingFeatures])

            for f in features:
                if f.get_feature_id() not in existingIDs:
                    existingFeatures.append(f)

            featuresAsJSON = [f.to_json_dict() for f in existingFeatures]

        except FileNotFoundError:
            featuresAsJSON = [f.to_json_dict() for f in features]

        self._dataSet.save_json_analysis_result(
            featuresAsJSON, 'feature_data', self._analysisTask,
            fov, 'features')

    def read_features(self, fov: int=None) -> List[SpatialFeature]:
        if fov is None:
            raise NotImplementedError

        features = [SpatialFeature.from_json_dict(x)
                    for x in self._dataSet.load_json_analysis_result(
                'feature_metadata', self._analysisTask, fov, 'features')]

        return features

    def empty_database(self, fov: int=None) -> None:
        pass

    @staticmethod
    def _extract_feature_metadata(feature: SpatialFeature) -> Dict:
        boundingBox = feature.get_bounding_box()
        return {'fov': feature.get_fov(),
                'featureID': feature.get_feature_id(),
                'bounds_x1': boundingBox[0],
                'bounds_y1': boundingBox[1],
                'bounds_x2': boundingBox[2],
                'bounds_y2': boundingBox[3],
                'volume': feature.get_volume()}