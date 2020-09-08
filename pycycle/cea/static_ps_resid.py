import numpy as np
from scipy.optimize import fsolve

import openmdao.api as om

from pycycle.constants import R_UNIVERSAL_SI

class PsResid(om.ImplicitComponent):
    """Actual implicit relationship for when Mach number is specified"""

    def initialize(self):
        self.options.declare('mode', values=['MN', 'area'])

    def setup(self):


        self.add_input('Ts', val=518., units="degK", desc="Static temp")
        self.add_input('ht', val=1., units="J/kg", desc="Total enthalpy reference condition")
        self.add_input('hs', val=1., units="J/kg", desc="Static enthalpy")
        self.add_input('n_moles', shape=1)
        self.add_input('gamma', val=1.4)
        self.add_input('W', val=1., desc="mass flow rate", units="kg/s")
        self.add_input('rho', val=1., desc="density", units="kg/m**3")

        # used for computing initial guess
        self.add_input('guess:gamt', val=1.4, desc="gamma computed from set total")
        self.add_input('guess:Pt', val=1.0, units="bar", desc="total pressure")

        self.add_output('Ps', lower=1e-4, upper=5e4, val=.001, units="bar",
                        desc="static pressure state variable",
                        ref0=1e-3)
        self.add_output('V', val=100.0, shape=1, desc="velocity", units="m/s",
                        res_ref=1e3)
        self.add_output('Vsonic', val=330.0, shape=1, desc="computed speed of sound", units="m/s",
                        res_ref=1e3)

        self.declare_partials('Ps', ['ht', 'hs'])
        self.declare_partials('Vsonic', ['gamma', 'n_moles', 'Ts', 'Vsonic'])
        self.declare_partials('V', 'V', val=-1.0)

        mode = self.options['mode']
        if mode == "MN":
            self.add_input('MN', val=.5, desc="target mach number")
            self.add_output('area', shape=1, desc="flow area", units="m**2", lower=1e-5)

            self.declare_partials('area', ['area', 'W', 'rho', 'gamma', 'n_moles', 'Ts', 'MN'])
            self.declare_partials('Ps', ['MN', 'n_moles', 'gamma', 'Ts'])
            self.declare_partials('V', ['MN', 'n_moles', 'gamma', 'Ts'])

        elif mode == "area":
            self.add_output('MN', val=.5, desc="target mach number", lower=1e-3)
            self.add_input('guess:MN', val=0.5, desc="Guess for Mach number.")
            self.add_input('area', val=np.inf, desc="flow area", units="m**2")

            self.declare_partials('MN', ['MN', 'area', 'Ts', 'n_moles', 'W', 'gamma', 'rho'])
            self.declare_partials('Ps', ['area', 'Ts', 'n_moles', 'W', 'gamma', 'rho'])
            self.declare_partials('V', ['area', 'Ts', 'n_moles', 'W', 'gamma', 'rho'])

        else:
            raise ValueError('mode must be either "MN" or "area", but "%s" was given' % mode)

        # self.deriv_options['check_type'] = 'cs'
        # self.deriv_options['check_step_size'] = 1e-40
        # self.deriv_options['check_type'] = 'fd'
        # self.deriv_options['check_step_size'] = 1e-3

        # self.deriv_options['type'] = 'fd'
        # self.deriv_options['step_size'] = 1e-7

        # cache the old guess and only re-apply it if it changes.
        # This lets us start from our last converged point, but
        # only if a new guess isn't present. If a new guess is present,
        # its a safe bet that we've moved a lot and our old converged point isn't
        # as good as our new guess
        self._ps_guess_cache = -1.

    def guess_nonlinear(self, inputs, outputs, resids):
        gamt = inputs['guess:gamt']
        if self.options['mode'] == "MN":
            ps_guess = inputs['guess:Pt'] * (1 + (gamt-1)/2 * inputs['MN']**2)**(-gamt/(gamt-1))
            if np.abs(ps_guess - self._ps_guess_cache) > 1e-10:
                if self._ps_guess_cache == -1:
                    outputs['Ps'] = ps_guess
                    self._ps_guess_cache = ps_guess

        else:
            def equations(params):
                ps, MN = params
                f1 = ps - inputs['guess:Pt'] * (1 + (gamt-1)/2 * M_guess**2)**(-gamt/(gamt-1))
                f2 = MN - inputs['W']*(R_UNIVERSAL_SI*inputs['Ts'])**0.5/(ps_guess*1.0e6*inputs['area']*gamt**0.5)
                return (f1[0], f2[0])

            M_guess = inputs['guess:MN']
            ps_guess = inputs['guess:Pt'] * (1 + (gamt-1)/2 * M_guess**2)**(-gamt/(gamt-1))
            ps_guess, M_guess = fsolve(equations, (ps_guess, M_guess))

            if np.abs(ps_guess - self._ps_guess_cache) > 1e-10:
                outputs['Ps'] = ps_guess
                if ('mixer.Fl_I1_calc' in self.pathname):
                    outputs['Ps'] = 3.
                self._ps_guess_cache = ps_guess

    def _compute_outputs_MN(self, Ts, n_moles, gamma, W, rho, MN):
        Vsonic = (gamma*R_UNIVERSAL_SI*n_moles*Ts)**0.5
        try:
            np.seterr(all='raise')
            Vsonic = (gamma*R_UNIVERSAL_SI*n_moles*Ts)**0.5
            np.seterr(all='warn')
        except:
            np.seterr(all='warn')
            print(self.pathname, gamma, n_moles, Ts)

        if MN < 1e-16:
            area = np.inf
        else:
            area = W/(rho*Vsonic*MN)

        V = MN*Vsonic
        return Vsonic, V, area

    def _compute_outputs_area(self, Ts, n_moles, gamma, W, rho, area):
        Vsonic = (gamma*R_UNIVERSAL_SI*n_moles*Ts)**0.5
        if area == np.inf:
            MN = 0.
        else:
            #MN = W/(rho*Vsonic*area)
            #print("MN_calc", self.pathname, W, rho, Vsonic, area)

            try:
                np.seterr(all='raise')
                MN = W/(rho*Vsonic*area)
                np.seterr(all='warn')
            except:
                np.seterr(all='warn')
                print("MN_calc", self.pathname, W, rho, Vsonic, area)
                MN = 5.

        V = MN*Vsonic

        return MN, Vsonic, V

    def solve_nonlinear(self, inputs, outputs):

        try:
            if self.options['mode'] == "MN":
                Ts, _, _, n_moles, gamma, W, rho, _, _, MN = inputs.values()
                Vsonic, V, area = self._compute_outputs_MN(Ts, n_moles, gamma, W, rho, MN)
                outputs.set_values(outputs['Ps'], V, Vsonic, area)
            else:
                Ts, _, _, n_moles, gamma, W, rho, _, _, _, area = inputs.values()
                MN, Vsonic, V = self._compute_outputs_area(Ts, n_moles, gamma, W, rho, area)
                outputs.set_values(outputs['Ps'], V, Vsonic, MN)
        except FloatingPointError:
            raise om.AnalysisError('Bad values flow states in {}: Ts={}'.format(self.pathname, inputs['Ts']))

    def apply_nonlinear(self, inputs, outputs, resids):
        MN_mode = self.options['mode'] == "MN"
        # explicit vars
        if MN_mode:
            Ts, ht, hs, n_moles, gamma, W, rho, _, _, MN = inputs.values()
            Ps, outs_V, outs_Vsonic, outs_area = outputs.values()
            Vsonic, V, area = self._compute_outputs_MN(Ts, n_moles, gamma, W, rho, MN)
            if area != np.inf:
                res_area = area - outs_area
            else:
                res_area = 0.
        else:
            Ts, ht, hs, n_moles, gamma, W, rho, _, _, _, area = inputs.values()
            Ps, outs_V, outs_Vsonic, outs_MN = outputs.values()
            MN, Vsonic, V = self._compute_outputs_area(Ts, n_moles, gamma, W, rho, area)
            res_MN = MN - outs_MN
            # print "MN resid", self.pathname, MN, outs_MN

        res_Vsonic = Vsonic - outs_Vsonic
        res_V = V - outs_V
        # print(res_Vsonic, res_V, resids['area'])

        # actual residual for Ps
        RT_q_MW = R_UNIVERSAL_SI*Ts*n_moles
        MN_squared_q2 = (MN**2)/2.
        # self.dh_dlnP = RT_q_MW*(1+MN_squared_q2*(gamma-1))
        ht_calc = hs + MN_squared_q2 * gamma * RT_q_MW
        # ^ TN_D-132 Equation (85) for h*

        res_Ps = (ht_calc - ht)/ht
        # print "foobar", self.pathname, Ps, res_Ps, ht_calc, ht

        # try:
        #     np.seterr(all="raise")
        #     res_Ps = (self.ht_calc - ht)/ht
        #     np.seterr(all="ignore")
        # except:
        #     print self.pathname, res_Ps, ht
        #     res_Ps = (self.ht_calc - ht)/ht

        # print "ps_resid: ", self.pathname, self.ht_calc, res_Ps, hs
        if MN_mode:
            resids.set_values(res_Ps, res_V, res_Vsonic, res_area)
        else:
            resids.set_values(res_Ps, res_V, res_Vsonic, res_MN)

    def linearize(self, inputs, outputs, J):

        mode = self.options['mode']

        if mode == "MN":
            Ts, ht, hs, n_moles, gamma, W, rho, _, _, MN = inputs.values()
            MN_squared_q2 = MN**2/2.
        else:
            Ts, ht, hs, n_moles, gamma, W, rho, _, _, _, area = inputs.values()
            Ps, outs_V, outs_Vsonic, outs_MN = outputs.values()
            MN_squared_q2 = outs_MN**2/2.

        RT_q_MW = R_UNIVERSAL_SI*Ts*n_moles
        ht_calc = hs + MN_squared_q2 * gamma * RT_q_MW

        J['Ps', 'ht'] = -ht_calc/ht**2
        J['Ps', 'hs'] = 1/ht

        # Derivatives of outputs
        part = .5*(gamma*R_UNIVERSAL_SI*n_moles*Ts)**-.5
        J['Vsonic', 'gamma'] = dVs_dgamma = part*R_UNIVERSAL_SI*n_moles*Ts
        J['Vsonic', 'n_moles'] = part*gamma*R_UNIVERSAL_SI*Ts
        J['Vsonic', 'Ts'] = part*gamma*R_UNIVERSAL_SI*n_moles
        J['Vsonic', 'Vsonic'] = -1.
        # J['V', 'V'] = -1.

        if mode=="MN":
            Vsonic, V, area = self._compute_outputs_MN(Ts, n_moles, gamma, W, rho, MN)

            J['area', 'area'] = -1.

            J['Ps', 'MN'] = MN*gamma*RT_q_MW/ht
            J['Ps', 'n_moles'] = R_UNIVERSAL_SI*Ts*MN_squared_q2*gamma/ht
            J['Ps', 'gamma'] = RT_q_MW*MN_squared_q2/ht
            J['Ps', 'Ts'] = MN_squared_q2*gamma*R_UNIVERSAL_SI*n_moles/ht

            if MN >= 1e-16:
                J['area', 'W'] = 1.0/(rho*Vsonic*MN)
                J['area', 'rho'] = -W/(Vsonic*MN*rho**2)

                part = -W/(rho*Vsonic**2*MN) * 0.5*(R_UNIVERSAL_SI*gamma*n_moles*Ts)**-.5*R_UNIVERSAL_SI
                J['area', 'gamma'] = part*n_moles*Ts
                J['area', 'n_moles'] = part*gamma*Ts
                J['area', 'Ts'] = part*gamma*n_moles
                J['area', 'MN'] = -W/rho/Vsonic/MN**2

                J['V', 'MN'] = Vsonic
                J['V', 'Ts'] = MN * J['Vsonic', 'Ts']
                J['V', 'n_moles'] = MN * J['Vsonic', 'n_moles']
                J['V', 'gamma'] = MN * J['Vsonic', 'gamma']

        else:
            MN, Vsonic, V = self._compute_outputs_area(Ts, n_moles, gamma, W, rho, area)

            J['MN', 'MN'] = -1

            dresid_dMN = (MN*gamma*RT_q_MW)

            try:
                dMN_dA = -(W/rho/Vsonic/area**2)
            except FloatingPointError:
                raise om.AnalysisError('{} Bad value in static calc: rho={}, Vsonic={}, area={}'.format(self.pathname, rho, Vsonic, area))

            J['Ps', 'area'] = dMN_dA*dresid_dMN/ht
            J['V', 'area'] = dMN_dA*Vsonic
            J['MN', 'area'] = dMN_dA

            dMN_dVs = -W/(rho*area*Vsonic**2)
            dVs_dTs = 0.5*gamma*R_UNIVERSAL_SI*n_moles/Vsonic
            J['Ps', 'Ts'] = gamma*(MN*dMN_dVs*dVs_dTs*RT_q_MW + MN_squared_q2*R_UNIVERSAL_SI*n_moles)/ht
            J['V', 'Ts'] = -(dMN_dVs*dVs_dTs*Vsonic + MN*dVs_dTs)
            J['MN', 'Ts'] = dMN_dVs*dVs_dTs

            dVs_dnmoles = 0.5*gamma*R_UNIVERSAL_SI*Ts/Vsonic
            J['Ps', 'n_moles'] = -gamma*(MN*RT_q_MW*dMN_dVs*dVs_dnmoles + MN_squared_q2*R_UNIVERSAL_SI*Ts)/ht
            J['V', 'n_moles'] = dMN_dVs*dVs_dnmoles*Vsonic + dVs_dnmoles*MN  # works out to exactly 0
            J['MN', 'n_moles'] = dMN_dVs*dVs_dnmoles

            dMN_dW = (1/rho/Vsonic/area)
            J['Ps', 'W'] = dMN_dW*dresid_dMN/ht
            J['V', 'W'] = dMN_dW*Vsonic
            J['MN', 'W'] = dMN_dW

            dMN_dgamma = dMN_dVs*dVs_dgamma
            J['MN', 'gamma'] = dMN_dgamma
            J['Ps', 'gamma'] = RT_q_MW/ht*(MN_squared_q2 + gamma*MN*dMN_dgamma)
            J['V', 'gamma'] = dMN_dgamma*Vsonic + dVs_dgamma*MN

            dMN_drho = -W/(area*Vsonic*rho**2)
            J['Ps', 'rho'] = MN*gamma*R_UNIVERSAL_SI*n_moles*Ts*dMN_drho/ht
            J['V', 'rho'] = dMN_drho*Vsonic
            J['MN', 'rho'] = dMN_drho
