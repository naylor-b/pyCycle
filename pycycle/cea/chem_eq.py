import numpy as np

import openmdao.api as om

from pycycle.constants import P_REF, R_UNIVERSAL_ENG, MIN_VALID_CONCENTRATION

# P_REF = 1.01325 # 1 atm
# R_UNIVERSAL_ENG = 1.9872035 # (Btu lbm)/(mol*degR)
# MIN_VALID_CONCENTRATION = 1e-10


def _resid_weighting(n):
    np.seterr(under='ignore')
    return (1 / (1 + np.exp(-1e5 * n)) - .5) * 2


class ChemEq(om.ImplicitComponent):
    """ Find the equilibirum composition for a given gaseous mixture """

    def guess_nonlinear(self, inputs, outputs, resids):
        norm = resids.get_norm()
        if norm > 1e-2 or norm==0.0 or np.any(outputs['n'] < 0):
            outputs['n'] = self.n_init
            if self.options['mode'] != 'T':
                outputs['T'] = 1000.

    def initialize(self):
        self.options.declare('thermo', desc='thermodynamic data object', recordable=False)
        self.options.declare('mode',
                              desc='the input variable that defines the total properties',
                              default='T',
                              values=('T', 'S', 'h'))

    def setup(self):

        newton = self.nonlinear_solver = om.NewtonSolver()
        newton.options['maxiter'] = 100
        newton.options['iprint'] = 2
        newton.options['atol'] = 1e-10
        newton.options['rtol'] = 1e-10
        newton.options['solve_subsystems'] = True
        newton.options['reraise_child_analysiserror'] = False

        self.options['assembled_jac_type'] = 'dense'
        self.linear_solver = om.DirectSolver(assemble_jac=True)

        # ln_bt = newton.linesearch = om.BoundsEnforceLS()
        ln_bt = newton.linesearch = om.ArmijoGoldsteinLS()
        ln_bt.options['maxiter'] = 2
        ln_bt.options['bound_enforcement'] = 'scalar'
        ln_bt.options['iprint'] = -1

        # Once the concentration of a species reaches its minimum, we
        # can essentially remove it from the problem. This switch controls
        # whether to do this.
        self.remove_trace_species = False

        # multiply a damping function that scales down the residual for trace species
        self.use_trace_damping = True

        thermo = self.options['thermo']
        mode = self.options['mode']

        num_prod = thermo.num_prod
        num_element = thermo.num_element

        # Input vars
        self.add_input('b0', val=thermo.b0, desc='moles of atoms present in mixture')

        self.add_input('P', val=1.0, units="bar", desc="Pressure")

        if mode == "T":  # T is an input
            self.add_input('T', val=400., units="degK", desc="Temperature")
        else:  # T becomes another state variable
            if mode == "h":  # hP solve
                self.add_input('h', val=0., units="cal/g",
                               desc="Enthalpy")
            elif mode == "S":  # SP solve
                self.add_input('S', val=0., units="cal/(g*degK)",
                               desc="Entropy")

            self.T_idx = num_prod + num_element
            self.add_output('T', val=400., units="degK", desc="Temperature",
                            lower=1.,
                            res_ref=100
                            )

        # State vars
        self.n_init = np.ones(num_prod) / num_prod / 10  # initial guess for n

        # for a known solution, these are the orders of magnitude of the variables.
        # We'll try setting scaling to +/1 1 order around thee values

        mag = np.array([3.23319258e-04, 1.00000000e-10, 1.10131241e-05, 1.00000000e-10,
                        1.15755853e-08, 2.95692989e-09, 1.00000000e-10, 2.69578794e-02,
                        1.00000000e-10, 7.23198523e-03])

        #mag = np.ones(num_prod)
        self.add_output('n', shape=num_prod,
                        val=self.n_init,
                        desc="mole fractions of the mixture",
                        lower=1e-10,
                        res_ref=10000.
                        )

        self.add_output('pi', val=np.ones(num_element),
                        desc="modified lagrange multipliers from the Gibbs lagrangian")

        # Explicit Outputs
        self.add_output('n_moles', lower=1e-10, val=0.034, shape=1,
                        desc="1/molecular weight of gas")

        # allocate the newton Jacobian
        self.size = size = num_prod + num_element
        if mode != "T":
            size += 1  # added T as a state variable

        self._dRdy = np.zeros((size, size))
        self._rhs = np.zeros(size)  # used for solve_linear

        # Cached stuff for speed
        self.H0_T = None
        self.S0_T = None
        self.dH0_dT = None
        self.dS0_dT = None
        self.sum_n_H0_T = None

        # self.deriv_options['check_type'] = 'cs'
        # self.deriv_options['check_step_size'] = 1e-50
        # self.deriv_options['type'] = 'fd'
        # self.deriv_options['step_size'] = 1e-5

        self.declare_partials('n', ['n', 'pi', 'P', 'T'])
        self.declare_partials('pi', ['n', 'b0'])
        self.declare_partials('n_moles', 'n')
        self.declare_partials('n_moles', 'n_moles', val=-1)

        if mode == 'h':
            self.declare_partials('T', ['n', 'h', 'T'])
        elif mode == 'S':
            self.declare_partials('T', ['n', 'S', 'T', 'P'])

    def apply_nonlinear(self, inputs, outputs, resids):
        thermo = self.options['thermo']
        mode = self.options['mode']

        if mode == 'T':
            b0, P, T  = inputs.split_vals()
            n, pi, n_moles_out = outputs.split_vals()
        elif mode == 'h':
            b0, P, h  = inputs.split_vals()
            T, n, pi, n_moles_out = outputs.split_vals()
        else:  # S
            b0, P, S  = inputs.split_vals()
            T, n, pi, n_moles_out = outputs.split_vals()

        P = P / P_REF
        n_moles = np.sum(n)

        # Output equation for n_moles
        resids_n_moles = n_moles - n_moles_out

        try:
            self.H0_T = H0_T = thermo.H0(T)
            self.S0_T = S0_T = thermo.S0(T)
        except:
            raise AnalysisError('Bad Temp')
            # T[:] = 500.
            # self.H0_T = H0_T = thermo.H0(T)
            # self.S0_T = S0_T = thermo.S0(T)
        # np.seterr(all='warn')
        # self.mu = H0_T - S0_T + np.log(n) + np.log(P) - np.log(n_moles)

        try:
            np.seterr(all='raise')
            self.mu = H0_T - S0_T + np.log(n) + np.log(P) - np.log(n_moles)
            np.seterr(all='warn')
        except:
            print('ChemEQ error in: ', self.pathname)
            print('n', n)
            print('P', P)
            print('n_moles', n_moles)
            self.mu = H0_T - S0_T + np.log(n) + np.log(1e-5) - np.log(n_moles)
            np.seterr(all='warn')

        resids_n = (self.mu - np.sum(pi * thermo.aij.T, axis=1))
        if self.use_trace_damping:
            self.weights = _resid_weighting(n * n_moles)
            resids_n *= self.weights

        # Zero out resids when a concentration drops too low.
        if self.remove_trace_species:
            # for j, composition in enumerate(n):
            #     if composition <= 1.0e-10:
            #         resids['n'][j] = 0.0
            self._trace = np.where(n <= MIN_VALID_CONCENTRATION+1e-20)
            resids_n[self._trace] = 0.

        # residuals from the conservation of mass
        resids_pi = np.sum(thermo.aij * n, axis=1) - b0

        # residuals from temperature equation when T is a state
        if mode == 'T':
            resids.join_vals(resids_n, resids_pi, resids_n_moles)
        elif mode == "h":
            self.sum_n_H0_T = np.sum(n * H0_T)
            resids_T = (h - self.sum_n_H0_T * R_UNIVERSAL_ENG * T)/h
            resids.join_vals(resids_T, resids_n, resids_pi, resids_n_moles)
        else:  # mode == "S"
            resids_T = (S-R_UNIVERSAL_ENG*np.sum(n*(S0_T-np.log(n)+np.log(n_moles)-np.log(P))))/S
            resids.join_vals(resids_T, resids_n, resids_pi, resids_n_moles)

        if np.linalg.norm(resids['n']) < 1e-4:
            self.remove_trace_species = True
        else:
            self.remove_trace_species = False

    def linearize(self, inputs, outputs, J):

        self._calc_dRdy(inputs, outputs)
        dRdy = self._dRdy

        mode = self.options['mode']
        thermo = self.options['thermo']

        num_element = thermo.num_element
        num_prod = thermo.num_prod

        P = inputs['P'] / P_REF
        n = outputs['n']
        n_moles = np.sum(n)

        # TODO: Talk to John about this problem.
        # hack to handle the fact that n_moles doesn't get set if you only call apply_linear
        if n_moles < 1e-30:
            n_moles = np.sum(n)

        qP = 1.0 / P_REF / P  # quotient_P or 1/P

        end_element = num_prod + num_element

        J_n_n = dRdy[:num_prod, :num_prod]

        J_n_pi = dRdy[:num_prod, num_prod: end_element]

        J_n_n_moles = -n/n_moles

        if self.use_trace_damping:
            J_n_P = (self.weights * qP).reshape((-1, 1))
        else:
            J_n_P = qP*np.ones((num_prod, 1))

        # can only use the dRdy vals when T is a state. Otherwise its not computed
        if mode != 'T':
            J_n_T = dRdy[:num_prod, -1].reshape((num_prod, 1))
        else:
            T = inputs['T']
            dH0_dT = thermo.H0_applyJ(T, 1)
            dS0_dT = thermo.S0_applyJ(T, 1)
            if self.use_trace_damping:
                J_n_T = ((dH0_dT - dS0_dT) * self.weights).reshape((num_prod, 1))
            else:
                J_n_T = ((dH0_dT - dS0_dT)).reshape((num_prod, 1))

        J['pi', 'n'] = dRdy[num_prod:end_element, :num_prod]
        J['pi', 'b0'] = -np.eye(num_element)

        if mode == 'h':
            J['T', 'n'] = dRdy[-1, :num_prod].reshape(1, num_prod)
            J['T', 'h'] = (self.sum_n_H0_T * R_UNIVERSAL_ENG * outputs['T'])/inputs['h']**2
            J['T', 'T'] = dRdy[-1, -1]

        elif mode == 'S':
            S = inputs['S']
            J['T', 'n'] = dRdy[-1, :num_prod].reshape(1, num_prod)

            tmp = np.sum(n*(self.S0_T - np.log(n) + np.log(n_moles) - np.log(P)))
            J['T', 'S'] = (R_UNIVERSAL_ENG*tmp)/S**2

            J['T', 'T'] = dRdy[-1, -1]
            J['T', 'P'] = R_UNIVERSAL_ENG * n_moles / (P * S * P_REF)

        J['n_moles', 'n'] = np.ones((1, num_prod))

        if self.remove_trace_species:
            # non-vectorized loop; left here for code clarity
            # for j, is_trace in enumerate(self._trace):
            #     if is_trace:
            #         J_n_n[:, j] = 0.
            #         J_n_n[j, :] = 0.
            #         J_n_n[j, j] = 1.

            #         J['n', 'P'][j, :] = 0
            #         J['n', 'T'][j, :] = 0
            #         J['n', 'pi'][j, :] = 0

            #         J['pi', 'n'][:, j] = 0.
            #         if self.mode == "h" or self.mode == "S":
            #             J['T', 'n'][:, j] = 0.

            mask = self._trace
            J_n_n[:, mask] = 0.
            J_n_n[mask, :] = 0.
            J_n_n[mask, mask] = 1.

            J_n_P[mask, :] = 0
            J_n_T[mask, :] = 0
            J_n_pi[mask, :] = 0
            J_n_n_moles[mask] = 0

            # J['pi', 'n'][:, mask] = 0.
            # if self.mode == "h" or self.mode == "S":
                # J['T', 'n'][:, mask] = 0.

        J['n', 'n'] = J_n_n
        J['n', 'P'] = J_n_P
        J['n', 'T'] = J_n_T
        J['n', 'pi'] = J_n_pi

    def _calc_dRdy(self, inputs, outputs):
        """ Computes the Jacobian for the newton solver. This Jacobian
        contains the derivatives of all residual equations with respect to
        the state variables, which are ['n', 'pi', and sometimes 'T'] """

        thermo = self.options['thermo']
        aij = thermo.aij
        num_prod = thermo.num_prod
        num_element = thermo.num_element
        mode = self.options['mode']

        n = outputs['n']
        n_moles = np.sum(n)
        # pi = outputs['pi']

        if outputs._under_complex_step:
            dRdy = self._dRdy = self._dRdy.astype(np.complex)
            if self.use_trace_damping:
                self.weights = self.weights.astype(np.complex)
        else:
            dRdy = self._dRdy = self._dRdy.real
            if self.use_trace_damping:
                self.weights = self.weights.real

        # dRgibbs_dn

        MW = 1 / n_moles
        dRdy[:num_prod, :num_prod] = (-MW)
        diag = (1 / n - MW)
        np.fill_diagonal(dRdy[:num_prod, :num_prod], diag)
        # multiples each row by one element of the vector
        if self.use_trace_damping:
            dRdy[:num_prod, :num_prod] *= self.weights[:, np.newaxis]

        end_element = num_prod + num_element
        # dRgibbs_dpi
        dRdy[:num_prod, num_prod:end_element] = (-aij.T)
        if self.use_trace_damping:
            dRdy[:num_prod, num_prod:end_element] *= self.weights[:, np.newaxis]

        if mode != "T":
            # dRgibbs_dT
            T = outputs['T']
            self.dH0_dT = thermo.H0_applyJ(T, 1)
            self.dS0_dT = thermo.S0_applyJ(T, 1)
            dRdy[:num_prod, -1] = (self.dH0_dT - self.dS0_dT)
            if self.use_trace_damping:
                dRdy[:num_prod, -1] *= self.weights
            # dRmass_dT = 0

        if mode == "h":
            h = inputs['h']
            # dRT_dn
            dRdy[-1, :num_prod] = (- R_UNIVERSAL_ENG * T * self.H0_T)/h
            # dRT_dT
            dRdy[-1, -1] = (-R_UNIVERSAL_ENG *
                            (T * np.sum(n * self.dH0_dT) + self.sum_n_H0_T))/h

        elif mode == "S":
            P = inputs['P'] / P_REF
            n_moles = np.sum(n)  # outputs['n_moles']
            S = inputs['S']
            # dRT_dn
            dRdy[-1, :num_prod] = -R_UNIVERSAL_ENG * \
                (self.S0_T - np.log(n) + np.log(n_moles)-np.log(P))/S
            # dRT_dT
            # uc*(S0_T + np.log(sum_nj) - np.log(P) - np.log(nj))
            # valid_products = n > MIN_VALID_CONCENTRATION
            dRdy[-1, -1] = -R_UNIVERSAL_ENG * np.sum(n * self.dS0_dT) / S

        # dRmass_dn
        dRdy[num_prod:end_element, :num_prod] = aij

        # Replace J for tiny values of n with identity
        if self.remove_trace_species:
            n = outputs['n']
            for j in range(num_prod):
                if n[j] <= 1.0e-10:
                    dRdy[j, :] = 0.0
                    dRdy[j, j] = -1.0


