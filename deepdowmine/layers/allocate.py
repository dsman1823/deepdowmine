"""Collection of layers that are using producing weight allocations."""

import cvxpy as cp
from cvxpylayers.torch import CvxpyLayer
import torch
from torch.distributions import MultivariateNormal
import torch.nn as nn

from .misc import Cov2Corr, CovarianceMatrix, KMeans

### Take into account this issue!!! https://github.com/cvxgrp/cvxpylayers/issues/77

class ThesisMarkowitzMinVar(nn.Module):
 	
    def __init__(self, n_assets, max_weight):
        super(ThesisMarkowitzMinVar, self).__init__()
        self.n_assets = n_assets
        self.max_weight = max_weight
        # Static CVXPY parameter for the covariance matrix, marked as positive semi-definite (PSD)
        self.covmat_param_sqrt = cp.Parameter((n_assets, n_assets), PSD=True)

        # Portfolio weights variable
        w = cp.Variable(n_assets)
        
        # Define the risk as the portfolio variance
        risk = cp.sum_squares(self.covmat_param_sqrt @ w)
        # Optimization problem to minimize the risk (portfolio variance)
        # subject to weights summing to 1 and each weight being bounded [0, max_weight]
        prob = cp.Problem(cp.Minimize(risk),
                          [cp.sum(w) == 1, w <= max_weight, -w <= max_weight])
                          
        self.cvxpylayer = CvxpyLayer(prob, parameters=[self.covmat_param_sqrt], variables=[w])

    def forward(self, covmat_sqrt):
        optimal_weights, = self.cvxpylayer(covmat_sqrt)
        return optimal_weights


class ThesisMarkowitzFullOpti(nn.Module):
    def __init__(self, n_assets, max_weight):
        super(ThesisMarkowitzFullOpti, self).__init__()
        self.n_assets = n_assets
        self.max_weight = max_weight
        self.rets_param = cp.Parameter(n_assets)
        self.alpha_param = cp.Parameter(nonneg=True)
        self.covmat_param = cp.Parameter((n_assets, n_assets), PSD=True) # <--- sqrt of cov matrix

        w = cp.Variable(n_assets)
        ret = cp.matmul(self.rets_param, w)
        risk = cp.sum_squares(self.covmat_param  @ w)
        reg = self.alpha_param * cp.norm(w, 2)

        prob = cp.Problem(cp.Maximize(ret  - risk - reg),
                          [cp.sum(w) == 1, w <= max_weight, -w <= max_weight])
        self.cvxpylayer = CvxpyLayer(prob, parameters=[self.rets_param, self.covmat_param, self.alpha_param], variables=[w])

    def forward(self, rets, covmat, gamma, alpha):
        # Ensure alpha and gamma are non-negative
        alpha_abs = torch.abs(alpha)
        n_samples, n_assets = rets.shape
        gamma_ = torch.abs(gamma.repeat((1, n_assets * n_assets)).view(
            n_samples, n_assets, n_assets
        ))
	

        optimal_weights, = self.cvxpylayer(rets, gamma_  * covmat, alpha_abs)
        return optimal_weights




class ThesisMarkowitz(nn.Module):
    def __init__(self, n_assets, max_weight=1):
        super(ThesisMarkowitz, self).__init__()
        self.n_assets = n_assets
        self.max_weight = max_weight
        # Static CVXPY parameters initialized without values
        self.rets_param = cp.Parameter(n_assets)
        self.alpha_param = cp.Parameter(nonneg=True)
        self.covmat_param = cp.Parameter((n_assets, n_assets), PSD=True)

        w = cp.Variable(n_assets)
        ret = cp.matmul(self.rets_param, w)
        risk = cp.sqrt(cp.quad_form(w, self.covmat_param))
        reg = self.alpha_param * cp.norm(w, 2)

        prob = cp.Problem(cp.Maximize(ret - risk - reg),
                          [cp.sum(w) == 1, w <= self.max_weight, -w <= self.max_weight])
        self.cvxpylayer = CvxpyLayer(prob, parameters=[self.rets_param, self.covmat_param, self.alpha_param], variables=[w])

    def forward(self, rets, covmat, gamma, alpha):
        # Ensure alpha is non-negative
        alpha_abs = torch.abs(alpha)
        # Reshape gamma for broadcasting if necessary
        gamma_reshaped = gamma.view(-1, 1, 1).expand_as(covmat)
        # Adjust covmat by gamma directly in PyTorch to maintain differentiability
        adjusted_covmat = gamma_reshaped * covmat
        
        optimal_weights, = self.cvxpylayer(rets, adjusted_covmat, alpha_abs)
        return optimal_weights




