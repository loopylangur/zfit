#  Copyright (c) 2020 zfit

from collections import OrderedDict
from typing import List

import iminuit
import numpy as np

from zfit.core.interfaces import ZfitLoss
from .baseminimizer import BaseMinimizer, ZfitStrategy, print_params, print_gradients
from .fitresult import FitResult
from ..core.parameter import Parameter
from ..util.cache import Cachable


class Minuit(BaseMinimizer, Cachable):
    _DEFAULT_name = "Minuit"

    def __init__(self, strategy: ZfitStrategy = None, minimize_strategy: int = 1, tolerance: float = None,
                 verbosity: int = 5, name: str = None,
                 ncall: int = 10000, **minimizer_options):
        """

        Args:
            strategy (): A :py:class:`~zfit.minimizer.baseminimizer.ZfitStrategy` object that defines the behavior of
            the minimizer in certain situations.
            minimize_strategy (): A number used by minuit to define the strategy
            tolerance (): Internal numerical tolerance
            verbosity (): Regulates how much will be printed during minimization. Values between 0 and 10 are valid.
            name (): Name of the minimizer
            ncall (): Maximum number of minimization steps.
        """
        minimizer_options['ncall'] = ncall
        if not minimize_strategy in range(3):
            raise ValueError(f"minimize_strategy has to be 0, 1 or 2, not {minimize_strategy}.")
        minimizer_options['strategy'] = minimize_strategy

        super().__init__(name=name, strategy=strategy, tolerance=tolerance, verbosity=verbosity,
                         minimizer_options=minimizer_options)
        self._minuit_minimizer = None
        self._use_tfgrad = True

    def _minimize(self, loss: ZfitLoss, params: List[Parameter]):
        # loss_val = loss.value()
        # gradients = loss.gradients(params)
        # self._check_gradients(params=params, gradients=gradients)

        # load_params = self._extract_load_method(params=params)  REMOVE

        # create options
        minimizer_options = self.minimizer_options.copy()
        minimize_options = {}
        minimize_options['precision'] = minimizer_options.pop('precision', None)
        minimize_options['ncall'] = minimizer_options.pop('ncall')

        minimizer_init = {}
        if 'errordef' in minimizer_options:
            raise ValueError("errordef cannot be specified for Minuit as this is already defined in the Loss.")
        loss_errordef = loss.errordef
        if not isinstance(loss_errordef, (float, int)):
            loss_errordef = 1.0  # default of minuit
        minimizer_init['errordef'] = loss_errordef
        minimizer_init['pedantic'] = minimizer_options.pop('pedantic', False)

        minimizer_setter = {}
        minimizer_setter['strategy'] = minimizer_options.pop('strategy')
        if self.verbosity > 7:
            minuit_verbosity = self.verbosity
        elif self.verbosity > 2:
            minuit_verbosity = 1
        else:
            minuit_verbosity = 0
        if minimizer_options:
            raise ValueError("The following options are not (yet) supported: {}".format(minimizer_options))

        # create Minuit compatible names
        limits = tuple(tuple((param.lower_limit, param.upper_limit)) for param in params)
        errors = tuple(param.step_size for param in params)
        start_values = [p.numpy() for p in params]
        limits = [(low.numpy(), up.numpy()) for low, up in limits]
        errors = [err.numpy() for err in errors]

        multiparam = isinstance(start_values[0], np.ndarray) and len(start_values[0]) > 1 and len(params) == 1
        if multiparam:
            # TODO(Mayou36): multiparameter
            params_name = None  # autogenerate for the moment
            start_values = start_values[0]
            errors = errors[0]
            limits = limits[0]
            gradients = gradients[0]
        else:
            params_name = [param.name for param in params]

        def func(values):
            self._update_params(params=params, values=values)
            do_print = self.verbosity > 5

            try:
                loss_evaluated = loss.value().numpy()
            except:
                loss_evaluated = "invalid, error occured"
                raise
            finally:
                if do_print:
                    print_params(params, values, loss_evaluated)
            if np.isnan(loss_evaluated):
                self.strategy.minimize_nan(loss=loss, minimizer=self, loss_value=loss_evaluated, params=params)
            return loss_evaluated

        def grad_func(values):
            self._update_params(params=params, values=values)
            do_print = self.verbosity > 5

            try:
                gradients = loss.gradients(params=params)
                gradients_values = [float(g.numpy()) for g in gradients]
            except:
                gradients_values = ["invalid"] * len(params)
                raise
            finally:
                if do_print:
                    print_gradients(params, values, gradients_values)

            if any(np.isnan(gradients_values)):
                self.strategy.minimize_nan(loss=loss, minimizer=self, gradient_values=gradients_values, params=params)
            return gradients_values

        grad_func = grad_func if self._use_tfgrad else None

        minimizer = iminuit.Minuit.from_array_func(fcn=func, start=start_values,
                                                   error=errors, limit=limits, name=params_name,
                                                   grad=grad_func,
                                                   # use_array_call=True,
                                                   print_level=minuit_verbosity,
                                                   # forced_parameters=[f"param_{i}" for i in range(len(start_values))],
                                                   **minimizer_init)

        strategy = minimizer_setter.pop('strategy')
        minimizer.set_strategy(strategy)
        minimizer.tol = self.tolerance / 1e-3  # iminuit 1e-3 and tolerance 0.1
        assert not minimizer_setter, "minimizer_setter is not empty, bug. Please report. minimizer_setter: {}".format(
            minimizer_setter)
        self._minuit_minimizer = minimizer
        result = minimizer.migrad(**minimize_options)
        params_result = [p_dict for p_dict in result[1]]
        result_vals = [res["value"] for res in params_result]
        self._update_params(params, values=result_vals)

        info = {'n_eval': result[0]['nfcn'],
                'n_iter': result[0]['ncalls'],
                # 'grad': result['jac'],
                # 'message': result['message'],
                'original': result[0]}
        edm = result[0]['edm']
        fmin = result[0]['fval']
        status = -999
        converged = result[0]['is_valid']
        params = OrderedDict((p, res['value']) for p, res in zip(params, params_result))
        result = FitResult(params=params, edm=edm, fmin=fmin, info=info, loss=loss,
                           status=status, converged=converged,
                           minimizer=self.copy())
        return result

    def copy(self):
        tmp_minimizer = self._minuit_minimizer
        self._minuit_minimizer = None
        new_minimizer = super().copy()
        new_minimizer._minuit_minimizer = tmp_minimizer
        return new_minimizer
