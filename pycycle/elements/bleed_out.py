""" Class definition for a BleedOut."""

import numpy as np
from collections.abc import Iterable

import openmdao.api as om

from pycycle.cea import species_data
from pycycle.cea.set_static import SetStatic
from pycycle.cea.set_total import SetTotal
from pycycle.constants import AIR_MIX
from pycycle.flow_in import FlowIn
from pycycle.passthrough import PassThrough

class BleedCalcs(om.ExplicitComponent):

    def initialize(self):
        self.options.declare('bleed_names', types=Iterable, desc='list of names for the bleed ports')

    def setup(self):
        self.add_input('W_in', val=30.0, units='lbm/s', desc='entrance mass flow')
        self.add_output('W_out', shape=1, units='lbm/s', desc='exit mass flow', res_ref=1e2)

        # bleed inputs and outputs
        for BN in self.options['bleed_names']:
            self.add_input(BN+':frac_W', val=0.0, desc='bleed mass flow fraction (W_bld/W_in)')
            self.add_output(BN+':stat:W', shape=1, units='lbm/s', desc='bleed mass flow', res_ref=1e2)

            self.declare_partials(BN+':stat:W', ['W_in', BN+':frac_W'])

        self.declare_partials('W_out', ['W_in', '*:frac_W'])

    def compute(self, inputs, outputs):
        # calculate flow and power without bleed flows
        outputs['W_out'] = inputs['W_in']

        # calculate bleed specific outputs and modify exit flow and power
        for BN in self.options['bleed_names']:
            outputs[BN+':stat:W'] = inputs['W_in'] * inputs[BN+':frac_W']
            outputs['W_out'] -= outputs[BN+':stat:W']

    def compute_partials(self, inputs, J):

        # Jacobian elements without bleed flows
        J['W_out','W_in'] = 1.0

        for BN in self.options['bleed_names']:
            J['W_out','W_in'] -= inputs[BN+':frac_W']
            J['W_out',BN+':frac_W'] = -inputs['W_in']

            J[BN+':stat:W','W_in'] = inputs[BN+':frac_W']
            J[BN+':stat:W',BN+':frac_W'] = inputs['W_in']