class NumericalMarkowitzWithShorting(nn.Module):
    """Convex optimization layer stylized into portfolio optimization problem.

    Parameters
    ----------
    n_assets : int
        Number of assets.

    Attributes
    ----------
    cvxpylayer : CvxpyLayer
        Custom layer used by a third party package called cvxpylayers.

    References
    ----------
    [1] https://github.com/cvxgrp/cvxpylayers

    """

    def __init__(self, n_assets, max_weight=2):
        """Construct."""
        super().__init__()
        covmat_sqrt = cp.Parameter((n_assets, n_assets))
        rets = cp.Parameter(n_assets)
        alpha = cp.Parameter(nonneg=True)

        w = cp.Variable(n_assets)
        ret = rets @ w
        risk = cp.sum_squares(covmat_sqrt @ w)
        reg = alpha * (cp.norm(w) ** 2)

        prob = cp.Problem(
            cp.Maximize(ret - risk - reg),
            [cp.sum(w) == 1, w >= -max_weight, w <= max_weight],
        )

        assert prob.is_dpp()

        self.cvxpylayer = CvxpyLayer(
            prob, parameters=[rets, covmat_sqrt, alpha], variables=[w]
        )

    def forward(self, rets, covmat_sqrt, gamma_sqrt, alpha):
        """Perform forward pass.

        Parameters
        ----------
        rets : torch.Tensor
            Of shape (n_samples, n_assets) representing expected returns (or whatever the feature extractor decided
            to encode).

        covmat_sqrt : torch.Tensor
            Of shape (n_samples, n_assets, n_assets) representing the square of the covariance matrix.

        gamma_sqrt : torch.Tensor
            Of shape (n_samples,) representing the tradeoff between risk and return - where on efficient frontier
            we are.

        alpha : torch.Tensor
            Of shape (n_samples,) representing how much L2 regularization is applied to weights. Note that
            we pass the absolute value of this variable into the optimizer since when creating the problem
            we asserted it is going to be nonnegative.

        Returns
        -------
        weights : torch.Tensor
            Of shape (n_samples, n_assets) representing the optimal weights as determined by the convex optimizer.

        """
        n_samples, n_assets = rets.shape
        gamma_sqrt_ = gamma_sqrt.repeat((1, n_assets * n_assets)).view(
            n_samples, n_assets, n_assets
        )
        alpha_abs = torch.abs(alpha)  # it needs to be nonnegative

        return self.cvxpylayer(rets, gamma_sqrt_ * covmat_sqrt, alpha_abs)[0]



