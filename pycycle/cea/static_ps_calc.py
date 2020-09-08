import numpy as np

from openmdao.api import ExplicitComponent

from pycycle.constants import R_UNIVERSAL_SI

class PsCalc(ExplicitComponent):
    """Mach number, Area calculation for when Ps is known"""

    def initialize(self):
        self.options.declare('thermo', desc='thermodynamic data object', recordable=False)

    def setup(self):

        self.add_input('P', val=.001, units="bar", desc="static pressure")
        self.add_input('gamma', val=1.4)
        self.add_input('n_moles', shape=1)
        self.add_input('Ts', val=518., units="degK", desc="Static temp")
        self.add_input('ht', val=0., units="J/kg", desc="Total enthalpy reference condition")
        self.add_input('hs', val=0., units="J/kg", desc="Static enthalpy")
        self.add_input('W', val=0.0, desc="mass flow rate", units="kg/s")
        self.add_input('rho', val=1.0, desc="density", units="kg/m**3")

        self.add_output('MN', val=1.0, desc="computed mach number")
        self.add_output('V', val=1.0, units="m/s", desc="computed speed", res_ref=1e3)
        self.add_output('Vsonic', val=1.0, units="m/s", desc="computed speed of sound", res_ref=1e3)
        self.add_output('area', val=1.0, units="m**2", desc="computed area")

        self.declare_partials('V', ['ht', 'hs'])
        self.declare_partials('Vsonic', ['gamma', 'n_moles', 'Ts'])
        self.declare_partials('MN', ['gamma', 'n_moles', 'Ts', 'hs', 'ht'])
        self.declare_partials('area', ['rho', 'W', 'hs', 'ht'])

    def compute(self, inputs, outputs):

        P, gamma, n_moles, Ts, ht, hs, W, rho = inputs.values()

        Vsonic = np.sqrt(gamma * R_UNIVERSAL_SI * n_moles * Ts)

        # If ht < hs then V will be imaginary, so use an inverse relationship to allow solution process to continue
        if ht >= hs:
            V = np.sqrt(2.0 * (ht - hs))
        else:
            # print('Warning: in', self.pathname, 'ht < hs, inverting relationship to get a real velocity, ht = ', inputs['ht'], 'hs = ', inputs['hs'])
            V = np.sqrt(2.0 * (hs - ht))

        MN = V / Vsonic
        area = W / (rho * V)

        outputs.set_values(MN, V, Vsonic, area)

    def compute_partials(self, inputs, J):

        P, gamma, n_moles, Ts, ht, hs, W, rho = inputs.values()
        Vsonic = np.sqrt(gamma * R_UNIVERSAL_SI * n_moles * Ts)

        J['Vsonic','gamma'] = Vsonic / (2.0 * gamma)
        J['Vsonic','n_moles'] = Vsonic / (2.0 * n_moles)
        J['Vsonic','Ts'] = Vsonic / (2.0 * Ts)

        if ht >= hs:
            V = np.sqrt(2.0 * (ht - hs))
            J['V','ht'] = 1.0 / V
            J['V','hs'] = -1.0 / V
        else:
            V = np.sqrt(2.0 * (hs - ht))
            J['V','hs'] = 1.0 / V
            J['V','ht'] = -1.0 / V

        J['MN','ht'] = 1.0 / Vsonic * J['V','ht']
        J['MN','hs'] = 1.0 / Vsonic * J['V','hs']
        J['MN','gamma'] = -V / Vsonic**2 * J['Vsonic','gamma']
        J['MN','n_moles'] = -V / Vsonic**2 * J['Vsonic','n_moles']
        J['MN','Ts'] = -V / Vsonic**2 * J['Vsonic','Ts']

        J['area','W'] = 1.0 / (rho * V)
        J['area','rho'] = -W / (rho**2 * V)
        J['area','ht'] = -W / (rho * V**2) * J['V','ht']
        J['area','hs'] = -W / (rho * V**2) * J['V','hs']






