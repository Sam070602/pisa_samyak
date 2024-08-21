#! /usr/bin/env python

"""
Try to simply run every existing service by automatically deriving as many
sensible test-configuration parameters as possible. A generic services's
test cannot be triggered from within a given service itself, because
sensibly initialising the instance itself (init params, expected params)
is part of the problem. Also, with this external script we can avoid
requesting the implementation of a test function within each service's
module.
"""

from __future__ import absolute_import

from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser
from importlib import import_module
from os import walk
from os.path import isfile, join, relpath

from numpy import linspace

from pisa.core.container import Container, ContainerSet
from pisa.utils.fileio import expand, nsort_key_func
from pisa.utils.log import Levels, logging, set_verbosity
from pisa_tests.run_unit_tests import PISA_PATH


__all__ = [
    "STAGES_PATH",
    "test_services",
    "find_services",
    "find_services_in_file",
    "get_stage_dot_service_from_module_pypath",
    "add_test_inputs",
    "set_service_modes",
    "run_service_test"
]

__author__ = "T. Ehrhardt, J. Weldert"

__license__ = """Copyright (c) 2014-2024, The IceCube Collaboration

 Licensed under the Apache License, Version 2.0 (the "License");
 you may not use this file except in compliance with the License.
 You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

 Unless required by applicable law or agreed to in writing, software
 distributed under the License is distributed on an "AS IS" BASIS,
 WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 See the License for the specific language governing permissions and
 limitations under the License."""


STAGES_PATH = join(PISA_PATH, "stages")
INIT_TEST_NAME = "init_test"
"""Assumed name of custom function in each module which returns an
example instance of the service in question"""

# TODO: define hopeless cases? define services whose tests may fail?
# optional modules from unit tests?
# Add in <stage>.<service> format
SKIP_SERVICES = (
)


def find_services(path):
    """Modelled after `run_unit_tests.find_unit_tests`"""
    path = expand(path, absolute=True, resolve_symlinks=True)

    services = {}
    if isfile(path):
        filerelpath = relpath(path, start=PISA_PATH)
        services[filerelpath] = find_services_in_file(path)
        return services

    for dirpath, dirs, files in walk(path, followlinks=True):
        files.sort(key=nsort_key_func)
        dirs.sort(key=nsort_key_func)

        for filename in files:
            if not filename.endswith(".py"):
                continue
            filepath = join(dirpath, filename)
            filerelpath = relpath(filepath, start=PISA_PATH)
            services[filerelpath] = find_services_in_file(filepath)

    return services


def find_services_in_file(filepath):
    """Modelled after `run_unit_tests.find_unit_tests_in_file`"""
    filepath = expand(filepath, absolute=True, resolve_symlinks=True)
    assert isfile(filepath), str(filepath)
    services = []
    with open(filepath, "r") as f:
        for line in f.readlines():
            tokens = line.split()
            if tokens and tokens[0] == "class" and "(Stage)" in tokens[1]:
                service_name = tokens[1].split("(")[0].strip()
                services.append(service_name)
    return services


def get_stage_dot_service_from_module_pypath(module_pypath):
    """Assumes `module_pypath` starts with pisa.stages and we
    have one directory per stage, which contains all services
    implementing that stage."""
    return module_pypath[12:]


def add_test_inputs(service):
    """Try to come up with sensible test input data for the `Stage`
    instance `service`"""
    container1 = Container('test1')
    container2 = Container('test2')
    for k in service.expected_container_keys:
        container1[k] = linspace(0.1, 1, 10)
        container2[k] = linspace(0.1, 1, 10)
    service.data = ContainerSet('data', [container1, container2])


def set_service_modes(service, calc_mode, apply_mode):
    """Set `calc_mode` and `apply_mode` for the `Stage` instance `service`"""
    service.calc_mode = calc_mode
    service.apply_mode = apply_mode


def run_service_test(service):
    """Try to set up and run the `Stage` instance `service`"""
    service.setup()
    service.run()


def test_services(
    path=STAGES_PATH,
    init_test_name=INIT_TEST_NAME,
    skip_services=SKIP_SERVICES,
    verbosity=Levels.WARN,
):
    """Modelled after `run_unit_tests.run_unit_tests`"""
    services = find_services(path=path)

    ntries = 0
    nsuccesses = 0
    set_verbosity(verbosity)

    for rel_file_path, service_names in services.items():
        if not service_names:
            continue
        assert len(service_names) == 1, 'Only specify one stage per file.'
        service_name = service_names[0]

        pypath = ["pisa"] + rel_file_path[:-3].split("/")
        parent_pypath = ".".join(pypath[:-1])
        module_name = pypath[-1].replace(".", "_")
        module_pypath = f"{parent_pypath}.{module_name}"
        stage_dot_service = get_stage_dot_service_from_module_pypath(module_pypath)

        ntries += 1
        # check whether we should skip testing this service for some reason
        if stage_dot_service in skip_services:
            logging.warning(f"{stage_dot_service} will be ignored in service test.")
            continue

        logging.debug(f"Starting test for service {stage_dot_service}...")

        # if service module import successful, try to initialise the service
        try:
            module = import_module(module_pypath, package=parent_pypath)
        except:
            logging.warning(f"{module_pypath} cannot be imported.")
            continue

        if not hasattr(module, init_test_name):
            try:
                # Without a dedicated `init_test` function, we just try to
                # instantiate the service with std. Stage kwargs
                service = getattr(module, service_name)()
            except Exception as err:
                logging.warning(
                    f"{stage_dot_service} has no {init_test_name} function " +
                     "and could not be instantiated with standard kwargs only.\n" +
                     "msg: %s" % err
                )
                continue
        else:
            try:
                # Exploit presence of init_test (TODO: switch order with above?)
                param_kwargs = {'prior': None, 'range': None, 'is_fixed': True}
                service = getattr(module, init_test_name)(**param_kwargs)
            except Exception as err:
                logging.error(
                    f"{stage_dot_service} has an {init_test_name} function " +
                    "which failed to instantiate the service with msg:\n %s." % err
                )
                continue

        try:
            add_test_inputs(service)
        except Exception as err:
            logging.warning(
                f"Failed to assign test inputs for {stage_dot_service} with msg:\n %s"
                % err
            )
            continue

        try:
            # Only try event-by-event mode for now
            set_service_modes(service, calc_mode="events", apply_mode="events")
        except Exception as err:
            logging.warning(
                "Failed to set `calc_mode` and `apply_mode` for " +
                f"{stage_dot_service} with msg:\n %s" % err
            )
            continue

        try:
            run_service_test(service)
            logging.info(f"{stage_dot_service} passed the test.")
            nsuccesses += 1
        except Exception as err:
            logging.error(f"{stage_dot_service} failed to setup or run with msg:\n %s." % err)
            continue

    logging.info("%d out of %d tested services passed the test" % (nsuccesses, ntries))


def parse_args(description=__doc__):
    """Parse command line arguments"""
    parser = ArgumentParser(description=description,
                            formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument(
        "-v", action="count", default=Levels.WARN, help="set verbosity level"
    )
    args = parser.parse_args()
    return args


def main():
    """Script interface to test_services"""
    args = parse_args()
    kwargs = vars(args)
    kwargs["verbosity"] = kwargs.pop("v")
    test_services(**kwargs)
    logging.info('Services testing done.')


if __name__ == "__main__":
    main()