class AnalyticalMarkowitz(nn.Module):
    """Minimum variance and maximum sharpe ratio with no constraints.

    There exists known analytical solutions so numerical solutions are necessary.

    References
    ----------
    [1] http://faculty.washington.edu/ezivot/econ424/portfolioTheoryMatrix.pdf
    """

    def forward(self, covmat, rets=None):
        """Perform forward pass.

        Parameters
        ----------
        covmat : torch.Tensor
            Covariance matrix of shape `(n_samples, n_assets, n_assets)`.

        rets : torch.Tensor or None
            If tensor then of shape `(n_samples, n_assets)` representing expected returns. If provided triggers
            computation of maximum share ratio. Else None triggers computation of minimum variance portfolio.

        Returns
        -------
        weights : torch.Tensor
            Of shape (n_samples, n_assets) representing the optimal weights. If `rets` provided, then it represents
            maximum sharpe ratio portfolio (tangency portfolio). Otherwise minimum variance portfolio.
        """
        n_samples, n_assets, _ = covmat.shape
        device = covmat.device
        dtype = covmat.dtype

        ones = torch.ones(n_samples, n_assets, 1).to(
            device=device, dtype=dtype
        )
        if rets is not None:
            expected_returns = rets.view(n_samples, n_assets, 1)
        else:
            expected_returns = ones

        w_unscaled = torch.linalg.solve(covmat, expected_returns)
        denominator = torch.matmul(ones.permute(0, 2, 1), w_unscaled)
        w = w_unscaled / denominator

        return w.squeeze(-1)


class NCO(nn.Module):
    """Nested cluster optimization.

    This optimization algorithm performs the following steps:

         1. Divide all assets into clusters
         2. Run standard optimization inside of each of these clusters (intra step)
         3. Run standard optimization on the resulting portfolios (inter step)
         4. Compute the final weights

    Parameters
    ----------
    n_clusters : int
        Number of clusters to find in the data. Note that the underlying clustering model is
        KMeans - ``deepdow.layers.KMeans``.

    n_init : int
        Number of runs of the clustering algorithm.

    init : str, {'random', 'k-means++'}
        Initialization strategy of the clustering algorithm.

    random_state : int or None
        Random state passed to the stochastic k-means clustering.

    See Also
    --------
    deepdow.layers.KMeans : k-means clustering algorithm

    References
    ----------
    [1] M Lopez de Prado.
        "A Robust Estimator of the Efficient Frontier"
        Available at SSRN 3469961, 2019

    """

    def __init__(
        self, n_clusters, n_init=10, init="random", random_state=None
    ):
        super().__init__()
        self.n_clusters = n_clusters
        self.n_init = n_init
        self.init = init
        self.random_state = random_state

        self.cov2corr_layer = Cov2Corr()
        self.kmeans_layer = KMeans(
            n_clusters=self.n_clusters,
            n_init=self.n_init,
            init=self.init,
            random_state=self.random_state,
        )

        self.analytical_markowitz_layer = AnalyticalMarkowitz()

    def forward(self, covmat, rets=None):
        """Perform forward pass.

        Parameters
        ----------
        covmat : torch.Tensor
            Covariance matrix of shape `(n_samples, n_assets, n_assets)`.

        rets : torch.Tensor or None
            If tensor then of shape `(n_samples, n_assets)` representing expected returns. If provided triggers
            computation of maximum share ratio. Else None triggers computation of minimum variance portfolio.

        Returns
        -------
        weights : torch.Tensor
            Of shape (n_samples, n_assets) representing the optimal weights. If `rets` provided, then
            maximum sharpe ratio portfolio (tangency portfolio) used both on intra and inter cluster level. Otherwise
            minimum variance portfolio.

        Notes
        -----
        Currently there is not batching over the sample dimension - simple for loop is used.

        """
        n_samples, n_assets, _ = covmat.shape
        dtype, device = covmat.dtype, covmat.device

        corrmat = Cov2Corr()(covmat)

        w_l = (
            []
        )  # we need to iterate over the sample dimension (currently no speedup)

        for i in range(n_samples):
            cluster_ixs, cluster_centers = self.kmeans_layer(corrmat[i])

            w_intra_clusters = torch.zeros(
                (n_assets, self.n_clusters), dtype=dtype, device=device
            )

            for c in range(self.n_clusters):
                in_cluster = torch.where(cluster_ixs == c)[
                    0
                ]  # indices from the same cluster
                intra_covmat = (
                    covmat[[i]]
                    .index_select(1, in_cluster)
                    .index_select(2, in_cluster)
                )  # (1, ?, ?)
                intra_rets = (
                    None
                    if rets is None
                    else rets[[i]].index_select(1, in_cluster)
                )  # (1, ?)
                w_intra_clusters[
                    in_cluster, c
                ] = self.analytical_markowitz_layer(intra_covmat, intra_rets)[
                    0
                ]

            inter_covmat = w_intra_clusters.T @ (
                covmat[i] @ w_intra_clusters
            )  # (n_clusters, n_clusters)
            inter_rets = (
                None
                if rets is None
                else (w_intra_clusters.T @ rets[i]).view(1, -1)
            )  # (1, n_clusters)
            w_inter_clusters = self.analytical_markowitz_layer(
                inter_covmat.view(1, self.n_clusters, self.n_clusters),
                inter_rets,
            )  # (1, n_clusters)
            w_final = (w_intra_clusters * w_inter_clusters).sum(
                dim=1
            )  # (n_assets,)

            w_l.append(w_final)

        res = torch.stack(w_l, dim=0)

        return res