if __name__ == "__main__":
    import time


    from pycycle.cea import species_data

    # thermo = species_data.Thermo(species_data.co2_co_o2)
    thermo = species_data.Thermo(species_data.janaf)

    prob = om.Problem()
    prob.model = om.Group()
    prob.model.nonlinear_solver = om.NewtonSolver(solve_subsystems=True)
    prob.model.linear_solver = om.LinearRunOnce()

    des_vars = prob.model.add_subsystem('des_vars', om.IndepVarComp(), promotes=["*"])
    des_vars.add_output('P', 1.034210, units='psi')
    des_vars.add_output('h', -24.26682261, units='cal/g')

    chemeq = prob.model.add_subsystem('chemeq', ChemEq(thermo=thermo, mode="h"), promotes=["*"])

    # prob.model.suppress_solver_output = True
    prob.setup(force_alloc_complex=True)

    st = time.time()
    prob.run_model()
    print("time: ", time.time()-st)
    print('n', prob['n'])
    print('T', prob['T'])
    print('b0', prob['b0'])
    print('n_moles', prob['n_moles'])
    print('pi', prob['pi'])

    # print(prob['T'], prob.model._residuals['T'])
    # print(prob['n'], prob.model._residuals['n'])
    # print(prob['pi'], prob.model._residuals['pi'])


    prob.check_partials(method='cs', compact_print=True)
