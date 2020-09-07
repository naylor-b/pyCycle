""" Class definition for a Duct."""

import numpy as np

import openmdao.api as om

from pycycle.cea import species_data
from pycycle.cea.set_static import SetStatic
from pycycle.cea.set_total import SetTotal
from pycycle.constants import AIR_MIX
from pycycle.flow_in import FlowIn
from pycycle.passthrough import PassThrough

class MachPressureLossMap(om.ExplicitComponent):
    """
    Calculates pressure loss across the duct as a function of Mach number.
    """
    def initialize(self):
        self.options.declare('design', default=True,
                              desc='Switch between on-design and off-design calculation.')
        self.options.declare('expMN', default=0.0,
                                desc='MN exponent for loss calculations')

    def setup(self):
        design = self.options['design']

        self.add_input('MN_in', val=0.0,
                        desc='Mach number entering duct')
        if design:
            self.add_input('dPqP', val=0.0,
                        desc='Pressure differential as a fraction of incoming pressure')
            self.add_output('s_dPqP', val=0.0,
                        desc='Pressure loss scalar')
            self.declare_partials('s_dPqP', ['dPqP', 'MN_in'])
        else:
            self.add_input('s_dPqP', val=0.0,
                        desc='Pressure loss scalar')
            self.add_output('dPqP', val=0.0,
                        desc='Pressure differential as a fraction of incoming pressure')
            self.declare_partials('dPqP', ['s_dPqP', 'MN_in'])

    def compute(self, inputs, outputs):
        design = self.options['design']
        expMN = self.options['expMN']

        if design:
            MN_in, dPqP = inputs.split_vals
            outputs['s_dPqP'] = dPqP / MN_in**expMN
        else:
            MN_in, s_dPqP = inputs.split_vals
            outputs['dPqP'] = s_dPqP * MN_in**expMN

    def compute_partials(self, inputs, J):
        design = self.options['design']
        expMN = self.options['expMN']

        if design:
            MN_in, dPqP = inputs.split_vals
            J['s_dPqP', 'dPqP'] = 1.0 / MN_in**expMN
            J['s_dPqP', 'MN_in'] = -expMN * dPqP * MN_in**(-expMN-1.0)
        else:
            MN_in, s_dPqP = inputs.split_vals
            J['dPqP', 's_dPqP'] = MN_in**expMN
            J['dPqP', 'MN_in'] = expMN * s_dPqP * MN_in**(expMN-1.0)

class PressureLoss(om.ExplicitComponent):
    """
    Calculates pressure loss across the duct.
    """

    def setup(self):
        # inputs
        self.add_input('dPqP', val = 0.0,
                       desc='pressure differential as a fraction of incoming pressure')
        self.add_input('Pt_in', val=5.0, units='lbf/inch**2', desc='Inlet total pressure')

        # outputs
        self.add_output('Pt_out', val=14.696, units='lbf/inch**2', desc='Exit total pressure', lower=1e-3)

        self.declare_partials('Pt_out', '*')

    def compute(self, inputs, outputs):
        dPqP, Pt_in = inputs.split_vals()
        outputs['Pt_out'] =Pt_in*(1.0 - dPqP)

    def compute_partials(self, inputs, J):
        dPqP, Pt_in = inputs.split_vals()
        J['Pt_out', 'dPqP'] = -Pt_in
        J['Pt_out', 'Pt_in'] = 1.0 - dPqP


class qCalc(om.ExplicitComponent):
    """
    Additional energy added or extracted by the duct.
    """

    def setup(self):
        #inputs
        self.add_input('W_in', val=2.0, units='lbm/s', desc='incoming mass flow')
        self.add_input('Q_dot', val=0.0, units='Btu/s',
                       desc='heat flow rate into (positive) or out of (negative) the air')
        self.add_input('ht_in', val=1.0, units='Btu/lbm', desc='incoming total enthalpy')

        #outputs
        self.add_output('ht_out', val=1.0, units='Btu/lbm', desc='outgoing total enthalpy' )

        self.declare_partials('ht_out', '*')

    def compute(self, inputs, outputs):
        W_in, Q_dot, ht_in = inputs.split_vals()
        outputs['ht_out'] = ht_in + Q_dot/W_in

    def compute_partials(self, inputs, J):
        W_in, Q_dot, ht_in = inputs.split_vals()
        J['ht_out','W_in'] = -Q_dot/(W_in**2)
        J['ht_out','Q_dot'] = 1.0/W_in
        J['ht_out','ht_in'] = 1.0