class BleedOut(om.Group):
    """
    bleed extration from the incomming flow

    --------------
    Flow Stations
    --------------
    Fl_I -> primary input flow
    Fl_O -> primary output flow
    Fl_{bleed_name} -> bleed output flows
        one for each name in `bleed_names` option

    -------------
    Design
    -------------
        inputs
        --------
        {bleed_name}:frac_W
            fraction of incoming flow to bleed off to FL_{bleed_name}
        MN

    -------------
    Off-Design
    -------------
        inputs
        --------
        area
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
        self.options.declare('bleed_names', types=(list,tuple), desc='list of names for the bleed ports',
                              default=[])

        self.default_des_od_conns = [
            # (design src, off-design target)
            ('Fl_O:stat:area', 'area')
        ]

    def setup(self):
        thermo_data = self.options['thermo_data']
        elements = self.options['elements']
        statics = self.options['statics']
        design = self.options['design']
        bleeds = self.options['bleed_names']

        gas_thermo = species_data.Thermo(thermo_data, init_reacts=elements)
        gas_prods = gas_thermo.products
        num_prod = gas_thermo.num_prod
        num_element = gas_thermo.num_element

        # Create inlet flowstation
        flow_in = FlowIn(fl_name='Fl_I', num_prods=num_prod, num_elements=num_element)
        self.add_subsystem('flow_in', flow_in, promotes=['Fl_I:tot:*', 'Fl_I:stat:*'])

        # Bleed flow calculations
        blds = BleedCalcs(bleed_names=bleeds)
        bld_port_globs = ['{}:*'.format(bn) for bn in bleeds]
        self.add_subsystem('bld_calcs', blds,
                           promotes_inputs=[('W_in', 'Fl_I:stat:W'), '*:frac_W'],
                           promotes_outputs=['W_out']+bld_port_globs)

        bleed_names = []
        for BN in bleeds:

            bleed_names.append(BN+'_flow')
            bleed_flow = SetTotal(thermo_data=thermo_data, mode='T',
                                  init_reacts=elements, fl_name=BN+":tot")
            self.add_subsystem(BN+'_flow', bleed_flow,
                               promotes_inputs=[('b0', 'Fl_I:tot:b0'),('T','Fl_I:tot:T'),('P','Fl_I:tot:P')],
                               promotes_outputs=['{}:tot:*'.format(BN)])

        # Total Calc
        real_flow = SetTotal(thermo_data=thermo_data, mode='T',
                             init_reacts=elements, fl_name="Fl_O:tot")
        prom_in = [('b0', 'Fl_I:tot:b0'),('T','Fl_I:tot:T'),('P','Fl_I:tot:P')]
        self.add_subsystem('real_flow', real_flow, promotes_inputs=prom_in,
                           promotes_outputs=['Fl_O:*'])

        if statics:
            if design:
            #   Calculate static properties
                out_stat = SetStatic(mode="MN", thermo_data=thermo_data, init_reacts=elements, fl_name="Fl_O:stat")
                prom_in = [('b0', 'Fl_I:tot:b0'),
                           'MN']
                prom_out = ['Fl_O:stat:*']
                self.add_subsystem('out_stat', out_stat, promotes_inputs=prom_in,
                                   promotes_outputs=prom_out)

                self.connect('Fl_O:tot:S', 'out_stat.S')
                self.connect('Fl_O:tot:h', 'out_stat.ht')
                self.connect('Fl_O:tot:P', 'out_stat.guess:Pt')
                self.connect('Fl_O:tot:gamma', 'out_stat.guess:gamt')
                self.connect('W_out', 'out_stat.W')

            else:
                # Calculate static properties
                out_stat = SetStatic(mode="area", thermo_data=thermo_data, init_reacts=elements, fl_name="Fl_O:stat")
                prom_in = [('b0', 'Fl_I:tot:b0'),
                           'area']
                prom_out = ['Fl_O:stat:*']
                self.add_subsystem('out_stat', out_stat, promotes_inputs=prom_in,
                                   promotes_outputs=prom_out)

                self.connect('Fl_O:tot:S', 'out_stat.S')
                self.connect('Fl_O:tot:h', 'out_stat.ht')
                self.connect('Fl_O:tot:P', 'out_stat.guess:Pt')
                self.connect('Fl_O:tot:gamma', 'out_stat.guess:gamt')
                self.connect('W_out', 'out_stat.W')
        else:
            self.add_subsystem('W_passthru', PassThrough('W_out', 'Fl_O:stat:W', 1.0, units= "lbm/s"),
                               promotes=['*'])

        self.add_subsystem('FAR_passthru', PassThrough('Fl_I:FAR', 'Fl_O:FAR', 0.0), promotes=['*'])

        self.set_input_defaults('Fl_I:tot:b0', gas_thermo.b0)


if __name__ == "__main__":

    p = om.Problem()

    des_vars = p.model.add_subsystem('des_vars', om.IndepVarComp(), promotes=['*'])
    des_vars.add_output('Fl_I:stat:W', 60.0, units='lbm/s')
    des_vars.add_output('test1:frac_W', 0.05, units=None)
    des_vars.add_output('test2:frac_W', 0.05, units=None)
    des_vars.add_output('Fl_I:tot:T', 518.67, units='degR')
    des_vars.add_output('Fl_I:tot:P', 14.696, units='psi')
    des_vars.add_output('MN', 0.25)

    p.model.add_subsystem('bleed', BleedOut(design=True, statics=True, bleed_names=['test1','test2']), promotes=['*'])

    p.setup(check=False)
    p.run_model()

    print('W',p['Fl_I:stat:W'],p['Fl_O:stat:W'],p['test1:stat:W'],p['test2:stat:W'])
    print('T',p['Fl_I:tot:T'],p['Fl_O:tot:T'],p['test1:tot:T'],p['test2:tot:T'])
    print('P',p['Fl_I:tot:P'],p['Fl_O:tot:P'],p['test1:tot:P'],p['test2:tot:P'])
    # p.check_partials()

