from pathlib import Path
from typing import List, Optional, Tuple, Union

import numpy as np
import pandas as pd


class MetrLaIo:
    """A class that encapsulates i/o operations related to the Metr-LA dataset.

    Args:
        n_readings: The number of readings in the dataset (not to be confunded
            with the dataset length).
        n_previous_steps: The number of previous time steps to consider when
            building the predictor variable.
        n_future_steps: The number of next time steps to consdier when
            building the target variable.
        normalized_k:  The threshold for constructing the adjacency matrix
            based on the thresholded Gaussian kernel.
    """
    def __init__(self, n_readings: int, n_previous_steps: int,
                 n_future_steps: int, normalized_k: float = .1) -> None:

        self.n_readings = n_readings
        self.n_previous_steps = n_previous_steps
        self.n_future_steps = n_future_steps

        self.normalized_k = normalized_k

        self.x: Optional[np.ndarray] = None
        self.y: Optional[np.ndarray] = None
        self.adjacency_matrix: Optional[np.ndarray] = None

        self.previous_offsets = np.arange(start=-self.n_previous_steps + 1,
                                          stop=1, step=1)
        self.future_offsets = np.arange(start=1, stop=self.n_future_steps + 1,
                                        step=1)

    @property
    def min_t(self):
        """The minimum time step so that accessing the element of
        index min_t-n_previous_steps does not err."""
        return abs(min(self.previous_offsets))

    @property
    def max_t(self):
        """The maximum time step so that accessing the elemnt of
        index max_t+n_future_steps does not err"""
        return abs(self.n_readings - abs(max(self.future_offsets)))

    @property
    def dataset_len(self):
        return self.max_t - self.min_t - 1

    @property
    def data(self):
        return self.x, self.y

    def get_metrla_data(self, data_path: str) -> Tuple[np.ndarray, np.ndarray]:
        """

        Load the MetrLA data.

        The returned values X (features/predictors/previous steps) and
        Y (target/next steps) are of shapes:
        X(n_intervals, n_previous_steps, n_nodes=207, n_features=1)
        Y(n_intervals, n_next_steps, n_nodes=207, n_features=1)

        Args:
            data_path: The path where the readings data is stored.

        Returns:
            A tuple containing the X and Y tensors.
        """

        if not features:
            features = ["features", "interval_of_day", "day_of_week"]

        data_df = pd.read_csv(filepath_or_buffer=data_path, index_col=0)
        _, n_nodes = data_df.shape

        data = {}

        if "features" in features:
            # Range of features is 0-100, so half precision (float16) is ok.
            dataset_features = np.expand_dims(a=data_df.values,
                                              axis=-1).astype(np.float16)

            data["features"] = dataset_features

        if "hour_of_day" in features:
            # Range of values is 0-23, so half precision (short) is ok.
            hour_of_day = ((data_df.index.values.astype("datetime64") -
                            data_df.index.values.astype("datetime64[D]")) / 3600) \
                              .astype(int) % 24
            hour_of_day = np.tile(hour_of_day, [1, n_nodes, 1]).transpose(
                (2, 1, 0)).astype(np.short)

            data["hour_of_day"] = hour_of_day

        if "day_of_week" in features:
            day_of_week = data_df.index.astype("datetime64[ns]").dayofweek
            day_of_week = np.tile(day_of_week, [1, n_nodes, 1]).transpose(
                (2, 1, 0)).astype(np.short)

            data["day_of_week"] = day_of_week

        if "interval_of_day" in features:
            interval_of_day = ((data_df.index.values.astype("datetime64") -
                                data_df.index.values.astype("datetime64[D]")) / 720) \
                                  .astype(int) % 288
            interval_of_day = np.tile(interval_of_day, [1, n_nodes, 1]).transpose(
                (2, 1, 0)).astype(np.short)
            data["interval_of_day"] = interval_of_day

        x, y = {}, {}

        indices_range = range(self.min_t, self.max_t)

        for key, value in data.items():
            x[key] = np.stack([np.swapaxes(value[t + self.previous_offsets, ...], 0, 1)
                               for t in indices_range], axis=0)
            y[key] = np.stack([np.swapaxes(value[t + self.future_offsets, ...], 0, 1)
                               for t in indices_range], axis=0)

        return x, y

    def generate_adjacency_matrix(
            self, distances_path: Union[str, Path],
            sensor_ids_path: Union[str, Path]) -> np.ndarray:
        """
        Generates the adjacency matrix of a distance graph using a
        thresholded Gaussian filter.
        https://github.com/liyaguang/DCRNN/blob/master/scripts/gen_adj_mx.py

        Args:
            distances_path: The path to the dataframe with real-road
                distances between sensors, of form (to, from, dist).
            sensor_ids_path: The path to the dataframe containing the IDs
                of all the sensors in the METR-LA network.

        Returns: A numpy array, which is the adjacency matrix generated by
            appling a thresholded gaussian kernel filter.
        """

        distances_df = pd.read_csv(filepath_or_buffer=distances_path)
        sensor_ids = self.read_sensor_ids(sensor_ids_path)

        n_nodes = len(sensor_ids)

        adjacency_matrix = np.full(shape=(n_nodes, n_nodes), fill_value=np.inf,
                                   dtype=np.float32)

        sensor_id_to_idx = {}
        for idx, sensor_id in enumerate(sensor_ids):
            sensor_id_to_idx[sensor_id] = idx

        for _, row in distances_df.iterrows():
            src, dst = int(row[0]), int(row[1])
            value = row[2]
            if src in sensor_id_to_idx and dst in sensor_id_to_idx:
                adjacency_matrix[sensor_id_to_idx[src],
                                 sensor_id_to_idx[dst]] = value

        distances = adjacency_matrix[~np.isinf(adjacency_matrix)].flatten()
        std = distances.std()

        adjacency_matrix = np.exp(-np.square(adjacency_matrix / std + 1e-5))
        adjacency_matrix[adjacency_matrix < self.normalized_k] = 0.

        return adjacency_matrix

    @staticmethod
    def read_sensor_ids(path: Union[str, Path]) -> List[str]:
        """
        Reads the sensor id's from a file containing a list of
        comma-separated integers.

        Args:
            param path: The path to the file.

        Returns: A list of IDs.
        """
        with open(path, "r") as input_file:
            sensor_ids = input_file.read()
            return list(map(int, sensor_ids.split(",")))