class Duct(om.Group):
    """
    Calculates flow for an element with specified MN (on design)
    or Area (off-design) and Pressure/Energy loss across the component.

    --------------
    Flow Stations
    --------------
    Fl_I
    Fl_O

    -------------
    Design
    -------------
        inputs
        --------
        dPqP
        Q_dot
        MN

        outputs
        --------
        s_dPqP

    -------------
    Off-Design
    -------------
        inputs
        --------
        s_dPqP | dPqP: if expMN > 0 then use s_dPqP
        Q_dot
        area

        outputs
        --------
        dPqP
    """

    def initialize(self):
        self.options.declare('thermo_data', default=species_data.janaf,
                              desc='thermodynamic data set', recordable=False)
        self.options.declare('elements', default=AIR_MIX,
                              desc='set of elements present in the flow')
        self.options.declare('statics', default=True,
                              desc='If True, calculate static properties.')
        self.options.declare('design', default=True,
                              desc='Switch between on-design and off-design calculation.')
        self.options.declare('expMN', default=0.0,
                              desc='Mach number exponent for dPqP_MN calculations.'
                                   '0 means it has no effect. Only has impact in off-design')

        self.default_des_od_conns = [
            # (design src, off-design target)
            ('Fl_O:stat:area', 'area')
        ]


    def setup(self):
        thermo_data = self.options['thermo_data']
        elements = self.options['elements']
        statics = self.options['statics']
        design = self.options['design']
        expMN = self.options['expMN']

        gas_thermo = species_data.Thermo(thermo_data, init_reacts=elements)
        gas_prods = gas_thermo.products
        num_prod = gas_thermo.num_prod
        num_element = gas_thermo.num_element

        # Create inlet flowstation
        flow_in = FlowIn(fl_name='Fl_I', num_prods=num_prod, num_elements=num_element)
        self.add_subsystem('flow_in', flow_in, promotes=['Fl_I:tot:*', 'Fl_I:stat:*'])

        if expMN > 1e-10: # Calcluate pressure losses as function of Mach number
            if design:
                self.add_subsystem('dPqP_MN', MachPressureLossMap(design=design, expMN=expMN),
                                promotes_inputs=['dPqP', ('MN_in', 'Fl_I:stat:MN')],
                                promotes_outputs=['s_dPqP'])
            else:
                self.add_subsystem('dPqP_MN', MachPressureLossMap(design=design, expMN=expMN),
                                promotes_inputs=['s_dPqP' ,('MN_in', 'Fl_I:stat:MN')],
                                promotes_outputs=['dPqP'])

        #Pressure Loss Component
        prom_in = [('Pt_in', 'Fl_I:tot:P'), 'dPqP']
        self.add_subsystem('p_loss', PressureLoss(), promotes_inputs=prom_in)

        # Energy Calc Component
        prom_in = [('W_in', 'Fl_I:stat:W'), ('ht_in', 'Fl_I:tot:h'), 'Q_dot']
        self.add_subsystem('q_calc', qCalc(), promotes_inputs=prom_in)

        # Total Calc
        real_flow = SetTotal(thermo_data=thermo_data, mode='h',
                             init_reacts=elements, fl_name="Fl_O:tot")
        prom_in = [('b0', 'Fl_I:tot:b0')]
        self.add_subsystem('real_flow', real_flow, promotes_inputs=prom_in,
                           promotes_outputs=['Fl_O:*'])
        self.connect("q_calc.ht_out", "real_flow.h")
        self.connect("p_loss.Pt_out", "real_flow.P")

        if statics:
            if design:
            #   Calculate static properties
                out_stat = SetStatic(mode="MN", thermo_data=thermo_data, init_reacts=elements, fl_name="Fl_O:stat")
                prom_in = [('b0', 'Fl_I:tot:b0'),
                           ('W', 'Fl_I:stat:W'),
                           'MN']
                prom_out = ['Fl_O:stat:*']
                self.add_subsystem('out_stat', out_stat, promotes_inputs=prom_in,
                                   promotes_outputs=prom_out)

                self.connect('Fl_O:tot:S', 'out_stat.S')
                self.connect('Fl_O:tot:h', 'out_stat.ht')
                self.connect('Fl_O:tot:P', 'out_stat.guess:Pt')
                self.connect('Fl_O:tot:gamma', 'out_stat.guess:gamt')

            else:
                # Calculate static properties
                out_stat = SetStatic(mode="area", thermo_data=thermo_data, init_reacts=elements, fl_name="Fl_O:stat")
                prom_in = [('b0', 'Fl_I:tot:b0'),
                           ('W', 'Fl_I:stat:W'),
                           'area']
                prom_out = ['Fl_O:stat:*']
                self.add_subsystem('out_stat', out_stat, promotes_inputs=prom_in,
                                   promotes_outputs=prom_out)

                self.connect('Fl_O:tot:S', 'out_stat.S')
                self.connect('Fl_O:tot:h', 'out_stat.ht')
                self.connect('Fl_O:tot:P', 'out_stat.guess:Pt')
                self.connect('Fl_O:tot:gamma', 'out_stat.guess:gamt')
        else:
            self.add_subsystem('W_passthru', PassThrough('Fl_I:stat:W', 'Fl_O:stat:W', 1.0, units= "lbm/s"),
                               promotes=['*'])

        self.add_subsystem('FAR_passthru', PassThrough('Fl_I:FAR', 'Fl_O:FAR', 0.0), promotes=['*'])

        # if not design:
        #     self.set_input_defaults('area', val=1, units='in**2')

        self.set_input_defaults('Fl_I:tot:b0', gas_thermo.b0)


if __name__ == "__main__":

    p = om.Problem()
    p.model = om.Group()

    # params = (
    #     ('dPqP', 0.02, {'shape': 1, 'desc': 'pressure differential as a fraction of incoming pressure'}),
    #     ('Pt_in', 5.0, {'units': 'lbf/inch**2', 'shape': 1, 'desc': 'Inlet total pressure'}),
    #     ('MN_in', 0.5)
    # )
    # p.model.add_subsystem('des_vars', om.IndepVarComp(params), promotes=['*'])


    p.model.add_subsystem('comp', PressureLoss(), promotes=['*'])
    p.model.add_subsystem('loss', MachPressureLossMap(design=True, expMN=2.0), promotes=['*'])


    p.setup(check=False, force_alloc_complex=True)
    p.run_model()

    p.check_partials(method='cs', compact_print=True)