class NumericalMarkowitz(nn.Module):
    """Convex optimization layer stylized into portfolio optimization problem.

    Parameters
    ----------
    n_assets : int
        Number of assets.

    Attributes
    ----------
    cvxpylayer : CvxpyLayer
        Custom layer used by a third party package called cvxpylayers.

    References
    ----------
    [1] https://github.com/cvxgrp/cvxpylayers

    """

    def __init__(self, n_assets, max_weight=1):
        """Construct."""
        super().__init__()
        covmat_sqrt = cp.Parameter((n_assets, n_assets))
        rets = cp.Parameter(n_assets)
        alpha = cp.Parameter(nonneg=True)

        w = cp.Variable(n_assets)
        ret = rets @ w
        risk = cp.sum_squares(covmat_sqrt @ w)
        reg = alpha * (cp.norm(w) ** 2)

        prob = cp.Problem(
            cp.Maximize(ret - risk - reg),
            [cp.sum(w) == 1, w >= 0, w <= max_weight],
        )

        assert prob.is_dpp()

        self.cvxpylayer = CvxpyLayer(
            prob, parameters=[rets, covmat_sqrt, alpha], variables=[w]
        )

    def forward(self, rets, covmat_sqrt, gamma_sqrt, alpha):
        """Perform forward pass.

        Parameters
        ----------
        rets : torch.Tensor
            Of shape (n_samples, n_assets) representing expected returns (or whatever the feature extractor decided
            to encode).

        covmat_sqrt : torch.Tensor
            Of shape (n_samples, n_assets, n_assets) representing the square of the covariance matrix.

        gamma_sqrt : torch.Tensor
            Of shape (n_samples,) representing the tradeoff between risk and return - where on efficient frontier
            we are.

        alpha : torch.Tensor
            Of shape (n_samples,) representing how much L2 regularization is applied to weights. Note that
            we pass the absolute value of this variable into the optimizer since when creating the problem
            we asserted it is going to be nonnegative.

        Returns
        -------
        weights : torch.Tensor
            Of shape (n_samples, n_assets) representing the optimal weights as determined by the convex optimizer.

        """
        n_samples, n_assets = rets.shape
        gamma_sqrt_ = gamma_sqrt.repeat((1, n_assets * n_assets)).view(
            n_samples, n_assets, n_assets
        )
        alpha_abs = torch.abs(alpha)  # it needs to be nonnegative

        return self.cvxpylayer(rets, gamma_sqrt_ * covmat_sqrt, alpha_abs)[0]


