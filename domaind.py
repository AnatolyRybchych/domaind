#!/usr/bin/env python3

import requests
import json
import re
import subprocess
import argparse
import copy
import sys
import time
import os
from string import Template
from dataclasses import dataclass


@dataclass
class UpdateCommand:
    source: str
    command: list[str]


@dataclass
class RestApiRequest:
    path: str
    method: str
    body: dict | list | None
    headers: dict


@dataclass
class UpdateRestApi:
    source: list[str|int] | None
    request: RestApiRequest


@dataclass
class UpdateMethod:
    method: str
    command: UpdateCommand | None = None
    rest_api: UpdateRestApi | None = None


@dataclass
class Source:
    method: str


@dataclass
class Config:
    polling_interval: int
    source: Source
    updates: list[UpdateMethod]


DEFAULT_SOURCE: Source = Source('ifconfig.me')


def parse_source(source_config: dict) -> Source:
    source_method: str = source_config.get('method')
    assert type(source_method) is str, f'"source.method" field is mandatory: {source_method}'
    return Source(source_method)

def parse_update(update_config: dict) -> UpdateMethod:
    method = update_config.get('method')
    assert type(method) is str, '"update[].method" field should be a string'

    if method == 'command':
        command = update_config.get('command')
        assert type(command) is dict, '"update[].command" section is mandatory for the "update[]" with a "method" == "command"'

        command_cmd = command.get('command')
        assert type(command) is dict, '"update[].command.command" field is mandatory for command'

        source = command.get('source', 'environment')

        assert source in ['stdin', 'argument', 'environment'], f'Unexpected command source "{source}"'

        return UpdateMethod(
            method = method,
            command = UpdateCommand(source, command_cmd))
    elif method == 'rest-api':
        rest_api = update_config.get('rest-api')
        assert type(rest_api) is dict, '"update[].rest-api" section is mandatory for the "update[]" with a "method" == "rest-api"'

        source = rest_api.get('source')

        request = rest_api.get('request')
        assert type(request) is dict, f'"update[].rest-api.request" section is mandatory: {request}'

        path = request.get('path')
        assert type(path) is str, f'"update[].rest-api.request.path" field is mandatory: {path}'

        body = request.get('body')
        headers = request.get('headers', {})
        request_method = request.get('method', 'PUT')

        return UpdateMethod(
            method = method,
            rest_api= UpdateRestApi(
                source = source,
                request = RestApiRequest(
                    path = path,
                    method = request_method,
                    body = body,
                    headers = headers)))
    else:
        raise Exception(f'Unexpected update method "{method}"')

def parse_config(config) -> Config:
    updates: list[UpdateMethod] = []

    assert type(config) is dict, 'The config is invalid'

    source_config = config.get('source')
    assert type(source_config) is dict, f'"source" section is mandatory: {source_config}'
    source: Source = parse_source(source_config)

    update_config = config.get('update', [])
    assert type(update_config) is list, '"update" section should be a list'
    for update in update_config:
        assert type(update) is dict, f'"update.[]" should be a dict: {update}'
        updates.append(parse_update(update))

    polling_interval: int = config.get('polling_interval', 10)

    return Config(
        polling_interval = polling_interval,
        source = source,
        updates = updates)


def verify_ip(ipv4: str) -> str | None:
    if re.match(r'^((25[0-5]|(2[0-4]|1\d|[1-9]|)\d)\.?\b){4}$', ipv4):
        return ipv4
    else:
        return None


def get_ip(source: Source) -> str | None:
    assert source.method == 'ifconfig.me', f'The method {source.method} is not supported'

    try:
        response = requests.get("https://ifconfig.me", timeout=5)
        response.raise_for_status()
        return response.text.strip()
    except requests.RequestException as e:
        return None


def update_domain_with_command(command: UpdateCommand, ip_address: str):
    if command.source == 'stdin':
        subprocess.run(expand_env(command.command), input = bytes(ip_address, 'utf8'), check=True)
    elif command.source == 'argument':
        subprocess.run([*expand_env(command.command), ip_address], check=True)
    elif command.source == 'environment':
        subprocess.run(expand_env(command.command), check=True)
    else:
        raise Exception(f'Unexpected "update[].command.source": "{command.source}"')


def expand_env(target, env: dict[str, str] = os.environ):
    if type(target) is list:
        return [expand_env(t) for t in target]
    elif type(target) is dict:
        return {expand_env(tk): expand_env(tv) for tk, tv in target.items()}
    elif type(target) is str:
        return Template(target).safe_substitute(env)
    else:
        return target


def update_domain_with_rest_api(api: UpdateRestApi, ip_address: str):
    request_body = copy.deepcopy(api.request.body)

    if api.source is not None:
        cur = request_body
        for i, hop in enumerate(api.source):
            if i == len(api.source) - 1:
                cur[hop] = ip_address
            else:
                if hop not in cur:
                    cur[hop] = {}
                cur = cur[hop]

        if cur is request_body:
            request_body = ip_address

    if api.request.method == 'PUT':
        response: requests.Response = requests.put(
            expand_env(api.request.path),
            json = expand_env(request_body),
            headers = expand_env(api.request.headers))
    elif api.request.method == 'POST':
        response: requests.Response = requests.post(
            expand_env(api.request.path),
            json = expand_env(request_body),
            headers = expand_env(api.request.headers))
    else:
        raise Exception(f'Unexpected HTTP method for setting an address: {api.request.method}')

    with response as res:
        if not res.ok:
            path: str = expand_env(api.request.path)
            raise Exception(f'{api.request.method} {path} returned {res.status_code} {res.reason}')


def update_domain(update: UpdateMethod, ip_address: str):
    if update.method == 'command':
        update_domain_with_command(update.command, ip_address)
    elif update.method == 'rest-api':
        update_domain_with_rest_api(update.rest_api, ip_address)
    else:
        raise Exception(f'Unexpected "update[].method": "{update.method}"')


def main():
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description = 'A demon for monitoring the ip address and updating the domain name')

    parser.add_argument('--config', type=str, metavar='CONFIG.json', default = '/etc/domaind/domaind.json')

    args: argparse.Namespace = parser.parse_args()

    with open(args.config, 'rt') as config_file:
        config: Config = parse_config(json.load(config_file))

    cur_ip: str | None = None
    todo: list[UpdateMethod] = []
    while True:
        new_ip: str | None = verify_ip(get_ip(config.source))
        if new_ip and new_ip != cur_ip:
            for update in config.updates:
                todo.append(update)
            os.environ['IP_ADDRESS'] = new_ip
            cur_ip = new_ip

        for i, update in reversed(list(enumerate(copy.copy(todo)))):
            try:
                update_domain(update, new_ip)
                todo.pop(i)
            except Exception as e:
                print(f'ERROR: Failed to update {update}:\n{e}', file=sys.stderr)
        time.sleep(10)


if __name__ == "__main__":
    main()

