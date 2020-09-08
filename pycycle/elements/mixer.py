import numpy as np
import openmdao.api as om

from pycycle.cea.set_static import SetStatic
from pycycle.cea.set_total import SetTotal
from pycycle.cea.species_data import Thermo, janaf
from pycycle.constants import AIR_FUEL_MIX, AIR_MIX
from pycycle.flow_in import FlowIn


class MixFlow(om.ExplicitComponent):
    """
    Mix two incoming flow streams
    """

    def initialize(self):
        self.options.declare('thermo_data', default=janaf,
                              desc='thermodynamic data set', recordable=False)
        self.options.declare('Fl_I1_elements', default=AIR_MIX,
                              desc='set of elements present in the flow')
        self.options.declare('Fl_I2_elements', default=AIR_FUEL_MIX,
                              desc='set of elements present in the flow')

    def setup(self):

        thermo_data = self.options['thermo_data']


        self.flow1_elements = self.options['Fl_I1_elements']
        flow1_thermo = Thermo(thermo_data, init_reacts=self.flow1_elements)
        n_flow1_prods = len(flow1_thermo.products)
        self.flow1_wt_mole = flow1_thermo.wt_mole
        self.add_input('Fl_I1:tot:h', val=0.0, units='J/kg', desc='total enthalpy for flow 1')
        self.add_input('Fl_I1:tot:n', val=np.zeros(n_flow1_prods), desc='species composition for flow 1')
        self.add_input('Fl_I1:stat:W', val=0.0, units='kg/s', desc='mass flow for flow 1')
        self.add_input('Fl_I1:stat:P', val=0.0, units='Pa', desc='static pressure for flow 1')
        self.add_input('Fl_I1:stat:V', val=0.0, units='m/s', desc='velocity for flow 1')
        self.add_input('Fl_I1:stat:area', val=0.0, units='m**2', desc='area for flow 1')

        self.flow2_elements = self.options['Fl_I2_elements']
        flow2_thermo = Thermo(thermo_data, init_reacts=self.flow2_elements)
        n_flow2_prods = len(flow2_thermo.products)
        self.flow2_wt_mole = flow2_thermo.wt_mole
        self.aij = flow1_thermo.aij

        self.add_input('Fl_I2:tot:h', val=0.0, units='J/kg', desc='total enthalpy for flow 2')
        self.add_input('Fl_I2:tot:n', val=np.zeros(n_flow2_prods), desc='species composition for flow 2')
        self.add_input('Fl_I2:stat:W', val=0.0, units='kg/s', desc='mass flow for flow 2')
        self.add_input('Fl_I2:stat:P', val=0.0, units='Pa', desc='static pressure for flow 2')
        self.add_input('Fl_I2:stat:V', val=0.0, units='m/s', desc='velocity for flow 2')
        self.add_input('Fl_I2:stat:area', val=0.0, units='m**2', desc='area for flow 2')

        self.add_output('ht_mix', val=0.0, units='J/kg', desc='total enthalpy out')
        self.add_output('n_mix', val=np.zeros(n_flow1_prods), desc='species composition for flow out')
        self.add_output('W_mix', val=0.0, units='kg/s', desc='mass flow for flow out')
        self.add_output('impulse_mix', val=0., units='N', desc='impulse of the outgoing flow')
        self.add_output('b0_mix', val=flow1_thermo.b0)


        ################################################################################
        # we assume that you are always mixing 2 into 1
        # so, the elements in 2 must be a subset of the elements in 1!!!
        ################################################################################
        # TODO: raise error if this is not True

        # create mapping for main and bleed flow
        # self.flow_1_idx_map = {prod: i for i, prod in enumerate(flow1_thermo.products)}
        self.flow_1_idx_map = {prod: i for i, prod in enumerate(flow1_thermo.products)}

        self.mix_mat = np.zeros((n_flow2_prods, n_flow1_prods), dtype=int)
        for i, prod in enumerate(flow2_thermo.products):
            j = self.flow_1_idx_map[prod]
            self.mix_mat[i,j] = 1

        # need to transpose so it maps 2 into 1
        self.mix_mat = self.mix_mat.T

        self.declare_partials('ht_mix', ['Fl_I1:tot:h', 'Fl_I2:tot:h', 'Fl_I1:stat:W', 'Fl_I2:stat:W'])
        self.declare_partials('W_mix', ['Fl_I1:stat:W', 'Fl_I2:stat:W'], val=1.) # linear!
        self.declare_partials('n_mix', ['Fl_I1:tot:n', 'Fl_I2:tot:n'])
        self.declare_partials('n_mix', ['Fl_I1:stat:W', 'Fl_I2:stat:W'])
        self.declare_partials('impulse_mix', ['Fl_I1:stat:P', 'Fl_I1:stat:area', 'Fl_I1:stat:W', 'Fl_I1:stat:V',
                                              'Fl_I2:stat:P', 'Fl_I2:stat:area', 'Fl_I2:stat:W', 'Fl_I2:stat:V'])

        self.declare_partials('b0_mix', ['Fl_I1:tot:n', 'Fl_I2:tot:n'])
        self.declare_partials('b0_mix', ['Fl_I1:stat:W', 'Fl_I2:stat:W'])

        self.set_check_partial_options('*', method='cs')


    def compute(self, inputs, outputs):
        ht1, nt1, W1, P1, V1, area1, ht2, nt2, W2, P2, V2, area2 = inputs.values()

        Wmix = outputs['W_mix'] = W1 + W2
        outputs['ht_mix'] = (W1*ht1 + W2*ht2)/Wmix

        outputs['impulse_mix'] = (P1*area1 + W1*V1) + (P2*area2 + W2*V2)

        ######################################################
        # Begin the mass averaged composition calculations:
        ######################################################
        # convert the incoming flow composition vectors into mass units
        Fl_I1_n_mass = nt1 * self.flow1_wt_mole
        Fl_I2_n_mass = nt2 * self.flow2_wt_mole

        # normalize the mass arrays to 1 kg each
        Fl_I1_n_mass /= np.sum(Fl_I1_n_mass)
        Fl_I2_n_mass /= np.sum(Fl_I2_n_mass)

        # scale by the incoming mass flow rates
        Fl_I1_n_mass *= W1
        Fl_I2_n_mass *= W2

        # sum the flow components together and normalize it down to 1 Kg
        Fl_O_n_mass = (self.mix_mat.dot(Fl_I2_n_mass) + Fl_I1_n_mass)/Wmix

        # convert back to molar units
        outputs['n_mix'] = Fl_O_n_mass/self.flow1_wt_mole
        outputs['b0_mix'] = np.sum(self.aij*outputs['n_mix'], axis=1)

    def compute_partials(self, inputs, J):

        ht1, nt1, W1, P1, V1, area1, ht2, nt2, W2, P2, V2, area2 = inputs.values()

        Wmix = W1+W2
        J['ht_mix', 'Fl_I1:stat:W'] = W2*(ht1-ht2)/Wmix**2
        J['ht_mix', 'Fl_I2:stat:W'] = W1*(ht2-ht1)/Wmix**2
        J['ht_mix', 'Fl_I1:tot:h'] = W1/Wmix
        J['ht_mix', 'Fl_I2:tot:h'] = W2/Wmix

        J['impulse_mix', 'Fl_I1:stat:P'] = area1
        J['impulse_mix', 'Fl_I1:stat:area'] = P1
        J['impulse_mix', 'Fl_I1:stat:W'] = V1
        J['impulse_mix', 'Fl_I1:stat:V'] = W1

        J['impulse_mix', 'Fl_I2:stat:P'] = area2
        J['impulse_mix', 'Fl_I2:stat:area'] = P2
        J['impulse_mix', 'Fl_I2:stat:W'] = V2
        J['impulse_mix', 'Fl_I2:stat:V'] = W2


        # composition derivatives
        n1_mass = nt1 * self.flow1_wt_mole
        n2_mass = nt2 * self.flow2_wt_mole

        n1_mass_hat = n1_mass/np.sum(n1_mass)
        n2_mass_hat = self.mix_mat.dot(n2_mass/np.sum(n2_mass))

        dnout_mole_q_dnout_mass = np.diag(1/self.flow1_wt_mole)

        dnout_mass_q_dn1hat_mass = W1/Wmix
        dnout_mass_q_dn2hat_mass = self.mix_mat*W2/Wmix

        n_n1 = len(n1_mass)
        sum_n1_mass = np.sum(n1_mass)
        dn1hat_mass_q_dn1_mass = -np.tile(n1_mass, (n_n1,1)).T/sum_n1_mass**2
        dn1hat_mass_q_dn1_mass += np.eye(n_n1)/sum_n1_mass # diagonal term


        n_n2 = len(n2_mass)
        sum_n2_mass = np.sum(n2_mass)
        dn2hat_mass_q_dn2_mass = -np.tile(n2_mass, (n_n2,1)).T/sum_n2_mass**2
        dn2hat_mass_q_dn2_mass += np.eye(n_n2)/sum_n2_mass # diagonal term

        dn1_mass_q_dn1_mole = np.diag(self.flow1_wt_mole)
        dn2_mass_q_dn2_mole = np.diag(self.flow2_wt_mole)

        J['n_mix', 'Fl_I1:tot:n'] = dnout_mole_q_dnout_mass.dot(dnout_mass_q_dn1hat_mass*(dn1hat_mass_q_dn1_mass.dot(dn1_mass_q_dn1_mole)))
        J['n_mix', 'Fl_I2:tot:n'] = dnout_mole_q_dnout_mass.dot(dnout_mass_q_dn2hat_mass.dot(dn2hat_mass_q_dn2_mass.dot(dn2_mass_q_dn2_mole)))

        dnout_mass_q_dW1 = W2*(n1_mass_hat-n2_mass_hat)/Wmix**2
        dnout_mass_q_dW2 = W1*(n2_mass_hat-n1_mass_hat)/Wmix**2
        J['n_mix', 'Fl_I1:stat:W'] = dnout_mole_q_dnout_mass.dot(dnout_mass_q_dW1)
        J['n_mix', 'Fl_I2:stat:W'] = dnout_mole_q_dnout_mass.dot(dnout_mass_q_dW2)

        J['b0_mix', 'Fl_I1:tot:n'] = np.matmul(self.aij,J['n_mix','Fl_I1:tot:n'])
        J['b0_mix', 'Fl_I2:tot:n'] = np.matmul(self.aij,J['n_mix','Fl_I2:tot:n'])
        J['b0_mix', 'Fl_I1:stat:W'] = np.matmul(self.aij,J['n_mix', 'Fl_I1:stat:W'])
        J['b0_mix', 'Fl_I2:stat:W'] = np.matmul(self.aij,J['n_mix', 'Fl_I2:stat:W'])