class Resample(nn.Module):
    """Meta allocator that bootstraps the input expected returns and covariance matrix.

    The idea is to take the input covmat and expected returns and view them as parameters of a Multivariate
    Normal distribution. After that, we iterate the below steps `n_portfolios` times:

        1. Sample `n_draws` from the distribution
        2. Estimate expected_returns and covariance matrix
        3. Use the `allocator` to compute weights.

    This will results in `n_portfolios` portfolios that we simply average to get the final weights.

    Parameters
    ----------
    allocator : AnalyticalMarkowitz or NCO or NumericalMarkowitz
        Instance of an allocator.

    n_draws : int or None
        Number of draws. If None then set equal to number of assets to prevent numerical problems.

    n_portfolios : int
        Number of samples.

    sqrt : bool
        If True, then the input array represent the square root of the covariance matrix. Else it is the actual
        covariance matrix.

    random_state : int or None
        Random state (forward passes with same parameters will have same results).

    References
    ----------
    [1] Michaud, Richard O., and Robert Michaud.
        "Estimation error and portfolio optimization: a resampling solution."
        Available at SSRN 2658657 (2007)
    """

    def __init__(
        self,
        allocator,
        n_draws=None,
        n_portfolios=5,
        sqrt=False,
        random_state=None,
    ):
        super().__init__()

        if not isinstance(
            allocator, (AnalyticalMarkowitz, NCO, NumericalMarkowitz)
        ):
            raise TypeError(
                "Unsupported type of allocator: {}".format(type(allocator))
            )

        self.allocator = allocator
        self.sqrt = sqrt
        self.n_draws = n_draws
        self.n_portfolios = n_portfolios
        self.random_state = random_state

        mapper = {
            "AnalyticalMarkowitz": False,
            "NCO": True,
            "NumericalMarkowitz": True,
        }

        self.uses_sqrt = mapper[allocator.__class__.__name__]

    def forward(self, matrix, rets=None, **kwargs):
        """Perform forward pass.

        Only accepts keyword arguments to avoid ambiguity.

        Parameters
        ----------
        matrix : torch.Tensor
            Of shape (n_samples, n_assets, n_assets) representing the square of the covariance matrix if
            `self.square=True` else the covariance matrix itself.

        rets : torch.Tensor or None
            Of shape (n_samples, n_assets) representing expected returns (or whatever the feature extractor decided
            to encode). Note that `NCO` and `AnalyticalMarkowitz` allow for `rets=None` (using only minimum variance).

        kwargs : dict
            All additional input arguments the `self.allocator` needs to perform forward pass.

        Returns
        -------
        weights : torch.Tensor
            Of shape (n_samples, n_assets) representing the optimal weights.

        """
        if self.random_state is not None:
            torch.manual_seed(self.random_state)

        n_samples, n_assets, _ = matrix.shape
        dtype, device = matrix.dtype, matrix.device
        n_draws = (
            self.n_draws or n_assets
        )  # make sure that if None then we have the same N=M

        covmat = matrix @ matrix if self.sqrt else matrix
        dist_rets = (
            torch.zeros(n_samples, n_assets, dtype=dtype, device=device)
            if rets is None
            else rets
        )

        dist = MultivariateNormal(loc=dist_rets, covariance_matrix=covmat)

        portfolios = []  # n_portfolios elements of (n_samples, n_assets)

        for _ in range(self.n_portfolios):
            draws = dist.rsample((n_draws,))  # (n_draws, n_samples, n_assets)
            rets_ = (
                draws.mean(dim=0) if rets is not None else None
            )  # (n_samples, n_assets)
            covmat_ = CovarianceMatrix(sqrt=self.uses_sqrt)(
                draws.permute(1, 0, 2)
            )  # (n_samples, n_assets, ...)

            if isinstance(self.allocator, (AnalyticalMarkowitz, NCO)):
                portfolio = self.allocator(covmat=covmat_, rets=rets_)

            elif isinstance(self.allocator, NumericalMarkowitz):
                gamma = kwargs["gamma"]
                alpha = kwargs["alpha"]
                portfolio = self.allocator(rets_, covmat_, gamma, alpha)

            portfolios.append(portfolio)

        portfolios_t = torch.stack(
            portfolios, dim=0
        )  # (n_portfolios, n_samples, n_assets)

        return portfolios_t.mean(dim=0)


