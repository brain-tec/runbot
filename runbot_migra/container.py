# -*- coding: utf-8 -*-
"""Containerize builds

The docker image used for the build is always tagged like this:
    odoo:runbot_tests
This file contains helpers to containerize builds with Docker.
When testing this file:
    the first parameter should be a directory containing Odoo.
    The second parameter is the exposed port
"""
import argparse
import datetime
import json
import logging
import os
import shutil
import subprocess
import time


_logger = logging.getLogger(__name__)
DOCKERUSER = """
RUN groupadd -g %(group_id)s odoo \\
&& useradd -u %(user_id)s -g odoo -G audio,video odoo \\
&& mkdir /home/odoo \\
&& chown -R odoo:odoo /home/odoo \\
&& echo "odoo ALL= NOPASSWD: /usr/bin/pip" > /etc/sudoers.d/pip \\
&& echo "odoo ALL= NOPASSWD: /usr/bin/pip3" >> /etc/sudoers.d/pip
USER odoo
ENV COVERAGE_FILE /data/build/.coverage
""" % {'group_id': os.getgid(), 'user_id': os.getuid()}


class Command():
    def __init__(self, pres, cmd, posts, finals=None):
        self.pres = pres or []
        self.cmd = cmd
        self.posts = posts or []
        self.finals = finals or []

    def __getattr__(self, name):
        return getattr(self.cmd, name)

    def __getitem__(self, key):
        return self.cmd[key]

    def __add__(self, l):
        return Command(self.pres, self.cmd + l, self.posts, self.finals)

    def build(self):
        cmd_chain = []
        cmd_chain += [' '.join(pre) for pre in self.pres if pre]
        cmd_chain.append(' '.join(self))
        cmd_chain += [' '.join(post) for post in self.posts if post]
        cmd_chain = [' && '.join(cmd_chain)]
        cmd_chain += [' '.join(final) for final in self.finals if final]
        return ' ; '.join(cmd_chain)


def docker_build(log_path, build_dir):
    """Build the docker image
    :param log_path: path to the logfile that will contain odoo stdout and stderr
    :param build_dir: the build directory that contains the Odoo sources to build.
    """
    # Prepare docker image
    docker_dir = os.path.join(build_dir, 'docker')
    os.makedirs(docker_dir, exist_ok=True)
    shutil.copy(os.path.join(os.path.dirname(__file__), 'data', 'Dockerfile'), docker_dir)
    # synchronise the current user with the odoo user inside the Dockerfile
    with open(os.path.join(docker_dir, 'Dockerfile'), 'a') as df:
        df.write(DOCKERUSER)
    logs = open(log_path, 'w')
    dbuild = subprocess.Popen(['docker', 'build', '--tag', 'odoo:runbot_tests', '.'], stdout=logs, stderr=logs, cwd=docker_dir)
    dbuild.wait()


def docker_run(run_cmd, log_path, build_dir, container_name, exposed_ports=None, cpu_limit=None, preexec_fn=None, ro_volumes=None, env_variables=None):
    """Run tests in a docker container
    :param run_cmd: command string to run in container
    :param log_path: path to the logfile that will contain odoo stdout and stderr
    :param build_dir: the build directory that contains the Odoo sources to build.
                      This directory is shared as a volume with the container
    :param container_name: used to give a name to the container for later reference
    :param exposed_ports: if not None, starting at 8069, ports will be exposed as exposed_ports numbers
    :params ro_volumes: dict of dest:source volumes to mount readonly in builddir
    :params env_variables: list of environment variables
    """
    _logger.debug('Docker run command: %s', run_cmd)
    logs = open(log_path, 'w')
    run_cmd = 'cd /data/build && %s' % run_cmd
    logs.write("Docker command:\n%s\n=================================================\n" % run_cmd.replace('&& ', '&&\n').replace('|| ', '||\n\t'))
    # create start script
    docker_command = [
        'docker', 'run', '--rm',
        '--name', container_name,
        '--volume=/var/run/postgresql:/var/run/postgresql',
        '--volume=%s:/data/build' % build_dir,
        '--shm-size=128m',
        '--init',
    ]
    if ro_volumes:
        for dest, source in ro_volumes.items():
            logs.write("Adding readonly volume '%s' pointing to %s \n" % (dest, source))
            docker_command.append('--volume=%s:/data/build/%s:ro' % (source, dest))

    if env_variables:
        for var in env_variables:
            docker_command.append('-e=%s' % var)

    serverrc_path = os.path.expanduser('~/.openerp_serverrc')
    odoorc_path = os.path.expanduser('~/.odoorc')
    final_rc = odoorc_path if os.path.exists(odoorc_path) else serverrc_path if os.path.exists(serverrc_path) else None
    if final_rc:
        docker_command.extend(['--volume=%s:/home/odoo/.odoorc:ro' % final_rc])
    if exposed_ports:
        for dp, hp in enumerate(exposed_ports, start=8069):
            docker_command.extend(['-p', '127.0.0.1:%s:%s' % (hp, dp)])
    if cpu_limit:
        docker_command.extend(['--ulimit', 'cpu=%s' % int(cpu_limit)])
    docker_command.extend(['odoo:runbot_tests', '/bin/bash', '-c', "%s" % run_cmd])
    docker_run = subprocess.Popen(docker_command, stdout=logs, stderr=logs, preexec_fn=preexec_fn, close_fds=False, cwd=build_dir)
    docker_wait_for(container_name)
    _logger.info('Started Docker container %s', container_name)
    return container_name

def docker_stop(container_name):
    """Stops the container named container_name"""
    _logger.info('Stopping container %s', container_name)
    dstop = subprocess.run(['docker', 'stop', container_name])

def docker_is_running(container_name):
    """Return True if container is still running"""
    dinspect = subprocess.run(['docker', 'container', 'inspect', container_name], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    return True if dinspect.returncode == 0 else False

def docker_get_gateway_ip():
    """Return the host ip of the docker default bridge gateway"""
    docker_net_inspect = subprocess.run(['docker', 'network', 'inspect', 'bridge'], stdout=subprocess.PIPE)
    if docker_net_inspect.returncode != 0:
        return None
    if docker_net_inspect.stdout:
        try:
            return json.loads(docker_net_inspect.stdout)[0]['IPAM']['Config'][0]['Gateway']
        except KeyError:
            return None

def docker_ps():
    """Return a list of running containers names"""
    try:
        docker_ps = subprocess.run(['docker', 'ps', '--format', '{{.Names}}'], stderr=subprocess.STDOUT, stdout=subprocess.PIPE)
    except FileNotFoundError:
        _logger.warning('Docker not found, returning an empty list.')
        return []
    if docker_ps.returncode != 0:
        return []
    return docker_ps.stdout.decode().strip().split('\n')


def docker_wait_for(container_name, timeout=5):
    """ Wait for container to be started """
    start_time = time.time()
    while (time.time() - start_time) < timeout:
        if docker_is_running(container_name):
            return True
        time.sleep(0.5)
    _logger.warning('Container "%s" never seen', container_name)
    return False