class AreaSum(om.ExplicitComponent):

    def setup(self):

        self.add_input('Fl_I1:stat:area', val=1., units='m**2')
        self.add_input('Fl_I2:stat:area', val=1., units='m**2')
        self.add_output('area_sum', val=1., units='m**2')

        self.declare_partials('area_sum', ['Fl_I1:stat:area', 'Fl_I2:stat:area'], val=1.)
        self.set_check_partial_options('*', method='cs')

    def compute(self, inputs, outputs):
        area1, area2 = inputs.values()
        outputs['area_sum'] = area1 + area2


class Impulse(om.ExplicitComponent):

    def setup(self):
        self.add_input('P', units='Pa')
        self.add_input('area', units='m**2')
        self.add_input('V', units='m/s')
        self.add_input('W', units='kg/s')

        self.add_output('impulse', units='N')

        self.declare_partials('impulse', '*')

    def compute(self, inputs, outputs):
        P, area, V, W = inputs.values()
        outputs['impulse'] = P*area + W*V
        self.set_check_partial_options('*', method='cs')

    def compute_partials(self, inputs, J):
        P, area, V, W = inputs.values()

        J['impulse', 'P'] = area
        J['impulse', 'area'] = P
        J['impulse', 'V'] = W
        J['impulse', 'W'] = V


