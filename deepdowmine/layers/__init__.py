"""Collection of layers."""

from .collapse import (
    AttentionCollapse,
    AverageCollapse,
    ElementCollapse,
    ExponentialCollapse,
    MaxCollapse,
    SumCollapse,
)
from .allocate import (
    AnalyticalMarkowitz,
    NCO,
    NumericalMarkowitz,
    NumericalRiskBudgeting,
    Resample,
    SoftmaxAllocator,
    SparsemaxAllocator,
    WeightNorm,
    NumericalMarkowitzWithShorting,
    ThesisMarkowitz,
    ThesisMarkowitzFullOpti,
    ThesisMarkowitzMinVar
)
from .misc import Cov2Corr, CovarianceMatrix, KMeans, MultiplyByConstant
from .transform import Conv, RNN, Warp, Zoom

__all__ = [
    "AnalyticalMarkowitz",
    "AttentionCollapse",
    "AverageCollapse",
    "Conv",
    "Cov2Corr",
    "CovarianceMatrix",
    "ElementCollapse",
    "ExponentialCollapse",
    "KMeans",
    "MaxCollapse",
    "MultiplyByConstant",
    "NCO",
    "NumericalMarkowitz",
    "NumericalRiskBudgeting",
    "Resample",
    "RNN",
    "SoftmaxAllocator",
    "SparsemaxAllocator",
    "SumCollapse",
    "Warp",
    "WeightNorm",
    "Zoom",
    "NumericalMarkowitzWithShorting",
    "ThesisMarkowitz",
    "ThesisMarkowitzFullOpti",
    "ThesisMarkowitzMinVar"
]
