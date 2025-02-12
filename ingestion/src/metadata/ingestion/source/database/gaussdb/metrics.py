from sqlalchemy.ext.compiler import compiles

from metadata.ingestion.source.database.gaussdb.types.money import GaussdbMoney
from metadata.profiler.metrics.static.mean import AvgFn
from metadata.profiler.metrics.static.stddev import StdDevFn
from metadata.profiler.orm.functions.median import MedianFn
from metadata.profiler.orm.registry import Dialects


@compiles(AvgFn, Dialects.Gaussdb)
def avg(element, compiler, **kw):
    """
    Cast to decimal to get around potential integer overflow error
    """
    proc = compiler.process(element.clauses, **kw)
    if isinstance(list(element.clauses)[0].type, GaussdbMoney):
        return f"{element.name}({GaussdbMoney.compile_as_float(proc)})"
    return f"{element.name}({proc})"


@compiles(StdDevFn, Dialects.Gaussdb)
def stddev(element, compiler, **kw):
    """Returns stdv for clickhouse database and handle empty tables.
    If table is empty, clickhouse returns NaN.
    """
    proc = compiler.process(element.clauses, **kw)
    if isinstance(list(element.clauses)[0].type, GaussdbMoney):
        return f"STDDEV_POP({GaussdbMoney.compile_as_float(proc)})"
    return f"STDDEV_POP({proc})"


@compiles(MedianFn, Dialects.Gaussdb)
def median(elements, compiler, **kwargs):  # pylint: disable=unused-argument
    col, _, percentile = [
        compiler.process(element, **kwargs) for element in elements.clauses
    ]
    if isinstance(list(elements.clauses)[0], GaussdbMoney):
        return "percentile_cont(%.2f) WITHIN GROUP (ORDER BY %s ASC)" % (
            percentile,
            GaussdbMoney.compile_as_float(col),
        )
    return MedianFn.default_fn(elements, compiler, **kwargs)