class Mixer(om.Group):
    """
    Combines two incomming flows into a single outgoing flow
    using a conservation of momentum approach

    --------------
    Flow Stations
    --------------
    Fl_I1
    FL_I2
    Fl_O

    -------------
    Design
    -------------
        inputs
        --------

        implicit states
        ---------------
        balance.P_tot

        outputs
        --------
        ER
    -------------
    Off-Design
    -------------
        inputs
        --------
        Fl_I1_stat_calc.area | Fl_I2_stat_calc.area
        area

        implicit states
        ---------------
        balance.P_tot

    """

    def initialize(self):

        self.options.declare('thermo_data', default=janaf,
                              desc='thermodynamic data set', recordable=False)
        self.options.declare('Fl_I1_elements', default=AIR_MIX,
                              desc='set of elements present in the flow')
        self.options.declare('Fl_I2_elements', default=AIR_FUEL_MIX,
                              desc='set of elements present in the flow')
        self.options.declare('design', default=True,
                              desc='Switch between on-design and off-design calculation.')
        self.options.declare('designed_stream', default=1, values=(1,2),
                              desc='control for which stream has its area varied to match static pressure (1 means, you vary Fl_I1)')
        self.options.declare('internal_solver', default=True,
                              desc='If True, a newton solver is used inside the mixer to converge the impulse balance')


    def setup(self):

        design = self.options['design']
        thermo_data = self.options['thermo_data']

        flow1_elements = self.options['Fl_I1_elements']
        flow1_thermo = Thermo(thermo_data, init_reacts=flow1_elements)
        n_flow1_prods = flow1_thermo.num_prod
        in_flow = FlowIn(fl_name='Fl_I1', num_prods=n_flow1_prods, num_elements=flow1_thermo.num_element)
        self.add_subsystem('in_flow1', in_flow, promotes=['Fl_I1:*'])

        flow2_elements = self.options['Fl_I2_elements']
        flow2_thermo = Thermo(thermo_data, init_reacts=flow2_elements)
        n_flow2_prods = flow2_thermo.num_prod
        in_flow = FlowIn(fl_name='Fl_I2', num_prods=n_flow2_prods, num_elements=flow2_thermo.num_element)
        self.add_subsystem('in_flow2', in_flow, promotes=['Fl_I2:*'])


        if design:
            # internal flow station to compute the area that is needed to match the static pressures
            if self.options['designed_stream'] == 1:
                Fl1_stat = SetStatic(mode="Ps", thermo_data=thermo_data,
                                    init_reacts=flow1_elements,
                                    fl_name="Fl_I1_calc:stat")
                self.add_subsystem('Fl_I1_stat_calc', Fl1_stat,
                                   promotes_inputs=[('b0', 'Fl_I1:tot:b0'), ('S', 'Fl_I1:tot:S'),
                                                    ('ht', 'Fl_I1:tot:h'), ('W', 'Fl_I1:stat:W'), ('Ps', 'Fl_I2:stat:P')],
                                   promotes_outputs=['Fl_I1_calc:stat*'])

                self.add_subsystem('area_calc', AreaSum(), promotes_inputs=['Fl_I2:stat:area'],
                                   promotes_outputs=[('area_sum', 'area')])
                self.connect('Fl_I1_calc:stat:area', 'area_calc.Fl_I1:stat:area')

                self.set_input_defaults('Fl_I1:tot:b0', flow1_thermo.b0)
            else:
                Fl2_stat = SetStatic(mode="Ps", thermo_data=thermo_data,
                                    init_reacts=flow2_elements,
                                    fl_name="Fl_I2_calc:stat")
                self.add_subsystem('Fl_I2_stat_calc', Fl2_stat,
                                   promotes_inputs=[('b0', 'Fl_I2:tot:b0'), ('S', 'Fl_I2:tot:S'),
                                                    ('ht', 'Fl_I2:tot:h'), ('W', 'Fl_I2:stat:W'), ('Ps', 'Fl_I1:stat:P')],
                                   promotes_outputs=['Fl_I2_calc:stat:*'])

                self.add_subsystem('area_calc', AreaSum(), promotes_inputs=['Fl_I1:stat:area'],
                                   promotes_outputs=[('area_sum', 'area')])
                self.connect('Fl_I2_calc:stat:area', 'area_calc.Fl_I2:stat:area')

                self.set_input_defaults('Fl_I2:tot:b0', flow2_thermo.b0)
        else:
            if self.options['designed_stream'] == 1:
                Fl1_stat = SetStatic(mode="area", thermo_data=thermo_data,
                                        init_reacts=flow1_elements,
                                        fl_name="Fl_I1_calc:stat")
                self.add_subsystem('Fl_I1_stat_calc', Fl1_stat,
                                    promotes_inputs=[('b0', 'Fl_I1:tot:b0'), ('S', 'Fl_I1:tot:S'),
                                                     ('ht', 'Fl_I1:tot:h'), ('W', 'Fl_I1:stat:W'),
                                                     ('guess:Pt', 'Fl_I1:tot:P'), ('guess:gamt', 'Fl_I1:tot:gamma')],
                                    promotes_outputs=['Fl_I1_calc:stat*'])

                self.set_input_defaults('Fl_I1:tot:b0', flow1_thermo.b0)
            else:
                Fl2_stat = SetStatic(mode="area", thermo_data=thermo_data,
                                        init_reacts=flow2_elements,
                                        fl_name="Fl_I2_calc:stat")
                self.add_subsystem('Fl_I2_stat_calc', Fl2_stat,
                                    promotes_inputs=[('b0', 'Fl_I2:tot:b0'), ('S', 'Fl_I2:tot:S'),
                                                     ('ht', 'Fl_I2:tot:h'), ('W', 'Fl_I2:stat:W'),
                                                     ('guess:Pt', 'Fl_I2:tot:P'), ('guess:gamt', 'Fl_I2:tot:gamma')],
                                    promotes_outputs=['Fl_I2_calc:stat*'])

                self.set_input_defaults('Fl_I2:tot:b0', flow2_thermo.b0)

        self.add_subsystem('extraction_ratio', om.ExecComp('ER=Pt1/Pt2', Pt1={'units':'Pa'}, Pt2={'units':'Pa'}),
                            promotes_inputs=[('Pt1', 'Fl_I1:tot:P'), ('Pt2', 'Fl_I2:tot:P')],
                            promotes_outputs=['ER'])

        mix_flow = MixFlow(thermo_data=thermo_data,
                   Fl_I1_elements=self.options['Fl_I1_elements'],
                   Fl_I2_elements=self.options['Fl_I2_elements'])
        if self.options['designed_stream'] == 1:
            self.add_subsystem('mix_flow', mix_flow,
                               promotes_inputs=['Fl_I1:tot:h', 'Fl_I1:tot:n', ('Fl_I1:stat:W','Fl_I1_calc:stat:W'), ('Fl_I1:stat:P','Fl_I1_calc:stat:P'),
                                                ('Fl_I1:stat:V','Fl_I1_calc:stat:V'), ('Fl_I1:stat:area','Fl_I1_calc:stat:area'),
                                                'Fl_I2:tot:h', 'Fl_I2:tot:n', 'Fl_I2:stat:W', 'Fl_I2:stat:P', 'Fl_I2:stat:V', 'Fl_I2:stat:area'])
        else:
            self.add_subsystem('mix_flow', mix_flow,
                               promotes_inputs=['Fl_I1:tot:h', 'Fl_I1:tot:n', 'Fl_I1:stat:W', 'Fl_I1:stat:P', 'Fl_I1:stat:V', 'Fl_I1:stat:area',
                                                'Fl_I2:tot:h', 'Fl_I2:tot:n', ('Fl_I2:stat:W','Fl_I2_calc:stat:W'), ('Fl_I2:stat:P','Fl_I2_calc:stat:P'),
                                                ('Fl_I2:stat:V','Fl_I2_calc:stat:V'), ('Fl_I2:stat:area','Fl_I2_calc:stat:area')])


        # group to converge for the impulse balance
        conv = self.add_subsystem('impulse_converge', om.Group(), promotes=['*'])

        if self.options['internal_solver']:
            newton = conv.nonlinear_solver = om.NewtonSolver()
            newton.options['maxiter'] = 30
            newton.options['atol'] = 1e-2
            newton.options['solve_subsystems'] = True
            newton.options['max_sub_solves'] = 20
            newton.options['reraise_child_analysiserror'] = False
            newton.linesearch = om.BoundsEnforceLS()
            newton.linesearch.options['bound_enforcement'] = 'scalar'
            newton.linesearch.options['iprint'] = -1
            conv.linear_solver = om.DirectSolver(assemble_jac=True)

        out_tot = SetTotal(thermo_data=thermo_data, mode='h', init_reacts=self.options['Fl_I1_elements'],
                        fl_name="Fl_O:tot")
        conv.add_subsystem('out_tot', out_tot, promotes_outputs=['Fl_O:tot:*'])
        self.connect('mix_flow.b0_mix', 'out_tot.b0')
        self.connect('mix_flow.ht_mix', 'out_tot.h')
        # note: gets Pt from the balance comp

        out_stat = SetStatic(mode="area", thermo_data=thermo_data,
                             init_reacts=self.options['Fl_I1_elements'],
                             fl_name="Fl_O:stat")
        conv.add_subsystem('out_stat', out_stat, promotes_outputs=['Fl_O:stat:*'], promotes_inputs=['area', ])
        self.connect('mix_flow.b0_mix', 'out_stat.b0')
        self.connect('mix_flow.W_mix','out_stat.W')
        conv.connect('Fl_O:tot:S', 'out_stat.S')
        self.connect('mix_flow.ht_mix', 'out_stat.ht')
        conv.connect('Fl_O:tot:P', 'out_stat.guess:Pt')
        conv.connect('Fl_O:tot:gamma', 'out_stat.guess:gamt')

        conv.add_subsystem('imp_out', Impulse())
        conv.connect('Fl_O:stat:P', 'imp_out.P')
        conv.connect('Fl_O:stat:area', 'imp_out.area')
        conv.connect('Fl_O:stat:V', 'imp_out.V')
        conv.connect('Fl_O:stat:W', 'imp_out.W')

        balance = conv.add_subsystem('balance', om.BalanceComp())
        balance.add_balance('P_tot', val=100, units='psi', eq_units='N', lower=1e-3, upper=10000)
        conv.connect('balance.P_tot', 'out_tot.P')
        conv.connect('imp_out.impulse', 'balance.lhs:P_tot')
        self.connect('mix_flow.impulse_mix', 'balance.rhs:P_tot') #note that this connection comes from outside the convergence group