class SoftmaxAllocator(torch.nn.Module):
    """Portfolio creation by computing a softmax over the asset dimension with temperature.

    Parameters
    ----------
    temperature : None or float
        If None, then needs to be provided per sample during forward pass. If ``float`` then assumed
        to be always the same.

    formulation : str, {'analytical', 'variational'}
        Controls what way the problem is solved. If 'analytical' then using an explicit formula,
        however, one cannot decide on a `max_weight` different than 1. If `variational` then solved
        via convex optimization and one can set any `max_weight`.

    n_assets : None or int
        Only required and used if `formulation='variational`.

    max_weight : float
        A float between (0, 1] representing the maximum weight per asset.

    """

    def __init__(
        self,
        temperature=1,
        formulation="analytical",
        n_assets=None,
        max_weight=1,
    ):
        super().__init__()

        self.temperature = temperature

        if formulation not in {"analytical", "variational"}:
            raise ValueError("Unrecognized formulation {}".format(formulation))

        if formulation == "variational" and n_assets is None:
            raise ValueError(
                "One needs to provide n_assets for the variational formulation."
            )

        if formulation == "analytical" and max_weight != 1:
            raise ValueError(
                "Cannot constraint weights via max_weight for analytical formulation"
            )

        if formulation == "variational" and n_assets * max_weight < 1:
            raise ValueError(
                "One cannot create fully invested portfolio with the given max_weight"
            )

        self.formulation = formulation

        if formulation == "analytical":
            self.layer = torch.nn.Softmax(dim=1)
        else:
            x = cp.Parameter(n_assets)
            w = cp.Variable(n_assets)
            obj = -x @ w - cp.sum(cp.entr(w))
            cons = [cp.sum(w) == 1.0, w <= max_weight]
            prob = cp.Problem(cp.Minimize(obj), cons)
            self.layer = CvxpyLayer(prob, [x], [w])

    def forward(self, x, temperature=None):
        """Perform forward pass.

        Parameters
        ----------
        x : torch.Tensor
            Tensor of shape `(n_samples, n_assets`).

        temperature : None or torch.Tensor
            If None, then using the `temperature` provided at construction time. Otherwise a `torch.Tensor` of shape
            `(n_samples,)` representing a per sample temperature.

        Returns
        -------
        weights : torch.Tensor
            Tensor of shape `(n_samples, n_assets`).

        """
        n_samples, _ = x.shape
        device, dtype = x.device, x.dtype

        if not ((temperature is None) ^ (self.temperature is None)):
            raise ValueError("Not clear which temperature to use")

        if temperature is not None:
            temperature_ = temperature  # (n_samples,)
        else:
            temperature_ = float(self.temperature) * torch.ones(
                n_samples, dtype=dtype, device=device
            )

        inp = x / temperature_[..., None]

        return (
            self.layer(inp)
            if self.formulation == "analytical"
            else self.layer(inp)[0]
        )


class SparsemaxAllocator(torch.nn.Module):
    """Portfolio creation by computing a sparsemax over the asset dimension with temperature.

    Parameters
    ----------
    n_assets : int
        Number of assets. Note that we require this quantity at construction to make sure
        the underlying cvxpylayer does not need to be reinitialized every forward pass.

    temperature : None or float
        If None, then needs to be provided per sample during forward pass. If ``float`` then
        assumed to be always the same.

    max_weight : float
        A float between (0, 1] representing the maximum weight per asset.

    References
    ----------
    [1] Martins, Andre, and Ramon Astudillo. "From softmax to sparsemax: A sparse model of attention
    and multi-label classification." International Conference on Machine Learning. 2016.

    [2] Malaviya, Chaitanya, Pedro Ferreira, and André FT Martins. "Sparse and constrained attention
    for neural machine translation." arXiv preprint arXiv:1805.08241 (2018)
    """

    def __init__(self, n_assets, temperature=1, max_weight=1):
        super().__init__()

        if n_assets * max_weight < 1:
            raise ValueError(
                "One cannot create fully invested portfolio with the given max_weight"
            )

        self.n_assets = n_assets
        self.temperature = temperature

        # Construct convex optimization problem
        x = cp.Parameter(n_assets)
        w = cp.Variable(n_assets)
        obj = cp.sum_squares(x - w)
        cons = [cp.sum(w) == 1, 0.0 <= w, w <= max_weight]
        prob = cp.Problem(cp.Minimize(obj), cons)

        self.layer = CvxpyLayer(prob, parameters=[x], variables=[w])

    def forward(self, x, temperature=None):
        """Perform forward pass.

        Parameters
        ----------
        x : torch.Tensor
            Tensor of shape `(n_samples, n_assets`).

        temperature : None or torch.Tensor
            If None, then using the `temperature` provided at construction time. Otherwise a
            `torch.Tensor` of shape `(n_samples,)` representing a per sample temperature.

        Returns
        -------
        weights : torch.Tensor
            Tensor of shape `(n_samples, n_assets`).

        """
        n_samples, _ = x.shape
        device, dtype = x.device, x.dtype

        if not ((temperature is None) ^ (self.temperature is None)):
            raise ValueError("Not clear which temperature to use")

        if temperature is not None:
            temperature_ = temperature  # (n_samples,)
        else:
            temperature_ = float(self.temperature) * torch.ones(
                n_samples, dtype=dtype, device=device
            )

        inp = x / temperature_[..., None]

        return self.layer(inp)[0]


