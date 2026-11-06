"""
Shared helpers for the DI Workshop provisioning scripts (setup_all.py / delete_all.py):
Azure CLI + REST wrappers, interactive prompts, and validators.
"""
import getpass
import ipaddress
import json
import os
import re
import secrets
import string
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# getpass reads the console directly on Windows, so fall back to input() when stdin is piped.
INTERACTIVE = sys.stdin.isatty()

FABRIC_API = 'https://api.fabric.microsoft.com/v1'

# Fixed names. The lab instructions use these values literally, so they are not prompted for.
CAPACITY_NAME = 'fabriccapdiws'
CONTAINER_NAME = 'opendatasets'
SQL_DATABASE_NAME = 'sqldb-di-workshop'
GATEWAY_NAME = 'vnetgw-di-workshop-2026'
GRAPH_API = 'https://graph.microsoft.com/v1.0'


# ============================================================
# Azure CLI / REST
# ============================================================
def az(args):
    """Run an az CLI command (shell=True is required on Windows for az.cmd)."""
    cmd = subprocess.list2cmdline(args) if isinstance(args, list) else args
    return subprocess.run(cmd, capture_output=True, text=True, shell=True)


def az_json(args, default=None):
    r = az(args + ['-o', 'json'])
    if r.returncode != 0 or not r.stdout.strip():
        return default
    return json.loads(r.stdout)


def token_for(resource):
    r = az(['az', 'account', 'get-access-token', '--resource', resource, '--query', 'accessToken', '-o', 'tsv'])
    return r.stdout.strip()


def rest(method, url, token, body=None, form=None, fatal=True):
    """Call a REST API. Returns (response_dict, http_status). Secrets never touch a command line.

    Network timeouts are retried; with fatal=False a persistent failure returns (error, 0)
    instead of ending the script, so callers such as cleanup can continue with the next item.
    """
    headers = {}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    elif body is not None:
        data = json.dumps(body).encode()
        headers['Content-Type'] = 'application/json'
    if token:
        headers['Authorization'] = f'Bearer {token}'
    attempts = 3
    for attempt in range(attempts):
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            resp = urllib.request.urlopen(req, timeout=120)
            raw = resp.read().decode()
            return (json.loads(raw) if raw.strip() else {}), resp.status
        except urllib.error.HTTPError as e:
            err = e.read().decode() if e.fp else str(e)
            try:
                return {'error': json.loads(err)}, e.code
            except ValueError:
                return {'error': err[:500]}, e.code
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            host = urllib.parse.urlsplit(url).hostname
            if attempt + 1 < attempts:
                wait = 5 * (attempt + 1)
                print(f'  Could not reach {host} ({e}); retrying in {wait}s '
                      f'({attempt + 2}/{attempts})...', flush=True)
                time.sleep(wait)
                continue
            message = (f'  Cannot reach {host} over HTTPS: {e}. Check VPN, proxy, firewall, and DNS '
                       'access to this endpoint, then re-run. No response was received from the service.')
            if not fatal:
                print(message, flush=True)
                return {'error': str(e)}, 0
            sys.exit(message)


def error_text(resp, limit=300):
    return str(resp.get('error', resp))[:limit]


# ============================================================
# Prompts
# ============================================================
def section(title):
    print(f'\n--- {title} ---')


def ask(label, default=None, validate=None, required=True):
    """Prompt for a value. Enter accepts the default. validate(value) returns an error string or None."""
    while True:
        suffix = f' [{default}]' if default not in (None, '') else ''
        try:
            val = input(f'  {label}{suffix}: ').strip()
        except EOFError:
            sys.exit('\nInput ended — aborting.')
        if not val and default not in (None, ''):
            val = str(default)
        if not val:
            if not required:
                return ''
            print('    A value is required.')
            continue
        err = validate(val) if validate else None
        if err:
            print(f'    {err}')
            continue
        return val


def ask_yes_no(label, default=True):
    hint = 'Y/N'
    while True:
        try:
            val = input(f'  {label} [{hint}]: ').strip().lower()
        except EOFError:
            sys.exit('\nInput ended — aborting.')
        if not val:
            return default
        if val in ('y', 'yes'):
            return True
        if val in ('n', 'no'):
            return False
        print('    Please answer Y or N.')


def ask_choice(label, choices, default):
    lookup = {c.lower(): c for c in choices}
    return lookup[ask(f'{label} ({"/".join(choices)})', default,
                      lambda v: None if v.lower() in lookup else f'Choose one of: {", ".join(choices)}').lower()]


def ask_int(label, default, lo, hi):
    return int(ask(label, default, lambda v: None if v.isdigit() and lo <= int(v) <= hi
                   else f'Enter a whole number between {lo} and {hi}'))


def ask_secret(label, validate=None, allow_blank=False):
    """Prompt for a secret twice (hidden when run interactively). Blank returns '' if allowed."""
    reader = getpass.getpass if INTERACTIVE else input
    while True:
        try:
            val = reader(f'  {label}: ')
        except EOFError:
            sys.exit('\nInput ended — aborting.')
        if not val:
            if allow_blank:
                return ''
            print('    A value is required.')
            continue
        err = validate(val) if validate else None
        if err:
            print(f'    {err}')
            continue
        if INTERACTIVE and reader(f'  Confirm {label[0].lower() + label[1:]}: ') != val:
            print('    Values did not match — try again.')
            continue
        return val


def ask_list(label, default='', validate_item=None):
    """Comma-separated list prompt. Returns a list of trimmed, non-empty items."""
    def check(v):
        if validate_item:
            for item in [i.strip() for i in v.split(',') if i.strip()]:
                err = validate_item(item)
                if err:
                    return err
        return None
    raw = ask(label, default, check, required=False)
    return [i.strip() for i in raw.split(',') if i.strip()]


# ============================================================
# Validators
# ============================================================
GUID_RE = re.compile(r'^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$')

# Characters cmd.exe interprets; values containing them can't be passed safely on an az command line.
CMD_UNSAFE = set('%^&|<>"')
_PASSWORD_SYMBOLS = '!#@*-_+='


def password_error(pwd, command_line_safe=False):
    if not 8 <= len(pwd) <= 128:
        return 'Password must be 8-128 characters.'
    classes = sum([any(c.islower() for c in pwd), any(c.isupper() for c in pwd),
                   any(c.isdigit() for c in pwd), any(not c.isalnum() for c in pwd)])
    if classes < 3:
        return 'Password needs 3 of: lowercase, uppercase, digit, symbol.'
    if command_line_safe and CMD_UNSAFE & set(pwd):
        return f'Password cannot contain any of: {" ".join(sorted(CMD_UNSAFE))}'
    return None


def generate_password(length=20):
    alphabet = string.ascii_letters + string.digits + _PASSWORD_SYMBOLS
    while True:
        pwd = ''.join(secrets.choice(alphabet) for _ in range(length))
        if password_error(pwd, command_line_safe=True) is None and any(c in _PASSWORD_SYMBOLS for c in pwd):
            return pwd


def cidr_error(value):
    try:
        ipaddress.ip_network(value, strict=False)
        return None
    except ValueError:
        return f'"{value}" is not a valid IP address or CIDR range.'


def regex_error(pattern, message):
    return lambda v: None if re.fullmatch(pattern, v) else message