if __name__ == "__main__":

    from pycycle.cea.species_data import Thermo, janaf
    from pycycle import constants

    prob = om.Problem()
    prob.model = Mixer(design=True, Fl_I1_elements=constants.AIR_MIX, Fl_I2_elements=constants.AIR_MIX)

    thermo = Thermo(janaf, constants.AIR_MIX)

    prob.model.set_input_defaults('Fl_I1:tot:b0', thermo.b0)
    prob.model.set_input_defaults('Fl_I1:tot:h', val=1.0,  units='Btu/lbm')
    prob.model.set_input_defaults('Fl_I2:tot:h', val=1.0,  units='Btu/lbm')
    prob.model.set_input_defaults('Fl_I1:tot:S', val=1.0, units='Btu/(lbm*degR)')
    # p.model.set_input_defaults('Fl_I:tot:T', 284, units='degK')
    prob.model.set_input_defaults('Fl_I1:tot:P', 5.0, units='lbf/inch**2')
    prob.model.set_input_defaults('Fl_I2:tot:P', 5.0, units='lbf/inch**2')
    prob.model.set_input_defaults('Fl_I2:stat:P', 5.0, units='lbf/inch**2')
    # # p.model.set_input_defaults('Fl_I:tot:n', thermo.init_prod_amounts)
    # p.model.set_input_defaults('Fl_I:tot:b0', thermo.b0)
    prob.model.set_input_defaults('Fl_I2:stat:V', 0.0, units='ft/s')#keep
    prob.model.set_input_defaults('Fl_I2:stat:area', 1.0, units='inch**2')#keep
    prob.model.set_input_defaults('Fl_I1:stat:W', 1, units='kg/s')
    prob.model.set_input_defaults('Fl_I2:stat:W', 1, units='kg/s')

    prob.setup(force_alloc_complex=True)
    prob.run_model()
    prob.check_partials(method='cs', compact_print=True)