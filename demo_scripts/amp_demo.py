# -*- coding: utf-8 -*-

import os
import importlib

import numpy as np
import scipy.interpolate as interp
import matplotlib.pyplot as plt

from bag import BagProject
from bag.io import read_yaml
from bag.layout.routing import RoutingGrid
from bag.layout.template import TemplateDB
from bag.data import load_sim_results, save_sim_results, load_sim_file


def make_tdb(prj, specs, impl_lib):
    # type: () -> TemplateDB
    """Create and return a new TemplateDB object."""
    grid_specs = specs['routing_grid']
    layers = grid_specs['layers']
    spaces = grid_specs['spaces']
    widths = grid_specs['widths']
    bot_dir = grid_specs['bot_dir']

    routing_grid = RoutingGrid(prj.tech_info, layers, spaces, widths, bot_dir)
    tdb = TemplateDB('template_libs.def', routing_grid, impl_lib, use_cybagoa=True)
    return tdb


def gen_pwl_data(fname):
    td = 100e-12
    tpulse = 800e-12
    tr = 20e-12
    amp = 10e-3

    tvec = [0, td, td + tr, td + tr + tpulse, td + tr + tpulse + tr]
    yvec = [-amp, -amp, amp, amp, -amp]

    dir_name = os.path.dirname(fname)
    os.makedirs(dir_name, exist_ok=True)

    with open(fname, 'w') as f:
        for t, y in zip(tvec, yvec):
            f.write('%.4g %.4g\n' % (t, y))


def gen_layout(prj, specs, dsn_name):
    dsn_specs = specs[dsn_name]
    impl_lib = dsn_specs['impl_lib']
    layout_params = dsn_specs['layout_params']
    lay_package = dsn_specs['layout_package']
    lay_class = dsn_specs['layout_class']
    gen_cell = dsn_specs['gen_cell']

    tdb = make_tdb(prj, specs, impl_lib)
    lay_module = importlib.import_module(lay_package)
    temp_cls = getattr(lay_module, lay_class)
    print('computing layout')
    template = tdb.new_template(params=layout_params, temp_cls=temp_cls)
    print('creating layout')
    tdb.batch_layout(prj, [template], [gen_cell])
    print('layout done')
    return template.sch_params


def gen_schematics(prj, specs, dsn_name, sch_params, check_lvs=False):
    dsn_specs = specs[dsn_name]

    impl_lib = dsn_specs['impl_lib']
    sch_lib = dsn_specs['sch_lib']
    sch_cell = dsn_specs['sch_cell']
    gen_cell = dsn_specs['gen_cell']
    testbenches = dsn_specs['testbenches']

    dsn = prj.create_design_module(sch_lib, sch_cell)
    print('computing %s schematics' % gen_cell)
    dsn.design(**sch_params)
    print('creating %s schematics' % gen_cell)
    dsn.implement_design(impl_lib, top_cell_name=gen_cell, erase=True)

    if check_lvs:
        print('running lvs')
        lvs_passed, lvs_log = prj.run_lvs(impl_lib, gen_cell)
        if not lvs_passed:
            raise ValueError('LVS failed.  check log file: %s' % lvs_log)
        else:
            print('lvs passed')

    for name, info in testbenches.items():
        tb_lib = info['tb_lib']
        tb_cell = info['tb_cell']
        tb_sch_params = info['sch_params']

        tb_gen_cell = '%s_%s' % (gen_cell, name)

        if 'tran_fname' in tb_sch_params:
            tran_fname = os.path.abspath(tb_sch_params['tran_fname'])
            gen_pwl_data(tran_fname)
            tb_sch_params['tran_fname'] = tran_fname

        tb_dsn = prj.create_design_module(tb_lib, tb_cell)
        print('computing %s schematics' % tb_gen_cell)
        tb_dsn.design(dut_lib=impl_lib, dut_cell=gen_cell, **tb_sch_params)
        print('creating %s schematics' % tb_gen_cell)
        tb_dsn.implement_design(impl_lib, top_cell_name=tb_gen_cell, erase=True)

    print('schematic done')


