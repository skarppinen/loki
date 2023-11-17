# (C) Copyright 2018- ECMWF.
# This software is licensed under the terms of the Apache Licence Version 2.0
# which can be obtained at http://www.apache.org/licenses/LICENSE-2.0.
# In applying this licence, ECMWF does not waive the privileges and immunities
# granted to it by virtue of its status as an intergovernmental organisation
# nor does it submit to any jurisdiction.

from pathlib import Path
from collections import OrderedDict

from loki.dimension import Dimension
from loki.tools import as_tuple, CaseInsensitiveDict, load_module
from loki.logging import error


__all__ = ['SchedulerConfig', 'TransformationConfig']


class SchedulerConfig:
    """
    Configuration object for the transformation :any:`Scheduler` that
    encapsulates default behaviour and item-specific behaviour. Can
    be create either from a raw dictionary or configration file.

    Parameters
    ----------
    default : dict
        Default options for each item
    routines : dict of dicts or list of dicts
        Dicts with routine-specific options.
    dimensions : dict of dicts or list of dicts
        Dicts with options to define :any`Dimension` objects.
    disable : list of str
        Subroutine names that are entirely disabled and will not be
        added to either the callgraph that we traverse, nor the
        visualisation. These are intended for utility routines that
        pop up in many routines but can be ignored in terms of program
        control flow, like ``flush`` or ``abort``.
    enable_imports : bool
        Disable the inclusion of module imports as scheduler dependencies.
    """

    def __init__(
            self, default, routines, disable=None, dimensions=None,
            transformation_configs=None, dic2p=None, derived_types=None,
            enable_imports=False
    ):
        if isinstance(routines, dict):
            self.routines = CaseInsensitiveDict(routines)
        else:
            self.routines = CaseInsensitiveDict((r.name, r) for r in as_tuple(routines))

        if isinstance(transformation_configs, dict):
            self.transformation_configs = transformation_configs
        else:
            self.transformation_configs = dict((r.name, r) for r in as_tuple(transformation_configs))

        # Instantiate Transformation objects
        self.transformations = {
            name: config.instantiate() for name, config in self.transformation_configs.items()
        }

        self.default = default
        self.disable = as_tuple(disable)
        self.dimensions = dimensions
        self.enable_imports = enable_imports

        if dic2p is not None:
            self.dic2p = dic2p
        else:
            self.dic2p = {}
        if derived_types is not None:
            self.derived_types = derived_types
        else:
            self.derived_types = ()

    @classmethod
    def from_dict(cls, config):
        default = config['default']
        if 'routine' in config:
            config['routines'] = OrderedDict((r['name'], r) for r in config.get('routine', []))
        else:
            config['routines'] = []
        routines = config['routines']
        disable = default.get('disable', None)
        enable_imports = default.get('enable_imports', False)

        # Add any dimension definitions contained in the config dict
        dimensions = {}
        if 'dimension' in config:
            dimensions = [Dimension(**d) for d in config['dimension']]
            dimensions = {d.name: d for d in dimensions}

        # Create config objects for Transformation configurations
        transformation_configs = config.get('transformations', {})
        transformation_configs = {
            name: TransformationConfig(name=name, **cfg)
            for name, cfg in transformation_configs.items()
        }

        dic2p = {}
        if 'dic2p' in config:
            dic2p = config['dic2p']

        derived_types = ()
        if 'derived_types' in config:
            derived_types = config['derived_types']

        return cls(
            default=default, routines=routines, disable=disable, dimensions=dimensions,
            transformation_configs=transformation_configs, dic2p=dic2p, derived_types=derived_types,
            enable_imports=enable_imports
        )

    @classmethod
    def from_file(cls, path):
        import toml  # pylint: disable=import-outside-toplevel
        # Load configuration file and process options
        with Path(path).open('r') as f:
            config = toml.load(f)

        return cls.from_dict(config)


class TransformationConfig:
    """
    Configuration object for :any:`Transformation` instances that can
    be used to create :any:`Transformation` objects from dictionaries
    or a config file.

    Parameters
    ----------
    name : str
        Name of the transformation object
    module : str
        Python module from which to load the transformation class
    classname : str, optional
        Name of the class to look for when instantiating the transformation.
        If not provided, ``name`` will be used instead.
    path : str or Path, optional
        Path to add to the sys.path before attempting to load the ``module``
    options : dict
        Dicts of options that define the transformation behaviour.
        These options will be passed as constructor arguments using
        keyword-argument notation.
    """

    def __init__(self, name, module, classname=None, path=None, options=None):
        self.name = name
        self.module = module
        self.classname = classname or self.name
        self.path = path
        self.options = dict(options)

    def instantiate(self):
        """
        Creates instantiated :any:`Transformation` object from stored config options.
        """
        # Load the module that contains the transformations
        mod = load_module(self.module, path=self.path)

        # Check for and return Transformation class
        if not hasattr(mod, self.classname):
            raise RuntimeError('Failed to load Transformation class!')

        # Attempt to instantiate transformation from config
        try:
            transformation = getattr(mod, self.classname)(**self.options)
        except TypeError as e:
            error(f'[Loki::Transformation] Failed to instiate {self.classname} from configuration')
            error(f'    Options passed: {self.options}')
            raise e

        return transformation
