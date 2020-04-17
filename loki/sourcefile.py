"""
Module containing a set of classes to represent and manipuate a
Fortran source code file.
"""
import pickle
from pathlib import Path
from collections import OrderedDict
from fparser.two import Fortran2003

from loki.subroutine import Subroutine
from loki.module import Module
from loki.tools import flatten, as_tuple
from loki.logging import info
from loki.frontend import OMNI, OFP, FP, blacklist
from loki.frontend.omni import preprocess_omni, parse_omni_file
from loki.frontend.ofp import parse_ofp_file
from loki.frontend.fparser import parse_fparser_file
from loki.types import TypeTable


__all__ = ['SourceFile']


class SourceFile:
    """
    Class to handle and manipulate source files.

    :param filename: Name of the source file
    :param routines: Subroutines (functions) contained in this source
    :param modules: Fortran modules contained in this source
    :param ast: Optional parser-AST of the original source file
    :param symbols: Instance of class:``TypeTable`` used to cache type information
                    for all symbols defined within this module's scope.
    :param types: Instance of class:``TypeTable`` used to cache type information
                  for all (derived) types defined within this module's scope.
    """

    def __init__(self, path, routines=None, modules=None, ast=None, symbols=None, types=None):
        self.path = Path(path)
        self._routines = routines
        self._modules = modules
        self._ast = ast
        self.symbols = symbols if symbols is not None else TypeTable(None)
        self.types = types if types is not None else TypeTable(None)

    @classmethod
    def from_file(cls, filename, preprocess=False, typedefs=None,
                  xmods=None, includes=None, frontend=OFP):
        if frontend == OMNI:
            return cls.from_omni(filename, typedefs=typedefs, xmods=xmods, includes=includes)
        elif frontend == OFP:
            return cls.from_ofp(filename, preprocess=preprocess, typedefs=typedefs)
        elif frontend == FP:
            return cls.from_fparser(filename, typedefs=typedefs)
        else:
            raise NotImplementedError('Unknown frontend: %s' % frontend)

    @classmethod
    def from_omni(cls, filename, preprocess=False, typedefs=None, xmods=None, includes=None):
        """
        Use the OMNI compiler frontend to generate internal subroutine
        and module IRs.
        """
        filepath = Path(filename)
        pppath = Path(filename).with_suffix('.omni.F90')

        preprocess_omni(filename, pppath, includes=includes)

        with filepath.open() as f:
            raw_source = f.read()

        # Parse the file content into an OMNI Fortran AST
        ast = parse_omni_file(filename=str(pppath), xmods=xmods)
        typetable = ast.find('typeTable')

        obj = cls(filename, ast=ast)

        ast_r = ast.findall('./globalDeclarations/FfunctionDefinition')
        routines = [Subroutine.from_omni(ast=ast, typedefs=typedefs, raw_source=raw_source,
                                         typetable=typetable, parent=obj) for ast in ast_r]

        ast_m = ast.findall('./globalDeclarations/FmoduleDefinition')
        modules = [Module.from_omni(ast=ast, raw_source=raw_source,
                                    typetable=typetable, parent=obj) for ast in ast_m]

        obj.__init__(filename, routines=routines, modules=modules, ast=ast, symbols=obj.symbols,
                     types=obj.types)
        return obj

    @classmethod
    def from_ofp(cls, filename, preprocess=False, typedefs=None):
        file_path = Path(filename)
        info_path = file_path.with_suffix('.pp.info')

        # Unfortunately we need a pre-processing step to sanitize
        # the input to the OFP, as it will otherwise drop certain
        # terms due to advanced bugged-ness! :(
        if preprocess:
            pp_path = file_path.with_suffix('.pp.F90')
            cls.preprocess(file_path, pp_path, info_path)
            file_path = pp_path

        # Import and store the raw file content
        with file_path.open() as f:
            raw_source = f.read()

        # Parse the file content into a Fortran AST
        ast = parse_ofp_file(filename=str(file_path))

        # Extract subroutines and pre/post sections from file
        pp_info = None
        if info_path.exists():
            with info_path.open('rb') as f:
                pp_info = pickle.load(f)

        obj = cls(filename, ast=ast)

        routines = [Subroutine.from_ofp(ast=r, raw_source=raw_source, typedefs=typedefs,
                                        parent=obj, pp_info=pp_info)
                    for r in ast.findall('file/subroutine')]
        routines += [Subroutine.from_ofp(ast=r, raw_source=raw_source, typedefs=typedefs,
                                         parent=obj, pp_info=pp_info)
                     for r in ast.findall('file/function')]
        modules = [Module.from_ofp(ast=m, raw_source=raw_source, parent=obj)
                   for m in ast.findall('file/module')]

        obj.__init__(filename, routines=routines, modules=modules, ast=ast, symbols=obj.symbols,
                     types=obj.types)
        return obj

    @classmethod
    def from_fparser(cls, filename, typedefs=None):
        file_path = Path(filename)

        # Parse the file content into a Fortran AST
        ast = parse_fparser_file(filename=str(file_path))

        obj = cls(filename, ast=ast)

        routine_asts = [r for r in ast.content if isinstance(r, (Fortran2003.Subroutine_Subprogram,
                                                                 Fortran2003.Function_Subprogram))]
        routines = [Subroutine.from_fparser(ast=r, typedefs=typedefs, parent=obj)
                    for r in routine_asts]

        # TODO: Do modules!
        module_asts = [r for r in ast.content if isinstance(r, Fortran2003.Module)]
        modules = [Module.from_fparser(ast=r, parent=obj) for r in module_asts]

        obj.__init__(filename, routines=routines, modules=modules, ast=ast, symbols=obj.symbols,
                     types=obj.types)
        return obj

    @classmethod
    def preprocess(cls, file_path, pp_path, info_path, kinds=None):
        """
        A dedicated pre-processing step to ensure smooth source parsing.

        Note: The OFP drops and/or jumbles up valid expression nodes
        if it encounters _KIND type casts (issue #48). To avoid this,
        we remove these here and create a record of the literals and
        their _KINDs, indexed by line. This allows us to the re-insert
        this information after the AST parse when creating `Subroutine`s.
        """
        if pp_path.exists():
            if pp_path.stat().st_mtime > file_path.stat().st_mtime:
                # Already pre-processed this one, skip!
                return
        info("Pre-processing %s => %s" % (file_path, pp_path))

        with file_path.open() as f:
            source = f.read()

        # Apply preprocessing rules and store meta-information
        pp_info = OrderedDict()
        for name, rule in blacklist.items():
            # Apply rule filter over source file
            rule.reset()
            new_source = ''
            for ll, line in enumerate(source.splitlines(keepends=True)):
                ll += 1  # Correct for Fortran counting
                new_source += rule.filter(line, lineno=ll)

            # Store met-information from rule
            pp_info[name] = rule.info
            source = new_source

        with pp_path.open('w') as f:
            f.write(source)

        with info_path.open('wb') as f:
            pickle.dump(pp_info, f)

    @property
    def source(self):
        content = self.modules + self.subroutines
        return '\n\n'.join(s.source for s in content)

    @property
    def modules(self):
        return as_tuple(self._modules)

    @property
    def subroutines(self):
        return as_tuple(self._routines)

    @property
    def all_subroutines(self):
        routines = as_tuple(self._routines)
        routines += as_tuple(flatten(m.subroutines for m in self.modules))
        return routines

    def __getitem__(self, name):
        module_map = {m.name.lower(): m for m in self.modules}
        if name.lower() in module_map:
            return module_map[name.lower()]

        subroutine_map = {s.name.lower(): s for s in self.all_subroutines}
        if name.lower() in subroutine_map:
            return subroutine_map[name.lower()]

        return None

    def write(self, source=None, filename=None):
        """
        Write content to file

        :param source: Optional source string; if not provided `self.source` is used
        :param filename: Optional filename; if not provided, `self.name` is used
        """
        path = Path(filename or self.path)
        source = self.source if source is None else source
        self.to_file(source=source, path=path)

    @classmethod
    def to_file(cls, source, path):
        """
        Same as ``write(source, filename)``, but can be called from a
        static context.
        """
        info("Writing %s" % path)
        with path.open('w') as f:
            f.write(source)

    @property
    def lines(self):
        """
        Sanitizes source content into long lines with continuous statements.

        Note: This does not change the content of the file
        """
        return self._raw_source.splitlines(keepends=True)

    @property
    def longlines(self):
        return self.body.longlines

    def replace(self, mapping):
        """
        Performs a line-by-line string-replacement from a given mapping

        Note: The replacement is performed on each raw line. Might
        need to improve this later to unpick linebreaks in the search
        keys.
        """
        for section in self.sections:
            section.replace(mapping)