class WeightNorm(torch.nn.Module):
    """Allocation via weight normalization.

    We learn a single weight for each asset and make sure that they sum up to one.
    """

    def __init__(self, n_assets):
        super().__init__()
        self.asset_weights = torch.nn.Parameter(
            torch.ones(n_assets), requires_grad=True
        )

    def forward(self, x):
        """Perform forward pass.

        Parameters
        ----------
        x : torch.Tensor
            Tensor of shape `(n_samples, dim_1, ...., dim_N)`.

        Returns
        -------
        weights : torch.Tensor
            Tensor of shape `(n_samples, n_assets`).

        """
        n_samples = x.shape[0]
        clamped = torch.clamp(self.asset_weights, min=0)
        normalized = clamped / clamped.sum()

        return torch.stack(n_samples * [normalized], dim=0)


class NumericalRiskBudgeting(nn.Module):
    """Convex optimization layer stylized into portfolio optimization problem.

    Parameters
    ----------
    n_assets : int
        Number of assets.

    Attributes
    ----------
    cvxpylayer : CvxpyLayer
        Custom layer used by a third party package called cvxpylayers.

    References
    ----------
    [1] https://github.com/cvxgrp/cvxpylayers
    [2] https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2297383
    [3] https://mpra.ub.uni-muenchen.de/37749/2/MPRA_paper_37749.pdf
    """

    def __init__(self, n_assets, max_weight=1):
        """Construct."""
        super().__init__()
        covmat_sqrt = cp.Parameter((n_assets, n_assets))
        b = cp.Parameter(n_assets, nonneg=True)

        w = cp.Variable(n_assets)

        term_1 = 0.5 * cp.sum_squares(covmat_sqrt @ w)
        term_2 = b @ cp.log(w)

        objective = cp.Minimize(term_1 - term_2)  # refer [2]
        constraint = [cp.sum(w) == 1, w >= 0, w <= max_weight]  # refer [2]

        prob = cp.Problem(objective, constraint)

        assert prob.is_dpp()

        self.cvxpylayer = CvxpyLayer(
            prob, parameters=[covmat_sqrt, b], variables=[w]
        )

    def forward(self, covmat_sqrt, b):
        """Perform forward pass.

        Parameters
        ----------
        covmat : torch.Tensor
            Of shape (n_samples, n_assets, n_assets) representing the covariance matrix.

        b : torch.Tensor
            Of shape (n_samples, n_assets) representing the budget,
            risk contribution from each component (asset) is equal to the budget, refer [3]

        Returns
        -------
        weights : torch.Tensor
            Of shape (n_samples, n_assets) representing the optimal weights as determined by the convex optimizer.

        """
        return self.cvxpylayer(covmat_sqrt, b)[0]
