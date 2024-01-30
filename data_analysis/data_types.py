
import numpy as np
import pandas as pd
from copy import deepcopy
from dataclasses import dataclass, field, asdict
from pathlib import Path
from ruamel.yaml import YAML
from scipy.signal import hilbert
from scipy.stats import entropy
from typing import Optional



@dataclass
class Event:
    category: str
    timestamp: float # Seconds since the start of the recording
    distance: Optional[float] = None
    duration: Optional[float] = None
    
    def to_dict(self):
        data = deepcopy(self.__dict__)
        if self.distance is None:
            del data['distance']
        if self.duration is None:
            del data['duration']
        return data


# Path the user walked. Assumed to be a straight line. Measurements define a triangle :)
@dataclass
class WalkPath:
    start: float # Start distance from sensor (m)
    stop: float # End distance from sensor (m)
    length: float # Total length of the walk (m)


@dataclass
class RecordingEnvironment:
    location: str
    fs: float
    user: str
    floor: str
    footwear: str
    walk_type: str
    obstacle_radius: float
    wall_radius: float
    path: WalkPath
    walk_speed: str = 'normal'
    temperature: Optional[float] = None # in Celsius
    notes: str = ''

    def __post_init__(self):
        valid_walks = ['normal', 'stomp', 'shuffle', 'limp', 'griddy']
        valid_footwear = ['barefoot', 'shoes', 'socks', 'slippers']
        if self.walk_type not in valid_walks:
            raise ValueError(f'Invalid walk type "{self.walk_type}". Valid walks are: {valid_walks}')
        if self.footwear not in valid_footwear:
            raise ValueError(f'Invalid footwear "{self.footwear}". Valid footwear are: {valid_footwear}')

    def to_dict(self):
        return asdict(self)


@dataclass
class Recording:
    env: RecordingEnvironment
    events: list[Event] = field(default_factory=list)
    ts: np.ndarray = field(default_factory=np.zeros(0))
    filepath: Optional[str] = None

    @classmethod
    def from_file(cls, filename: str):
        try:
            yaml = YAML()
            with open(filename) as file:
                data = yaml.load(file)
            rec = cls.from_dict(data)
            rec.filepath = filename
            return rec
        except Exception as e:
            raise ValueError(f'Failed to load recording from "{filename}".') from e

    @classmethod
    def from_dict(cls, data: dict):
        env = RecordingEnvironment(**data['env'])
        events = [Event(**event) for event in data['events']]
        return cls(env, events, np.asarray(data['ts']))

    def to_yaml(self, filename: str):
        yaml = YAML()
        yaml.dump(self.to_dict(), Path(filename))

    def to_dict(self):
        return {
            'env': self.env.to_dict(),
            'events': [event.to_dict() for event in self.events],
            'ts': self.ts
        }


class Metrics:
    def __init__(self, *timestamp_groups: np.ndarray):
        self._df = pd.DataFrame.from_dict(
            {
                'step_count': [np.sum([len(timestamps) for timestamps in timestamp_groups])],
                'STGA': [np.mean([self._get_STGA(timestamps) for timestamps in timestamp_groups])],
                'cadence': [np.mean([self._get_cadence(timestamps) for timestamps in timestamp_groups])],
                'phase_sync': [np.mean([self._get_phase_sync(timestamps) for timestamps in timestamp_groups])],
                'conditional_entropy': [np.mean([self._get_conditional_entropy(timestamps) for timestamps in timestamp_groups])],
            }
        )

    @property
    def step_count(self):
        """Total number of steps recorded."""
        return np.sum(self._df['step_count'].values)

    @property
    def STGA(self):
        """Stride Time Gait Asymmetry (STGA)"""
        return np.mean(self._df['STGA'].values)

    @property
    def cadence(self):
        """Cadence (steps per second)"""
        return np.mean(self._df['cadence'].values)
    
    @property
    def phase_sync(self):
        """Stride Time Phase Synchronization Index (0 < p < 1). High values indicate high phase synchronization, which is good."""
        return np.mean(self._df['phase_sync'].values)

    @property
    def conditional_entropy(self):
        """Stride Time Conditional Entropy. Low values indicate high regularity, which is good."""
        return np.mean(self._df['conditional_entropy'].values)

    def __len__(self):
        return len(self._df)

    @staticmethod
    def _get_STGA(timestamps: np.ndarray):
        if len(timestamps) < 3:
            return np.nan
        step_durations = np.diff(timestamps)
        # TODO: Does this match literature?
        # https://citeseerx.ist.psu.edu/document?repid=rep1&type=pdf&doi=aeee9316f2a72d0f89e59f3c5144bf69a695730b
        return np.abs(np.mean(step_durations[1:] / step_durations[:-1]) - 1) / np.mean(step_durations)

    @staticmethod
    def _get_cadence(timestamps: np.ndarray):
        if len(timestamps) < 2:
            return np.nan
        return 1 / np.mean(np.diff(timestamps))
    
    @staticmethod
    def _get_phase_sync(timestamps: np.ndarray, num_bins=40):
        if len(timestamps) < 4:
            return np.nan
        if len(timestamps) % 2 != 0:
            timestamps = np.copy(timestamps)[:-1]
        timestamps_right_foot = timestamps[1::2]
        timestamps_left_foot = timestamps[::2]
        analytic_signal1 = hilbert(timestamps_left_foot)
        analytic_signal2 = hilbert(timestamps_right_foot)
        phase1 = np.unwrap(np.angle(analytic_signal1))
        phase2 = np.unwrap(np.angle(analytic_signal2))
        phase_difference = phase1 - phase2
        H = Metrics._calculate_shannon_entropy(phase_difference, num_bins)
        H_max = np.log2(num_bins)
        return (H_max - H) / H_max

    # https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=7247739
    @staticmethod
    def _get_conditional_entropy(timestamps: np.ndarray):
        if len(timestamps) < 4:
            return np.nan
        timestamps_left_foot = timestamps[::2]
        timestamps_right_foot = timestamps[1::2]
        stride_times_left_foot = np.diff(timestamps_left_foot)
        stride_times_right_foot = np.diff(timestamps_right_foot)
        shannon_entropy_left_foot = Metrics._calculate_shannon_entropy(stride_times_left_foot)
        shannon_entropy_right_foot = Metrics._calculate_shannon_entropy(stride_times_right_foot)
        return (shannon_entropy_left_foot + shannon_entropy_right_foot) / 2

    @staticmethod
    def _calculate_shannon_entropy(stride_times: np.ndarray, num_bins=40):
        counts, _ = np.histogram(stride_times, bins=num_bins)
        probabilities = counts / sum(counts)
        return entropy(probabilities, base=2)

    def __add__(self, other: 'Metrics'):
        """Combines two Metrics objects by averaging their values."""
        if not isinstance(other, Metrics):
            raise ValueError('Can only add Metrics to Metrics.')
        if not len(self):
            return other
        if not len(other):
            return self
        self._df = pd.concat([self._df, other._df])
        return self

    def error(self, truth: 'Metrics') -> pd.DataFrame:
        """Returns the % error between two Metrics objects."""
        if not isinstance(truth, Metrics):
            raise ValueError('Can only compare Metrics to Metrics.')
        if truth._df.shape != self._df.shape:
            raise ValueError('Cannot compare Metrics of different lengths.')
        return np.abs(self._df - truth._df) / truth._df

    def __str__(self) -> str:
        return str(self._df)
