import re
import pymbolic.primitives as pmbl
from pymbolic.mapper import Mapper, WalkMapper, CombineMapper
from pymbolic.mapper.stringifier import (StringifyMapper, PREC_NONE, PREC_CALL)

from loki.tools import as_tuple

__all__ = ['LokiStringifyMapper', 'ExpressionRetriever', 'ExpressionDimensionsMapper',
           'ExpressionCallbackMapper']


class LokiStringifyMapper(StringifyMapper):
    """
    A class derived from the default :class:`StringifyMapper` that adds mappings for nodes of the
    expression tree that we added ourselves.

    This is the default pretty printer for nodes in the expression tree.
    """
    _regex_string_literal = re.compile(r"((?<!')'(?:'')*(?!'))")

    def __init__(self, constant_mapper=None):
        super(LokiStringifyMapper, self).__init__(constant_mapper)

    def map_logic_literal(self, expr, *args, **kwargs):
        return str(expr.value)

    def map_float_literal(self, expr, enclosing_prec, *args, **kwargs):
        if expr.kind is not None:
            return '%s_%s' % (str(expr.value), str(expr.kind))
        else:
            return str(expr.value)

    map_int_literal = map_logic_literal

    def map_string_literal(self, expr, *args, **kwargs):
        return "'%s'" % self._regex_string_literal.sub(r"'\1", expr.value)

    def map_scalar(self, expr, *args, **kwargs):
        if expr.parent is not None:
            parent = self.rec(expr.parent, *args, **kwargs)
            return self.format('%s%%%s', parent, expr.basename)
        else:
            return expr.name

    def map_array(self, expr, enclosing_prec, *args, **kwargs):
        dims = ','.join(self.rec(d, PREC_NONE, *args, **kwargs) for d in expr.dimensions or [])
        if dims:
            dims = '(' + dims + ')'
        parent, initial = '', ''
        if expr.parent is not None:
            parent = self.rec(expr.parent, PREC_NONE, *args, **kwargs) + '%'
        if expr.type is not None and expr.type.initial is not None:
            initial = ' = %s' % self.rec(expr.initial, PREC_NONE, *args, **kwargs)
        return self.format('%s%s%s%s', parent, expr.basename, dims, initial)

    map_inline_call = StringifyMapper.map_call_with_kwargs

    def map_cast(self, expr, enclosing_prec, *args, **kwargs):
        name = self.rec(expr.function, PREC_CALL, *args, **kwargs)
        expression = self.rec(expr.parameters[0], PREC_NONE, *args, **kwargs)
        if expr.kind:
            if isinstance(expr.kind, pmbl.Expression):
                kind = ', kind=' + self.rec(expr.kind, PREC_NONE, *args, **kwargs)
            else:
                kind = ', kind=' + str(expr.kind)
        else:
            kind = ''
        return self.format('%s(%s%s)', name, expression, kind)

    def map_range_index(self, expr, *args, **kwargs):
        lower = self.rec(expr.lower, *args, **kwargs) if expr.lower else ''
        upper = self.rec(expr.upper, *args, **kwargs) if expr.upper else ''
        if expr.step:
            return '%s:%s:%s' % (lower, upper, self.rec(expr.step, *args, **kwargs))
        else:
            return '%s:%s' % (lower, upper)

    def map_parenthesised_add(self, *args, **kwargs):
        return self.parenthesize(self.map_sum(*args, **kwargs))

    def map_parenthesised_mul(self, *args, **kwargs):
        return self.parenthesize(self.map_product(*args, **kwargs))

    def map_parenthesised_pow(self, *args, **kwargs):
        return self.parenthesize(self.map_power(*args, **kwargs))

    def map_string_concat(self, expr, *args, **kwargs):
        return ' // '.join(self.rec(c, *args, **kwargs) for c in expr.children)

    def map_literal_list(self, expr, *args, **kwargs):
        return '[' + ','.join(str(c) for c in expr.elements) + ']'