def simulate(prj, specs, dsn_name):
    view_name = specs['view_name']
    sim_envs = specs['sim_envs']
    dsn_specs = specs[dsn_name]

    data_dir = dsn_specs['data_dir']
    impl_lib = dsn_specs['impl_lib']
    gen_cell = dsn_specs['gen_cell']
    testbenches = dsn_specs['testbenches']

    results_dict = {}
    for name, info in testbenches.items():
        tb_params = info['tb_params']
        tb_gen_cell = '%s_%s' % (gen_cell, name)

        print('setting up %s' % tb_gen_cell)
        tb = prj.configure_testbench(impl_lib, tb_gen_cell)

        for key, val in tb_params.items():
            tb.set_parameter(key, val)
        tb.set_simulation_view(impl_lib, gen_cell, view_name)
        tb.set_simulation_environments(sim_envs)
        tb.update_testbench()
        print('running simulation')
        tb.run_simulation()
        print('simulation done, load results')
        results = load_sim_results(tb.save_dir)
        save_sim_results(results, os.path.join(data_dir, '%s.hdf5' % tb_gen_cell))
        results_dict[name] = results

    print('all simulation done')

    return results_dict


def load_sim_data(specs, dsn_name):
    dsn_specs = specs[dsn_name]
    data_dir = dsn_specs['data_dir']
    gen_cell = dsn_specs['gen_cell']
    testbenches = dsn_specs['testbenches']

    results_dict = {}
    for name, info in testbenches.items():
        tb_gen_cell = '%s_%s' % (gen_cell, name)
        fname = os.path.join(data_dir, '%s.hdf5' % tb_gen_cell)
        print('loading simulation data for %s' % tb_gen_cell)
        results_dict[name] = load_sim_file(fname)

    print('finish loading data')

    return results_dict


def plot_data(results_dict):
    dc_results = results_dict['tb_dc']
    vin = dc_results['vin']
    vout = dc_results['vout']

    vin_arg = np.argsort(vin)
    vin = vin[vin_arg]
    vout = vout[vin_arg]
    vout_fun = interp.InterpolatedUnivariateSpline(vin, vout)
    vout_diff_fun = vout_fun.derivative(1)

    f, (ax1, ax2) = plt.subplots(2, sharex='all')
    ax1.set_title('Vout vs Vin')
    ax1.set_ylabel('Vout (V)')
    ax1.plot(vin, vout)
    ax2.set_title('Gain vs Vin')
    ax2.set_ylabel('Gain (V/V)')
    ax2.set_xlabel('Vin (V)')
    ax2.plot(vin, vout_diff_fun(vin))

    ac_tran_results = results_dict['tb_ac_tran']
    tvec = ac_tran_results['time']
    freq = ac_tran_results['freq']
    vout_ac = ac_tran_results['vout_ac']
    vout_tran = ac_tran_results['vout_tran']

    f, (ax1, ax2) = plt.subplots(2, sharex='all')
    ax1.set_title('Magnitude vs Frequency')
    ax1.set_ylabel('Magnitude (dB)')
    ax1.semilogx(freq, 20 * np.log10(np.abs(vout_ac)))
    ax2.set_title('Phase vs Frequency')
    ax2.set_ylabel('Phase (Degrees)')
    ax2.set_xlabel('Frequency (Hz)')
    ax2.semilogx(freq, np.angle(vout_ac, deg=True))

    plt.figure()
    plt.title('Vout vs Time')
    plt.ylabel('Vout (V)')
    plt.xlabel('Time (s)')
    plt.plot(tvec, vout_tran)

    plt.show()


if __name__ == '__main__':
    spec_fname = 'demo_specs/demo.yaml'
    cur_dsn_name = 'amp_sf'
    run_lvs = True

    top_specs = read_yaml(spec_fname)

    # """
    bprj = BagProject()

    dsn_sch_params = gen_layout(bprj, top_specs, cur_dsn_name)
    gen_schematics(bprj, top_specs, cur_dsn_name, dsn_sch_params, check_lvs=run_lvs)
    simulate(bprj, top_specs, cur_dsn_name)
    # """

    res_dict = load_sim_data(top_specs, cur_dsn_name)
    plot_data(res_dict)