class ExpressionRetriever(WalkMapper):
    """
    A visitor for the expression tree that looks for entries specified by a query.
    """

    def __init__(self, query):
        super(ExpressionRetriever, self).__init__()

        self.query = query
        self.exprs = list()

    def post_visit(self, expr, *args, **kwargs):
        if self.query(expr):
            self.exprs.append(expr)

    map_scalar = WalkMapper.map_variable

    def map_array(self, expr, *args, **kwargs):
        self.visit(expr)
        if expr.dimensions:
            for d in expr.dimensions:
                self.rec(d, *args, **kwargs)
        self.post_visit(expr, *args, **kwargs)

    map_logic_literal = WalkMapper.map_constant
    map_float_literal = WalkMapper.map_constant
    map_int_literal = WalkMapper.map_constant
    map_string_literal = WalkMapper.map_constant
    map_inline_call = WalkMapper.map_call_with_kwargs

    def map_cast(self, expr, *args, **kwargs):
        self.visit(expr)
        for p in expr.parameters:
            self.rec(p, *args, **kwargs)
        if isinstance(expr.kind, pmbl.Expression):
            self.rec(expr.kind, *args, **kwargs)
        self.post_visit(expr, *args, **kwargs)

    map_parenthesised_add = WalkMapper.map_sum
    map_parenthesised_mul = WalkMapper.map_product
    map_parenthesised_pow = WalkMapper.map_power
    map_string_concat = WalkMapper.map_sum

    def map_range_index(self, expr, *args, **kwargs):
        self.visit(expr)
        if expr.lower:
            self.rec(expr.lower, *args, **kwargs)
        if expr.upper:
            self.rec(expr.upper, *args, **kwargs)
        if expr.step:
            self.rec(expr.step, *args, **kwargs)
        self.post_visit(expr, *args, **kwargs)

    def map_literal_list(self, expr, *args, **kwargs):
        self.visit(expr)
        for elem in expr.elements:
            self.visit(elem)
        self.post_visit(expr, *args, **kwargs)


class ExpressionDimensionsMapper(Mapper):
    """
    A visitor for an expression that determines the dimensions of the expression.
    """

    def __init__(self):
        super(ExpressionDimensionsMapper, self).__init__()

    def map_algebraic_leaf(self, expr, *args, **kwargs):
        from loki.expression.symbol_types import IntLiteral
        return as_tuple(IntLiteral(1))

    map_logic_literal = map_algebraic_leaf
    map_float_literal = map_algebraic_leaf
    map_int_literal = map_algebraic_leaf
    map_scalar = map_algebraic_leaf

    def map_array(self, expr, *args, **kwargs):
        if expr.dimensions is None:
            return expr.shape
        else:
            from loki.expression.symbol_types import RangeIndex, IntLiteral
            dims = [self.rec(d, *args, **kwargs)[0] for d in expr.dimensions]
            # Replace colon dimensions by the value from shape
            shape = expr.shape or [None] * len(dims)
            dims = [s if (isinstance(d, RangeIndex) and d.lower is None and d.upper is None)
                    else d for d, s in zip(dims, shape)]
            # Remove singleton dimensions
            dims = [d for d in dims if d != IntLiteral(1)]
            return as_tuple(dims)

    def map_range_index(self, expr, *args, **kwargs):
        if expr.lower is None and expr.upper is None:
            return as_tuple(expr)
        else:
            lower = expr.lower.value - 1 if expr.lower is not None else 0
            step = expr.step.value if expr.step is not None else 1
            return as_tuple((expr.upper - lower) // step)


class ExpressionCallbackMapper(CombineMapper):
    """
    A visitor for expressions that returns the combined result of a specified callback function.
    """

    def __init__(self, callback, combine):
        super(ExpressionCallbackMapper, self).__init__()
        self.callback = callback
        self.combine = combine

    def map_constant(self, expr, *args, **kwargs):
        return self.callback(expr, *args, **kwargs)

    map_logic_literal = map_constant
    map_int_literal = map_constant
    map_float_literal = map_constant
    map_string_literal = map_constant
    map_scalar = map_constant
    map_array = map_constant

    def map_inline_call(self, expr, *args, **kwargs):
        parameters = tuple(self.rec(ch, *args, **kwargs) for ch in expr.parameters)
        kw_parameters = tuple(self.rec(ch, *args, **kwargs) for ch in expr.kw_parameters.values())
        return self.combine(parameters + kw_parameters)

    def map_cast(self, expr, *args, **kwargs):
        if expr.kind and isinstance(expr.kind, pmbl.Expression):
            kind = (self.rec(expr.kind, *args, **kwargs),)
        else:
            kind = tuple()
        return self.combine((self.rec(expr.function, *args, **kwargs),
                             self.rec(expr.parameters[0])) + kind)

    def map_range_index(self, expr, *args, **kwargs):
        lower = (self.rec(expr.lower, *args, **kwargs),) if expr.lower else tuple()
        upper = (self.rec(expr.upper, *args, **kwargs),) if expr.upper else tuple()
        step = (self.rec(expr.step, *args, **kwargs),) if expr.step else tuple()
        return self.combine(lower + upper + step)

    map_parenthesised_add = CombineMapper.map_sum
    map_parenthesised_mul = CombineMapper.map_product
    map_parenthesised_pow = CombineMapper.map_power
    map_string_concat = CombineMapper.map_sum

    def map_literal_list(self, expr, *args, **kwargs):
        return self.combine(tuple(self.rec(c, *args, **kwargs) for c in expr.elements))