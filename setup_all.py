#!/usr/bin/env python3
"""
Azure DI Workshop — Single Setup Script

Prompts for the environment values, then creates everything end-to-end:
   1. Resource group
   2. Fabric capacity
   3. ADLS Gen2 storage account (firewall: deny by default, allow-listed IPs only)
   4. Key Vault (RBAC, same firewall model) — holds the lab service principal secret
   5. NYC Taxi data copied with AzCopy
   6. Lab service principal (app registration) — secret stored in and read back from Key Vault
   7. Azure SQL server (private only) + AdventureWorksLT database
   8. VNet, private endpoints (SQL, blob, dfs, vault) and private DNS zones
   9. SQL prep for Fabric mirroring (SAMI, Directory Readers, Entra admin group)
  10. Security Defaults (only if you opt in)
  11. Entra ID workshop users
  12. Fabric capacity resume check
  13. One Fabric workspace per user on the capacity
  14. Fabric VNet data gateway for reaching the private endpoints
  15. Participant access to the data (Entra group -> db_datareader/db_datawriter and mirroring permissions on SQL,
      Storage Blob Data Reader on the storage account)
  16. Fabric managed private endpoints so workspace notebooks can reach the private SQL server
  17. Fabric tenant settings (service principal API access, OneLake access from external apps)
  18. Logic App that pauses the capacity every night
  19. workshop_environment.md handout with this deployment's values (gitignored)

Authentication uses Azure CLI. Setup can keep the signed-in user or interactively sign in as another
user in the selected tenant before creating resources.
Idempotent — safe to re-run with the same answers.
"""
import csv
import datetime
import glob
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile

from workshop_common import (
    FABRIC_API, GRAPH_API, SCRIPT_DIR, CAPACITY_NAME, CONTAINER_NAME, SQL_DATABASE_NAME, GATEWAY_NAME,
    az, az_json, token_for, rest, error_text, section,
    ask, ask_yes_no, ask_choice, ask_int, ask_secret, ask_list,
    password_error, generate_password, cidr_error, regex_error,
)

CREDS_FILE = os.path.join(SCRIPT_DIR, 'workshop_users.csv')
PASSWORDS_FILE = os.path.join(SCRIPT_DIR, 'workshop_user_passwords.csv')
PE_SUBNET = 'snet-private-endpoints'
GATEWAY_SUBNET = 'snet-fabric-gateway'
KV_SECRET_CLIENT_ID = 'lab-spn-client-id'
KV_SECRET_CLIENT_SECRET = 'lab-spn-client-secret'
KV_SECRET_TENANT_ID = 'lab-spn-tenant-id'
KV_SECRET_SQL_PASSWORD = 'sql-admin-password'
DIRECTORY_READERS_ROLE = '88d8e3e3-8f55-4a1e-953a-9b9898b8876b'
PROVIDERS = ['Microsoft.Fabric', 'Microsoft.Storage', 'Microsoft.Sql', 'Microsoft.KeyVault',
             'Microsoft.Network', 'Microsoft.PowerPlatform', 'Microsoft.Logic']
HANDOUT_FILE = os.path.join(SCRIPT_DIR, 'workshop_environment.md')
FABRIC_CAPACITY_API = '2023-11-01'
TENANT_SETTING_SPN_APIS = 'ServicePrincipalAccessPermissionAPIs'   # Service principals can call Fabric public APIs
TENANT_SETTING_ONELAKE_EXTERNAL = 'OneLakeForThirdParty'          # Users can access OneLake with apps external to Fabric
GATEWAY_SLEEP_CHOICES = ('30', '60', '90', '120', '150', '240', '360', '480', '720', '1440')


def windows_time_zones():
    """(local zone, list of valid Windows time zone IDs) — the format Logic App recurrence triggers use."""
    if os.name != 'nt':
        return 'UTC', []
    local = subprocess.run(['tzutil', '/g'], capture_output=True, text=True).stdout.strip() or 'UTC'
    lines = subprocess.run(['tzutil', '/l'], capture_output=True, text=True).stdout.splitlines()
    zones = [lines[i + 1].strip() for i, line in enumerate(lines[:-1]) if line.startswith('(UTC')]
    return local, zones


def mfa_policies(graph_token):
    """Enforced Conditional Access policies that make every user do MFA (or a risk-based MFA/password change),
    plus whether any policy blocks security-info (MFA) registration. Returns (policies, registration_blocked)."""
    resp, st = rest('GET', f'{GRAPH_API}/identity/conditionalAccess/policies', graph_token)
    if st != 200:
        return None, False
    found, reg_blocked = [], False
    for pol in resp.get('value', []):
        if pol.get('state') != 'enabled':
            continue
        cond, grant = pol.get('conditions') or {}, pol.get('grantControls') or {}
        controls = set(grant.get('builtInControls') or [])
        if 'urn:user:registersecurityinfo' in ((cond.get('applications') or {}).get('includeUserActions') or []) \
                and 'block' in controls:
            reg_blocked = True
            continue
        if 'All' in ((cond.get('users') or {}).get('includeUsers') or []) and \
                (controls & {'mfa', 'passwordChange'} or grant.get('authenticationStrength')):
            found.append({'id': pol['id'], 'name': pol['displayName']})
    return found, reg_blocked


def norm_region(value):
    return value.strip().lower().replace(' ', '')


def capacity_admin_error(value):
    """Fabric capacity administration needs a tenant-resolvable user principal name."""
    if '#EXT#' in value.upper() or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
        return 'Enter a member user UPN in this tenant (not a #EXT# guest UPN).'
    return None


def odata_eq(field, value):
    """URL-encoded OData equality filter."""
    escaped = value.replace("'", "''")
    return urllib.parse.quote(f"{field} eq '{escaped}'")


# ============================================================
# Prompts
# ============================================================
def select_subscription(account, tenant_id):
    """Select an enabled subscription by number within the confirmed tenant."""
    subs = az_json(['az', 'account', 'list', '--query',
                    f"[?tenantId=='{tenant_id}' && state=='Enabled'].{{id:id, name:name}}"], [])
    if not subs:
        sys.exit(f'  No enabled subscriptions are available in tenant {tenant_id}.')
    current_id = account.get('id', '').lower()
    current = next((s for s in subs if s['id'].lower() == current_id), None)
    print(f'\n  Available subscriptions in tenant {tenant_id}:')
    for number, sub in enumerate(subs, 1):
        marker = ' (current)' if sub['id'].lower() == current_id else ''
        print(f'    {number}. {sub["name"]} — {sub["id"]}{marker}')
    while True:
        choice = ask('Select a subscription and tenant (Type a number or Enter for no changes)',
                     validate=lambda v: None if v.isascii() and v.isdigit() and 1 <= int(v) <= len(subs)
                     else f'Enter a number from 1 to {len(subs)}.', required=False)
        if choice:
            return subs[int(choice) - 1]['id']
        if current:
            return current['id']
        print('    The current subscription is not enabled in this tenant. Choose a number.')


def collect_inputs():
    print('='*60)
    print('DI WORKSHOP — SETUP')
    print('='*60)
    print('Press Enter to accept the [default] shown for any prompt.')

    print('\n*** REQUIRED PERMISSIONS FOR SETUP USER ***')
    print('The Entra user running setup must have the following permissions in the deployment tenant:')
    print('  • Fabric Administrator (at tenant level)')
    print('  • User Administrator (at tenant level)')
    print('  • Global Administrator (at tenant level)')
    print('  • Subscription Owner (at subscription level)')
    print('If these permissions are not assigned, setup will fail. Contact your Azure admin if needed.\n')

    section('Azure sign-in')
    account = az_json(['az', 'account', 'show'])
    me = az_json(['az', 'ad', 'signed-in-user', 'show', '--query', '{id:id, upn:userPrincipalName}'])
    cached_user = (me or {}).get('upn') or ((account or {}).get('user') or {}).get('name')
    cached_tenant = (account or {}).get('tenantId')
    if cached_user:
        print(f'  Currently signed in as: {cached_user}')
    if cached_tenant:
        print(f'  Current tenant ID: {cached_tenant}')
    if account and not me:
        # Usually a stale Graph token (e.g. Conditional Access changed) — a fresh sign-in clears it.
        print('  The cached sign-in cannot read Entra right now; setup will sign in again.')
    deployment_user = ask('Entra user to run the entire setup (Azure CLI sign-in)', cached_user,
                          regex_error(r'[^\s@]+@[^\s@]+', 'Enter a user principal name (user@domain).'))
    tenant_id = ask('Tenant ID to deploy into', cached_tenant,
                    regex_error(r'[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}',
                                'Enter a valid tenant ID (GUID).'))
    signed_in_names = {n.lower() for n in (cached_user, (me or {}).get('upn')) if n}
    if not me or tenant_id.lower() != (cached_tenant or '').lower() or deployment_user.lower() not in signed_in_names:
        print(f'  Sign in as {deployment_user} in tenant {tenant_id} using the device-code prompt.', flush=True)
        login = subprocess.run(subprocess.list2cmdline(['az', 'login', '--tenant', tenant_id,
                                                         '--use-device-code', '--output', 'none']),
                               shell=True, env=dict(os.environ, AZURE_CORE_LOGIN_EXPERIENCE_V2='off'))
        if login.returncode != 0:
            sys.exit('  Azure CLI sign-in failed. Setup has not started.')

        # Device-code login already provides fresh tokens; just wait a moment for CLI to stabilize
        time.sleep(2)

        # Retry reading account and user info with retries
        account = None
        me = None
        for attempt in range(5):
            account = az_json(['az', 'account', 'show'])
            if account:
                break
            if attempt < 4:
                print(f'  Waiting for account info (attempt {attempt + 1}/5)...', flush=True)
                time.sleep(2)

        for attempt in range(5):
            me = az_json(['az', 'ad', 'signed-in-user', 'show', '--query', '{id:id, upn:userPrincipalName}'])
            if me:
                break
            if attempt < 4:
                print(f'  Waiting for Entra user info (attempt {attempt + 1}/5)...', flush=True)
                time.sleep(2)

    if not account or account.get('tenantId', '').lower() != tenant_id.lower():
        sys.exit(f'  Azure CLI is not using tenant {tenant_id}. Select an account in that tenant and re-run setup.')
    if not me:
        sys.exit(f'  Could not read {deployment_user} from Entra after signing in. A user login '
                 '(not a service principal) is required, and the account must be able to read its own profile. '
                 'Try: (1) Sign in at https://app.fabric.microsoft.com to activate Fabric; (2) Check tenant settings '
                 'allow Graph API access; (3) Verify your account has Entra role "Directory Readers" or higher.')
    if deployment_user.lower() not in (me['upn'].lower(), account['user']['name'].lower()):
        sys.exit(f'  Azure CLI is not signed in as {deployment_user} in tenant {tenant_id}. '
                 'Sign in with the requested account and re-run setup.')
    graph_token = token_for('https://graph.microsoft.com')
    org, _ = rest('GET', f'{GRAPH_API}/organization?$select=displayName,verifiedDomains', graph_token)
    org = (org.get('value') or [{}])[0]
    domains = [d['name'] for d in org.get('verifiedDomains', [])]
    default_domain = next((d['name'] for d in org.get('verifiedDomains', []) if d.get('isDefault')),
                          domains[0] if domains else '')
    print(f'  Setup will run as: {me["upn"]} ({account["user"]["name"]})')
    print(f'  Deployment tenant: {org.get("displayName", "?")} ({tenant_id})')

    resp, st = rest('GET', f'{FABRIC_API}/workspaces', token_for('https://api.fabric.microsoft.com'))
    if st != 200:
        code = resp.get('error', {}).get('errorCode') if isinstance(resp.get('error'), dict) else error_text(resp, 200)
        sys.exit(f'  Fabric is not usable for {me["upn"]} ({st}: {code}).\n'
                 '  Sign in once at https://app.fabric.microsoft.com with this account to activate Fabric for the\n'
                 '  tenant and get a Fabric (Free) license, then re-run.')
    deployment_admin_upn = me['upn']
    admin_error = capacity_admin_error(deployment_admin_upn)
    if admin_error:
        sys.exit(f'  {admin_error} The signed-in account cannot administer a Fabric capacity.')

    subscription_id = select_subscription(account, tenant_id)
    r = az(['az', 'account', 'set', '--subscription', subscription_id])
    if r.returncode != 0:
        sys.exit(f'  Could not select subscription: {r.stderr[:300]}')
    mgmt_token = token_for('https://management.azure.com/')

    # Name-availability checks below fail with SubscriptionNotFound until the providers are registered.
    pending = [p for p in PROVIDERS if az(['az', 'provider', 'show', '-n', p, '--query', 'registrationState',
                                           '-o', 'tsv']).stdout.strip() != 'Registered']
    if pending:
        print(f'  Registering resource providers: {", ".join(pending)} (can take a few minutes)...', flush=True)
        for p in pending:
            az(['az', 'provider', 'register', '-n', p, '--wait'])

    domain = ask('Domain for workshop user accounts', default_domain,
                 lambda v: None if not domains or v.lower() in [d.lower() for d in domains]
                 else f'Not a verified domain. Options: {", ".join(domains)}')

    section('Location & resource group')
    regions = az_json(['az', 'account', 'list-locations', '--query',
                       "[?metadata.regionType=='Physical'].name"], [])
    region_ok = lambda v: None if not regions or norm_region(v) in regions else 'Unknown Azure region name.'
    location = norm_region(ask('Azure region (e.g. eastus2, westus3, westeurope)', 'eastus2', region_ok))
    resource_group = ask('Resource group', 'rg-di-workshop',
                         regex_error(r'[\w\-\.\(\)]{1,89}[\w\-\(\)]', 'Invalid resource group name.'))
    if az(['az', 'group', 'exists', '-n', resource_group]).stdout.strip() == 'true':
        print('    (exists — resources in it will be reused)')

    def storage_name_error(v):
        if not v.isalnum() or not v.islower() or not 3 <= len(v) <= 24:
            return '3-24 lowercase letters and digits.'
        r = az(['az', 'storage', 'account', 'check-name', '--name', v, '-o', 'json'])
        if r.returncode != 0:
            return f'Could not check availability: {r.stderr.strip()[:200]}'
        chk = json.loads(r.stdout)
        if chk.get('nameAvailable') or az(['az', 'storage', 'account', 'show', '-n', v, '-g', resource_group]).returncode == 0:
            return None
        return f'Not available: {chk.get("message") or chk.get("reason")}'

    def sql_name_error(v):
        err = regex_error(r'[a-z0-9][a-z0-9-]{0,61}[a-z0-9]', 'Lowercase letters, digits and hyphens (2-63).')(v)
        if err:
            return err
        resp, st = rest('POST', f'https://management.azure.com/subscriptions/{subscription_id}/providers/'
                        'Microsoft.Sql/checkNameAvailability?api-version=2021-11-01', mgmt_token,
                        {'name': v, 'type': 'Microsoft.Sql/servers'})
        if resp.get('available') or az(['az', 'sql', 'server', 'show', '-n', v, '-g', resource_group]).returncode == 0:
            return None
        return f'Not available: {resp.get("message") or resp.get("reason") or error_text(resp)}'

    def kv_name_error(v):
        err = regex_error(r'[a-zA-Z][a-zA-Z0-9-]{1,22}[a-zA-Z0-9]', '3-24 letters, digits and hyphens, starting with a letter.')(v)
        if err or '--' in v:
            return err or 'No consecutive hyphens.'
        if az(['az', 'keyvault', 'show', '-n', v, '-g', resource_group]).returncode == 0:
            return None
        resp, st = rest('POST', f'https://management.azure.com/subscriptions/{subscription_id}/providers/'
                        'Microsoft.KeyVault/checkNameAvailability?api-version=2022-07-01', mgmt_token,
                        {'name': v, 'type': 'Microsoft.KeyVault/vaults'})
        if resp.get('nameAvailable'):
            return None
        if az(['az', 'keyvault', 'show-deleted', '--name', v]).returncode == 0:
            if ask_yes_no(f'"{v}" is a soft-deleted vault in this subscription. Purge it now (takes a few minutes)?', False):
                print('    Purging...')
                r = az(['az', 'keyvault', 'purge', '--name', v])
                return None if r.returncode == 0 else f'Purge failed: {r.stderr[:200]}'
            return 'Name is held by a soft-deleted vault. Purge it or pick another name.'
        return f'Not available: {resp.get("message") or resp.get("reason") or error_text(resp)}'

    section('Globally unique names')
    suffix = ask('Short suffix to make names globally unique (lowercase letters/digits)', None,
                 regex_error(r'[a-z0-9]{1,8}', '1-8 lowercase letters or digits.'))
    storage_account = ask('Storage account name', f'adlsdiws{suffix}', storage_name_error)
    sql_server = ask('SQL server name', f'sqldiws{suffix}', sql_name_error)
    key_vault = ask('Key Vault name', f'kv-diws-{suffix}', kv_name_error)

    section('Fabric capacity')
    fabric_locations = az_json(['az', 'provider', 'show', '-n', 'Microsoft.Fabric', '--query',
                                "resourceTypes[?resourceType=='capacities'].locations | [0]"], [])
    fabric_regions = {norm_region(l) for l in fabric_locations}
    capacity_sku = ask('Capacity SKU (F2, F4, F8, ... F64 ...)', 'F64', regex_error(r'[Ff]\d+', 'Must look like F2, F64, ...')).upper()
    capacity_cus = int(capacity_sku[1:])
    existing_cap = az_json(['az', 'fabric', 'capacity', 'show', '-g', resource_group, '--capacity-name', CAPACITY_NAME])
    capacity_admins = [deployment_admin_upn]

    def fabric_quota_free(region):
        resp, st = rest('GET', f'https://management.azure.com/subscriptions/{subscription_id}/providers/'
                        f'Microsoft.Fabric/locations/{region}/usages?api-version=2023-11-01', mgmt_token)
        quota = next((u for u in resp.get('value', []) if u.get('name', {}).get('value') == 'CapacityQuota'), None)
        return None if st != 200 or not quota else quota['limit'] - quota['currentValue']

    def capacity_region_error(v):
        region = norm_region(v)
        if fabric_regions and region not in fabric_regions:
            return 'Fabric capacities are not available in that region.'
        if existing_cap and norm_region(existing_cap.get('location', '')) == region:
            return None
        free = fabric_quota_free(region)
        if free is not None and free < capacity_cus:
            return (f'Only {free} CU of Fabric quota free in {region}; {capacity_sku} needs {capacity_cus}. '
                    'Pick another region/SKU or request a quota increase.')
        return None

    cap_default = location
    if capacity_region_error(location):
        cap_default = next((r for r in ('centralus', 'westus2', 'westus3', 'eastus', 'eastus2', 'southcentralus',
                                        'northcentralus', 'westus') if not capacity_region_error(r)), location)
        print(f'  Not enough Fabric quota for {capacity_sku} in {location} — suggesting {cap_default}.')
    capacity_location = norm_region(ask('Capacity region', cap_default, capacity_region_error))
    pause_nightly = ask_yes_no('Pause the capacity automatically every night?', True)
    pause_time, pause_tz = '20:00', ''
    if pause_nightly:
        pause_time = ask('Pause time (24-hour HH:MM)', '20:00',
                         regex_error(r'([01]\d|2[0-3]):[0-5]\d', 'Use 24-hour HH:MM, e.g. 20:00.'))
        local_tz, zones = windows_time_zones()
        zone_lookup = {z.lower(): z for z in zones}
        pause_tz = ask('Time zone (Windows time zone name)', local_tz,
                       lambda v: None if not zones or v.lower() in zone_lookup
                       else 'Unknown Windows time zone. Run `tzutil /l` to list names.')
        pause_tz = zone_lookup.get(pause_tz.lower(), pause_tz)

    section('Fabric tenant settings')
    print('  The labs need "Service principals can call Fabric public APIs" (scoped to a group containing the')
    print('  lab service principal) and "Users can access data stored in OneLake with apps external to Fabric".')
    print('  Settings that are already enabled more broadly are left as they are. Requires Fabric administrator.')
    configure_tenant = ask_yes_no('Configure these Fabric tenant settings?', True)

    section('Network access')
    my_ip = urllib.request.urlopen('https://api.ipify.org').read().decode().strip()
    print('  SQL is private-only. Storage and Key Vault deny public traffic except allow-listed IPs.')
    print(f'  Your current public IP ({my_ip}) is always allow-listed, plus the IP Azure actually sees if it differs.')
    extra_ips = ask_list('Additional IPs/CIDRs to allow (comma-separated, blank for none)', '', cidr_error)
    tags = ask_list('Tags for storage and Key Vault, e.g. SecurityControl=Ignore (comma-separated, blank for none)', '',
                    lambda t: None if '=' in t and t.split('=', 1)[0].strip() else f'"{t}" must be name=value')

    section('Data')
    folders = ask('NYC Taxi folders to copy (comma-separated, "all", or "none")', 'yellow',
                  regex_error(r'[a-z0-9_,\s]+', 'Folder names, "all" or "none".'))
    folders = [] if folders.lower() == 'none' else [''] if folders.lower() == 'all' else \
        [f.strip().strip('/') for f in folders.split(',') if f.strip()]

    section('Azure SQL')

    def sql_region_error(v):
        if norm_region(v) not in regions:
            return 'Unknown Azure region name.'
        caps, st = rest('GET', f'https://management.azure.com/subscriptions/{subscription_id}/providers/'
                        f'Microsoft.Sql/locations/{norm_region(v)}/capabilities?api-version=2021-11-01', mgmt_token)
        if st == 200 and caps.get('status') == 'Available':
            return None
        return f"SQL can't be provisioned there for this subscription: {caps.get('reason') or error_text(caps, 200)}"

    sql_default = location
    if sql_region_error(location):
        sql_default = next((r for r in ('centralus', 'westus2', 'westus3', 'southcentralus', 'eastus', 'eastus2')
                            if not sql_region_error(r)), location)
        print(f'  SQL servers cannot be created in {location} for this subscription — suggesting {sql_default}.')
    sql_location = norm_region(ask('SQL server region', sql_default, sql_region_error))
    entra_only = ask_yes_no('Use Microsoft Entra authentication only (recommended)? (No = also create a SQL admin login)', True)
    sql_admin_user = sql_admin_password = ''
    if not entra_only:
        sql_admin_user = ask('SQL admin login', 'sqladmin',
                             regex_error(r'[A-Za-z][A-Za-z0-9_]{2,127}', 'Letters, digits, underscore; start with a letter.'))
        sql_admin_password = ask_secret('SQL admin password (blank = generate)',
                                        lambda p: password_error(p, command_line_safe=True) or
                                        ('Password cannot contain the login name.' if sql_admin_user.lower() in p.lower() else None),
                                        allow_blank=True) or generate_password()

    section('Workshop users')
    user_count = ask_int('Number of workshop users', 5, 1, 99)

    ca_policies, reg_blocked = mfa_policies(graph_token)
    ca_exclude = False
    if ca_policies is None:
        print('  (Could not read Conditional Access policies — skipping the MFA exclusion question)')
        ca_policies = []
    elif ca_policies:
        print('  These Conditional Access policies require MFA for all users:')
        for pol in ca_policies:
            print(f"    - {pol['name']}")
        if reg_blocked:
            print('  Another policy blocks MFA registration outside trusted locations, so new workshop users')
            print('  will not be able to sign in unless they are excluded.')
        ca_exclude = ask_yes_no('Exclude the workshop participants group from these policies?', reg_blocked)

    advanced = ask_yes_no('Customize advanced settings (names, prefixes, and VNet ranges)?', False)
    s = dict(
        me_upn=me['upn'], me_oid=me['id'], my_ip=my_ip, tenant_id=tenant_id, subscription_id=subscription_id,
        domain=domain, location=location, resource_group=resource_group,
        storage_account=storage_account, sql_server=sql_server, sql_location=sql_location, key_vault=key_vault,
        capacity_name=CAPACITY_NAME, capacity_sku=capacity_sku, capacity_location=capacity_location,
        capacity_admins=capacity_admins, fabric_admin_upn=deployment_admin_upn,
        allowed_ips=sorted(set(extra_ips + [my_ip])), tags=tags,
        container=CONTAINER_NAME, nyc_folders=folders,
        sql_database=SQL_DATABASE_NAME, sql_service_objective='S3', sql_entra_only=entra_only,
        sql_admin_user=sql_admin_user, sql_admin_password=sql_admin_password,
        sql_admin_group='DI Workshop SQL Admins', lab_app_name='DIworkshop',
        user_count=user_count, user_prefix='diuser', user_display_prefix='DI Workshop User',
        workspace_prefix='DIWORKSPACE_',
        vnet_name='vnet-di-workshop', vnet_prefix='10.60.0.0/16', pe_subnet_prefix='10.60.1.0/24',
        gw_subnet_prefix='10.60.2.0/24', gateway_name=GATEWAY_NAME, gateway_sleep=120,
        disable_security_defaults=False,
        pause_nightly=pause_nightly, pause_time=pause_time, pause_tz=pause_tz,
        pause_logic_app='la-pause-fabric-capacity',
        configure_tenant=configure_tenant, spn_api_group='DI Workshop Service Principals',
        participants_group='DI Workshop Participants',
        ca_exclude=ca_exclude, ca_policies=ca_policies if ca_exclude else [],
    )
    if advanced:
        section('Advanced')
        s['sql_service_objective'] = ask('SQL service objective (e.g. S0, S3, GP_S_Gen5_2)', s['sql_service_objective'])
        s['sql_admin_group'] = ask('Entra group used as SQL admin (you + lab SPN)', s['sql_admin_group'])
        s['lab_app_name'] = ask('Lab service principal (app registration) name', s['lab_app_name'])
        s['user_prefix'] = ask('User name prefix', s['user_prefix'],
                               regex_error(r'[a-z][a-z0-9]{0,15}', 'Lowercase letters/digits, starting with a letter.'))
        s['user_display_prefix'] = ask('User display name prefix', s['user_display_prefix'])
        s['participants_group'] = ask('Entra group for workshop users (gets read/write on the database)',
                                      s['participants_group'])
        s['workspace_prefix'] = ask('Workspace name prefix', s['workspace_prefix'])
        s['vnet_name'] = ask('VNet name', s['vnet_name'])
        s['vnet_prefix'] = ask('VNet address space', s['vnet_prefix'], cidr_error)
        s['pe_subnet_prefix'] = ask('Private endpoint subnet range', s['pe_subnet_prefix'], cidr_error)
        s['gw_subnet_prefix'] = ask('Gateway subnet range', s['gw_subnet_prefix'], cidr_error)
        s['gateway_sleep'] = int(ask(f'Gateway auto-sleep minutes ({"/".join(GATEWAY_SLEEP_CHOICES)})', '120',
                                     lambda v: None if v in GATEWAY_SLEEP_CHOICES else 'Choose one of the listed values.'))
        if pause_nightly:
            s['pause_logic_app'] = ask('Logic App that pauses the capacity', s['pause_logic_app'],
                                       regex_error(r'[\w\-\.\(\)]{1,80}', 'Letters, digits, - _ . ( ) only.'))
        if configure_tenant:
            s['spn_api_group'] = ask('Entra group allowed to call Fabric APIs (contains the lab SPN)', s['spn_api_group'])
        s['disable_security_defaults'] = ask_yes_no(
            'Disable tenant Security Defaults (MFA) for the WHOLE tenant? Demo tenants only', False)

    s['users'] = [{
        'name': f"{s['user_prefix']}{i:d}",
        'display': f"{s['user_display_prefix']} {i:d}",
        'upn': f"{s['user_prefix']}{i:d}@{domain}",
    } for i in range(1, user_count + 1)]

    section('Review')
    print(f"""  Tenant / subscription:  {tenant_id} / {subscription_id}
  Resource group:         {resource_group} ({location})
  Fabric capacity:        {s['capacity_name']} ({capacity_sku}, {capacity_location})
  Storage account:        {storage_account} (container {s['container']}, NYC Taxi: {', '.join(folders) if folders and folders != [''] else 'all' if folders else 'skip'})
  Key Vault:              {key_vault}
  SQL server / database:  {sql_server} ({sql_location}) / {s['sql_database']} ({s['sql_service_objective']}, {'Entra-only' if entra_only else 'Entra + SQL login ' + sql_admin_user})
  SQL Entra admin group:  {s['sql_admin_group']}
  Lab service principal:  {s['lab_app_name']}
  Allow-listed IPs:       {', '.join(s['allowed_ips'])}
  Tags (storage, KV):     {', '.join(tags) or 'none'}
  VNet / gateway:         {s['vnet_name']} ({s['vnet_prefix']}) / {s['gateway_name']}
    Users:                  {s['users'][0]['upn']} … {s['users'][-1]['upn']} ({user_count}, generated passwords with username suffixes)
    Workspaces:             {s['workspace_prefix']}{s['users'][0]['name']} … (each user as Admin, {s['fabric_admin_upn']} as Admin)
  Nightly capacity pause: {f"{pause_time} {pause_tz} ({s['pause_logic_app']})" if pause_nightly else 'no'}
  Fabric tenant settings: {f"configure (group: {s['spn_api_group']})" if configure_tenant else 'leave unchanged'}
  Exclude users from MFA policies: {f"yes ({len(s['ca_policies'])} policies)" if s['ca_exclude'] else 'no'}
  Disable Security Defaults: {'YES' if s['disable_security_defaults'] else 'no'}""")
    if not ask_yes_no('Proceed with deployment?', True):
        sys.exit('  Aborted — nothing was created.')
    return s


S = collect_inputs()
PROBLEMS = []


def problem(msg):
    PROBLEMS.append(msg)
    print(f'  ERROR: {msg}')
RG, LOC = S['resource_group'], S['location']
ME_UPN, ME_OID = S['me_upn'], S['me_oid']
TAG_ARGS = ['--tags'] + S['tags'] if S['tags'] else []


def ensure_role(role, scope, principal_id, principal_type='User', required=False):
    """Assign a role; required assignments must succeed before deployment continues."""
    def already_assigned():
        assignments = az_json(['az', 'role', 'assignment', 'list', '--scope', scope,
                               '--include-inherited', '--query',
                               f"[?principalId=='{principal_id}' && roleDefinitionName=='{role}'].id"], [])
        return bool(assignments)

    if required and already_assigned():
        return True
    r = az(['az', 'role', 'assignment', 'create', '--assignee-object-id', principal_id,
            '--assignee-principal-type', principal_type, '--role', role, '--scope', scope])
    if r.returncode == 0 or 'already exists' in r.stderr.lower():
        return True
    if required:
        # Another administrator may have granted the role while this command ran.
        if already_assigned():
            return True
        sys.exit(f'  Required role assignment failed: {role}\n'
                 f'  User object ID: {principal_id}\n'
                 f'  Scope: {scope}\n'
                 f'  Azure CLI error: {r.stderr.strip()[:1000]}\n'
                 '  Contributor cannot assign Azure roles. Have an Owner, User Access Administrator,\n'
                 '  or Role Based Access Control Administrator (with permission to assign this role)\n'
                 '  grant the role above to this user, then re-run setup.\n'
                 '  Stopping now instead of waiting for an assignment that was not created.')
    print(f'  Role assignment warning ({role}): {r.stderr[:300]}')
    return False


def lock_public_access(kind, name):
    """Public endpoint on, default deny, only allow-listed IPs; tags applied first so governance policies see them."""
    rid = az(['az'] + kind.split() + ['show', '-n', name, '-g', RG, '--query', 'id', '-o', 'tsv']).stdout.strip()
    if S['tags'] and rid:
        az(['az', 'resource', 'tag', '-i', '--ids', rid, '--tags'] + S['tags'])
    if kind == 'keyvault':
        az(['az', 'keyvault', 'update', '-n', name, '-g', RG, '--public-network-access', 'Enabled',
            '--default-action', 'Deny', '--bypass', 'AzureServices'])
        for ip in S['allowed_ips']:
            az(['az', 'keyvault', 'network-rule', 'add', '-n', name, '-g', RG, '--ip-address', ip])
        eff = az_json(['az', 'keyvault', 'show', '-n', name, '-g', RG, '--query',
                       '{p:properties.publicNetworkAccess, d:properties.networkAcls.defaultAction}'], {})
    else:
        az(['az', 'storage', 'account', 'update', '-n', name, '-g', RG, '--public-network-access', 'Enabled',
            '--default-action', 'Deny', '--bypass', 'AzureServices'])
        for ip in S['allowed_ips']:
            az(['az', 'storage', 'account', 'network-rule', 'add', '--account-name', name, '-g', RG, '--ip-address', ip])
        eff = az_json(['az', 'storage', 'account', 'show', '-n', name, '-g', RG, '--query',
                       '{p:publicNetworkAccess, d:networkRuleSet.defaultAction}'], {})
    print(f"  Firewall: publicNetworkAccess={eff.get('p')}, defaultAction={eff.get('d')}, "
          f"allowed: {', '.join(S['allowed_ips'])}")
    if eff.get('p') != 'Enabled':
        print('  WARNING: public access was forced off (likely an Azure Policy). This machine cannot reach it;'
              ' re-run with a policy exemption tag in "Tags".')
    return rid


# ============================================================
# Step 1: Providers & resource group
# ============================================================
print('\n[Step 1] Resource group...')
if az(['az', 'group', 'exists', '-n', RG]).stdout.strip() == 'true':
    state = az(['az', 'group', 'show', '-n', RG, '--query', 'properties.provisioningState', '-o', 'tsv']).stdout.strip()
    if state == 'Deleting':
        sys.exit(f'  Resource group {RG} is still being deleted. Wait until Azure finishes deleting it, '
                 'then re-run setup. Capacity creation cannot succeed while the group is deleting.')
if az(['az', 'group', 'exists', '-n', RG]).stdout.strip() == 'true':
    print(f'  Resource group exists: {RG}')
else:
    r = az(['az', 'group', 'create', '-n', RG, '-l', LOC])
    if r.returncode != 0:
        sys.exit(f'  Resource group creation failed: {r.stderr[:300]}')
    print(f'  Resource group created: {RG}')

# ============================================================
# Step 2: Fabric capacity
# ============================================================
print(f"\n[Step 2] Fabric {S['capacity_sku']} capacity in {S['capacity_location']}...")
az(['az', 'extension', 'add', '--name', 'microsoft-fabric', '--upgrade', '-y'])
cap = az_json(['az', 'fabric', 'capacity', 'show', '-g', RG, '--capacity-name', S['capacity_name']])
if cap:
    print(f"  Already exists: {S['capacity_name']} ({cap.get('location')}, {cap.get('sku', {}).get('name')})")
else:
    r = az(['az', 'fabric', 'capacity', 'create', '-g', RG, '--capacity-name', S['capacity_name'],
            '--sku', json.dumps({'name': S['capacity_sku'], 'tier': 'Fabric'}, separators=(',', ':')),
            '--administration', json.dumps({'members': S['capacity_admins']}, separators=(',', ':')),
            '--location', S['capacity_location']])
    if r.returncode == 0:
        print(f"  Created: {S['capacity_name']}")
    else:
        detail = r.stderr.strip()
        if 'Invalid principal name' in detail:
            detail += ('\n  Fabric rejected the capacity administrator. Specify a member user UPN in this'
                       ' tenant under Capacity admins (not a #EXT# guest UPN), then re-run.')
        sys.exit(f"  Fabric capacity {S['capacity_name']} not created: {detail[:1000]}\n"
                 '  Stopping before provisioning resources that depend on this capacity.')

# ============================================================
# Step 3: Storage account
# ============================================================
print('\n[Step 3] ADLS Gen2 storage account...')
SA = S['storage_account']
if az(['az', 'storage', 'account', 'show', '-n', SA, '-g', RG]).returncode == 0:
    print(f'  Already exists: {SA}')
else:
    r = az(['az', 'storage', 'account', 'create', '-n', SA, '-g', RG, '-l', LOC, '--sku', 'Standard_LRS',
            '--kind', 'StorageV2', '--hns', 'true', '--public-network-access', 'Enabled',
            '--default-action', 'Deny', '--bypass', 'AzureServices'] + TAG_ARGS)
    if r.returncode == 0:
        print(f'  Created: {SA} (HNS enabled)')
    else:
        problem(f'Storage account {SA} not created: {r.stderr.strip()[:300]}')
storage_id = lock_public_access('storage account', SA)
r = az(['az', 'storage', 'container-rm', 'create', '--name', S['container'], '--storage-account', SA, '-g', RG])
print(f"  Container ready: {S['container']}" if r.returncode == 0 or 'already exists' in r.stderr.lower()
      else f'  Container creation failed: {r.stderr[:300]}')

# ============================================================
# Step 4: Key Vault
# ============================================================
print('\n[Step 4] Key Vault...')
KV = S['key_vault']
KV_URL = f'https://{KV}.vault.azure.net'
if az(['az', 'keyvault', 'show', '-n', KV, '-g', RG]).returncode == 0:
    print(f'  Already exists: {KV}')
else:
    r = az(['az', 'keyvault', 'create', '-n', KV, '-g', RG, '-l', LOC, '--enable-rbac-authorization', 'true',
            '--public-network-access', 'Enabled', '--default-action', 'Deny', '--bypass', 'AzureServices',
            '--network-acls-ips'] + S['allowed_ips'] + TAG_ARGS)
    if r.returncode != 0:
        sys.exit(f'  Key Vault creation failed: {r.stderr[:400]}')
    print(f'  Created: {KV} (RBAC authorization)')
kv_id = lock_public_access('keyvault', KV)
print(f'  Ensuring Key Vault Secrets Officer access for {ME_UPN} ({ME_OID})...')
ensure_role('Key Vault Secrets Officer', kv_id, ME_OID, required=True)
print(f'  {ME_UPN}: Key Vault Secrets Officer assignment confirmed')


def allow_azure_seen_ip(error_message):
    """Split-tunnel VPNs/proxies can make Azure see a different public IP than api.ipify.org.
    Key Vault's firewall error names the address it saw — allow-list that on Key Vault and storage."""
    m = re.search(r'Client address: ([0-9a-fA-F.:]+)', error_message)
    if not m or m.group(1) in S['allowed_ips']:
        return False
    ip = m.group(1)
    print(f'  Azure sees this machine as {ip} (not {S["my_ip"]}) — adding it to the Key Vault and storage firewalls')
    S['allowed_ips'].append(ip)
    az(['az', 'keyvault', 'network-rule', 'add', '-n', KV, '-g', RG, '--ip-address', ip])
    az(['az', 'storage', 'account', 'network-rule', 'add', '--account-name', SA, '-g', RG, '--ip-address', ip])
    return True


def kv_call(method, name, value=None, retries=20):
    """Read/write a Key Vault secret over REST, retrying while RBAC propagates."""
    body = {'value': value, 'contentType': 'text/plain'} if value is not None else None
    for attempt in range(retries):
        resp, st = rest(method, f'{KV_URL}/secrets/{name}?api-version=7.4', token_for('https://vault.azure.net'), body)
        if st == 200:
            return resp.get('value')
        if st == 404:
            return None
        text = error_text(resp, 2000)
        if 'ForbiddenByFirewall' in text or 'not authorized and caller is not a trusted service' in text:
            if allow_azure_seen_ip(text) or attempt < 8:
                if attempt == 0:
                    print('  Waiting for the Key Vault firewall rule to apply...', flush=True)
                time.sleep(15)
                continue
            sys.exit(f'  Key Vault firewall blocked this machine: {text[:400]}')
        if st in (401, 403) and attempt < retries - 1:
            if attempt == 0:
                print('  Waiting for Key Vault RBAC to propagate...', flush=True)
            time.sleep(15)
            continue
        sys.exit(f'  Key Vault {method} {name} failed ({st}): {text}')


# First data-plane call: confirms access and learns the IP Azure sees before the storage copy needs it.
kv_call('GET', KV_SECRET_CLIENT_ID)
print('  Key Vault data access confirmed')


# ============================================================
# Step 5: NYC Taxi data
# ============================================================
print('\n[Step 5] NYC Taxi data (AzCopy)...')
if not S['nyc_folders']:
    print('  Skipped')
else:
    azcopy_exe = shutil.which('azcopy')
    if not azcopy_exe:
        cached = glob.glob(os.path.join(SCRIPT_DIR, 'azcopy_install', '**', 'azcopy.exe'), recursive=True)
        azcopy_exe = cached[0] if cached else None
    if not azcopy_exe:
        print('  Downloading AzCopy v10...')
        zip_path = os.path.join(SCRIPT_DIR, 'azcopy.zip')
        urllib.request.urlretrieve('https://aka.ms/downloadazcopy-v10-windows', zip_path)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(os.path.join(SCRIPT_DIR, 'azcopy_install'))
        os.remove(zip_path)
        found = glob.glob(os.path.join(SCRIPT_DIR, 'azcopy_install', '**', 'azcopy.exe'), recursive=True)
        azcopy_exe = found[0] if found else None
    if not azcopy_exe:
        problem('AzCopy not available — data copy skipped')
    else:
        ensure_role('Storage Blob Data Contributor', storage_id, ME_OID)
        print(f'  {ME_UPN}: Storage Blob Data Contributor — waiting for data access...', end=' ', flush=True)
        # Data-plane RBAC can take several minutes to apply; probe before copying.
        for attempt in range(40):
            probe = az(['az', 'storage', 'blob', 'list', '--account-name', SA, '-c', S['container'],
                        '--auth-mode', 'login', '--num-results', '1', '-o', 'none'])
            if probe.returncode == 0:
                print('ready')
                break
            time.sleep(15)
        else:
            print('still denied after 10 minutes — copy will likely fail; re-run setup later')
        env = dict(os.environ, AZCOPY_AUTO_LOGIN_TYPE='AZCLI', AZCOPY_TENANT_ID=S['tenant_id'])
        for folder in S['nyc_folders']:
            sub = f'{folder}/' if folder else ''
            print(f'  Copying nyctlc/{sub} ...', end=' ', flush=True)
            r = subprocess.run([azcopy_exe, 'copy', f'https://azureopendatastorage.blob.core.windows.net/nyctlc/{sub}*',
                                f"https://{SA}.blob.core.windows.net/{S['container']}/nyctlc/{sub}",
                                '--recursive', '--overwrite=ifSourceNewer', '--log-level', 'ERROR'],
                               capture_output=True, text=True, env=env)
            summary = [l.strip() for l in r.stdout.splitlines()
                       if l.startswith(('Number of File Transfers', 'Final Job Status'))]
            print(('Done — ' if r.returncode == 0 else 'FAILED — ') + '; '.join(summary))
            if r.returncode != 0:
                problem(f'NYC Taxi copy of nyctlc/{sub} failed (see ~/.azcopy logs); re-run setup to retry')

# ============================================================
# Step 6: Lab service principal + Key Vault secret
# ============================================================
print('\n[Step 6] Lab service principal...')
graph_token = token_for('https://graph.microsoft.com')
app_name = S['lab_app_name']
apps, _ = rest('GET', f"{GRAPH_API}/applications?$filter={odata_eq('displayName', app_name)}&$select=id,appId", graph_token)
apps = apps.get('value', [])
if len(apps) > 1:
    print(f'  WARNING: {len(apps)} apps named "{app_name}" — using {apps[0]["appId"]}')
if apps:
    app = apps[0]
    print(f"  App registration exists: {app_name} ({app['appId']})")
else:
    app, st = rest('POST', f'{GRAPH_API}/applications', graph_token,
                   {'displayName': app_name, 'signInAudience': 'AzureADMyOrg'})
    if st not in (200, 201):
        sys.exit(f'  App registration failed ({st}): {error_text(app)}')
    print(f"  App registration created: {app_name} ({app['appId']})")
APP_ID = app['appId']
sps, _ = rest('GET', f"{GRAPH_API}/servicePrincipals?$filter={odata_eq('appId', APP_ID)}&$select=id,displayName", graph_token)
sps = sps.get('value', [])
if sps:
    SPN_OID = sps[0]['id']
else:
    sp, st = rest('POST', f'{GRAPH_API}/servicePrincipals', graph_token, {'appId': APP_ID})
    if st not in (200, 201):
        sys.exit(f'  Service principal creation failed ({st}): {error_text(sp)}')
    SPN_OID = sp['id']
print(f'  Service principal: {app_name} (OID: {SPN_OID})')


def spn_secret_valid(secret, wait=0):
    """Client-credentials sign-in to prove the secret works (new secrets take a moment to become usable)."""
    deadline = time.time() + wait
    while True:
        resp, st = rest('POST', f"https://login.microsoftonline.com/{S['tenant_id']}/oauth2/v2.0/token", None,
                        form={'grant_type': 'client_credentials', 'client_id': APP_ID, 'client_secret': secret,
                              'scope': 'https://graph.microsoft.com/.default'})
        if st == 200:
            return True
        if time.time() >= deadline:
            return False
        time.sleep(10)


stored = kv_call('GET', KV_SECRET_CLIENT_SECRET)
stored_app = kv_call('GET', KV_SECRET_CLIENT_ID)
if stored and stored_app == APP_ID and spn_secret_valid(stored):
    print('  Existing secret in Key Vault is valid — reusing it')
else:
    print('  Creating a new client secret (1 year) and storing it in Key Vault...')
    end = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365)).strftime('%Y-%m-%dT%H:%M:%SZ')
    cred, st = rest('POST', f"{GRAPH_API}/applications/{app['id']}/addPassword", graph_token,
                    {'passwordCredential': {'displayName': 'DI workshop lab', 'endDateTime': end}})
    if st != 200:
        sys.exit(f'  addPassword failed ({st}): {error_text(cred)}')
    kv_call('PUT', KV_SECRET_CLIENT_SECRET, cred['secretText'])
    kv_call('PUT', KV_SECRET_CLIENT_ID, APP_ID)
    kv_call('PUT', KV_SECRET_TENANT_ID, S['tenant_id'])
    del cred
# Always use the copy pulled from Key Vault from here on.
LAB_SPN_SECRET = kv_call('GET', KV_SECRET_CLIENT_SECRET)
if spn_secret_valid(LAB_SPN_SECRET, wait=120):
    print(f'  Secret read from Key Vault ({KV}/{KV_SECRET_CLIENT_SECRET}) — sign-in as {app_name} verified')
else:
    print('  WARNING: could not sign in with the secret from Key Vault yet (may still be propagating)')

# ============================================================
# Step 7: Azure SQL
# ============================================================
print('\n[Step 7] Azure SQL...')
graph_token = token_for('https://graph.microsoft.com')
SQL = S['sql_server']
groups, _ = rest('GET', f"{GRAPH_API}/groups?$filter={odata_eq('displayName', S['sql_admin_group'])}&$select=id", graph_token)
groups = groups.get('value', [])
if groups:
    GROUP_OID = groups[0]['id']
    print(f"  Entra group exists: {S['sql_admin_group']}")
else:
    nick = ''.join(c for c in S['sql_admin_group'] if c.isalnum()) or 'diworkshopsqladmins'
    grp, st = rest('POST', f'{GRAPH_API}/groups', graph_token,
                   {'displayName': S['sql_admin_group'], 'mailEnabled': False, 'mailNickname': nick,
                    'securityEnabled': True})
    if st not in (200, 201):
        sys.exit(f'  Group creation failed ({st}): {error_text(grp)}')
    GROUP_OID = grp['id']
    print(f"  Entra group created: {S['sql_admin_group']}")
for label, oid in ((ME_UPN, ME_OID), (app_name, SPN_OID)):
    resp, st = rest('POST', f'{GRAPH_API}/groups/{GROUP_OID}/members/$ref', graph_token,
                    {'@odata.id': f'{GRAPH_API}/directoryObjects/{oid}'})
    ok = st in (200, 204) or 'already exist' in error_text(resp).lower()
    print(f'  Group member: {label}' if ok else f'  Group member add failed for {label}: {error_text(resp)}')

if az(['az', 'sql', 'server', 'show', '-n', SQL, '-g', RG]).returncode == 0:
    print(f'  SQL server exists: {SQL}')
    if not S['sql_entra_only']:
        az(['az', 'sql', 'server', 'update', '-n', SQL, '-g', RG, '--admin-password', S['sql_admin_password']])
else:
    args = ['az', 'sql', 'server', 'create', '-n', SQL, '-g', RG, '-l', S['sql_location'], '--assign-identity',
            '--enable-public-network', 'false']
    if S['sql_entra_only']:
        args += ['--enable-ad-only-auth', '--external-admin-principal-type', 'Group',
                 '--external-admin-name', S['sql_admin_group'], '--external-admin-sid', GROUP_OID]
    else:
        args += ['--admin-user', S['sql_admin_user'], '--admin-password', S['sql_admin_password']]
    r = az(args)
    if r.returncode != 0:
        sys.exit(f'  SQL server creation failed: {r.stderr[:500]}')
    print(f"  SQL server created: {SQL}.database.windows.net ({'Entra-only' if S['sql_entra_only'] else 'Entra + SQL auth'})")
az(['az', 'sql', 'server', 'update', '-n', SQL, '-g', RG, '--enable-public-network', 'false'])
if not S['sql_entra_only']:
    kv_call('PUT', KV_SECRET_SQL_PASSWORD, S['sql_admin_password'])
    print(f'  SQL admin password stored in Key Vault ({KV}/{KV_SECRET_SQL_PASSWORD})')

if az(['az', 'sql', 'db', 'show', '-g', RG, '-s', SQL, '-n', S['sql_database']]).returncode == 0:
    print(f"  Database exists: {S['sql_database']}")
else:
    print(f"  Database {S['sql_database']}: will be created per-user after workshop users are provisioned")

# ============================================================
# Step 8: VNet, private endpoints, private DNS
# ============================================================
print('\n[Step 8] Private networking...')
VNET = S['vnet_name']
if az(['az', 'network', 'vnet', 'show', '-g', RG, '-n', VNET]).returncode == 0:
    print(f'  VNet exists: {VNET}')
else:
    r = az(['az', 'network', 'vnet', 'create', '-g', RG, '-n', VNET, '-l', LOC, '--address-prefixes', S['vnet_prefix'],
            '--subnet-name', PE_SUBNET, '--subnet-prefixes', S['pe_subnet_prefix']])
    print(f'  VNet created: {VNET}' if r.returncode == 0 else f'  VNet ERROR: {r.stderr[:300]}')
if az(['az', 'network', 'vnet', 'subnet', 'show', '-g', RG, '--vnet-name', VNET, '-n', GATEWAY_SUBNET]).returncode == 0:
    print(f'  Gateway subnet exists: {GATEWAY_SUBNET}')
else:
    r = az(['az', 'network', 'vnet', 'subnet', 'create', '-g', RG, '--vnet-name', VNET, '-n', GATEWAY_SUBNET,
            '--address-prefixes', S['gw_subnet_prefix'], '--delegations', 'Microsoft.PowerPlatform/vnetaccesslinks'])
    print(f'  Gateway subnet created: {GATEWAY_SUBNET} (delegated to Power Platform)'
          if r.returncode == 0 else f'  Gateway subnet ERROR: {r.stderr[:300]}')

sql_id = az(['az', 'sql', 'server', 'show', '-n', SQL, '-g', RG, '--query', 'id', '-o', 'tsv']).stdout.strip()
for pe_name, target_id, group_id, zone in [
    (f'pe-{SQL}-sql', sql_id, 'sqlServer', 'privatelink.database.windows.net'),
    (f'pe-{SA}-blob', storage_id, 'blob', 'privatelink.blob.core.windows.net'),
    (f'pe-{SA}-dfs', storage_id, 'dfs', 'privatelink.dfs.core.windows.net'),
    (f'pe-{KV}-vault', kv_id, 'vault', 'privatelink.vaultcore.azure.net'),
]:
    if not target_id:
        print(f'  Skipping {pe_name}: target resource not found')
        continue
    if az(['az', 'network', 'private-dns', 'zone', 'show', '-g', RG, '-n', zone]).returncode != 0:
        az(['az', 'network', 'private-dns', 'zone', 'create', '-g', RG, '-n', zone])
    link = f'link-{VNET}'
    if az(['az', 'network', 'private-dns', 'link', 'vnet', 'show', '-g', RG, '-z', zone, '-n', link]).returncode != 0:
        az(['az', 'network', 'private-dns', 'link', 'vnet', 'create', '-g', RG, '-z', zone, '-n', link,
            '-v', VNET, '-e', 'false'])
    if az(['az', 'network', 'private-endpoint', 'show', '-g', RG, '-n', pe_name]).returncode == 0:
        print(f'  Private endpoint exists: {pe_name}')
    else:
        r = az(['az', 'network', 'private-endpoint', 'create', '-g', RG, '-n', pe_name, '-l', LOC,
                '--vnet-name', VNET, '--subnet', PE_SUBNET, '--private-connection-resource-id', target_id,
                '--group-id', group_id, '--connection-name', f'{pe_name}-conn'])
        if r.returncode != 0:
            problem(f'Private endpoint {pe_name} not created: {r.stderr.strip()[:300]}')
            continue
        print(f'  Private endpoint created: {pe_name} ({group_id})')
    az(['az', 'network', 'private-endpoint', 'dns-zone-group', 'create', '-g', RG, '--endpoint-name', pe_name,
        '-n', 'default', '--private-dns-zone', zone, '--zone-name', zone.replace('.', '-')])

# ============================================================
# Step 9: SQL prep for Fabric mirroring
# ============================================================
print('\n[Step 9] SQL prep for Fabric mirroring...')
graph_token = token_for('https://graph.microsoft.com')
sami = az_json(['az', 'sql', 'server', 'update', '-n', SQL, '-g', RG, '--assign_identity'], {})
sami_pid = sami.get('identity', {}).get('principalId')
if not sami_pid:
    print('  SAMI ERROR: could not enable the system-assigned managed identity')
else:
    print(f'  SAMI enabled (principal ID: {sami_pid})')
    resp, st = rest('POST', f'{GRAPH_API}/roleManagement/directory/roleAssignments', graph_token,
                    {'principalId': sami_pid, 'roleDefinitionId': DIRECTORY_READERS_ROLE, 'directoryScopeId': '/'})
    if st in (200, 201):
        print('  Directory Readers assigned to SAMI')
    elif 'conflicting object' in error_text(resp).lower() or 'already exist' in error_text(resp).lower():
        print('  Directory Readers already assigned to SAMI')
    else:
        print(f'  Directory Readers warning ({st}): {error_text(resp, 200)}')
r = az(['az', 'sql', 'server', 'ad-admin', 'create', '-g', RG, '--server-name', SQL,
        '--display-name', S['sql_admin_group'], '--object-id', GROUP_OID])
print(f"  Entra admin: {S['sql_admin_group']} (members: {ME_UPN}, {app_name})"
      if r.returncode == 0 else f'  Entra admin ERROR: {r.stderr[:300]}')

# ============================================================
# Step 10: Security Defaults (opt-in)
# ============================================================
print('\n[Step 10] Security Defaults...')
graph_token = token_for('https://graph.microsoft.com')
if not S['disable_security_defaults']:
    print('  Left unchanged')
else:
    resp, st = rest('PATCH', f'{GRAPH_API}/policies/identitySecurityDefaultsEnforcementPolicy', graph_token,
                    {'isEnabled': False})
    print('  Security Defaults disabled — MFA not enforced' if st in (200, 204)
          else f'  Could not disable ({st}) — do it manually: {error_text(resp, 200)}')

# ============================================================
# Step 11: Workshop users
# ============================================================
print('\n[Step 11] Entra ID workshop users...')
graph_token = token_for('https://graph.microsoft.com')
password_base = generate_password(length=16)
created_users = []
for u in S['users']:
    suffix = u['name'][len(S['user_prefix']):]
    password = f'{password_base}{suffix}'
    print(f"  {u['upn']} ...", end=' ')
    existing, st = rest('GET', f"{GRAPH_API}/users/{urllib.parse.quote(u['upn'])}?$select=id", graph_token)
    if st == 200:
        oid = existing['id']
        resp, st2 = rest('PATCH', f'{GRAPH_API}/users/{oid}', graph_token,
                         {'passwordProfile': {'forceChangePasswordNextSignIn': False, 'password': password}})
        print('exists (password reset)' if st2 == 204 else f'exists (password reset failed: {error_text(resp, 120)})')
        created_users.append({**u, 'oid': oid, 'password': password})
        continue
    resp, st = rest('POST', f'{GRAPH_API}/users', graph_token, {
        'accountEnabled': True, 'displayName': u['display'], 'mailNickname': u['name'],
        'userPrincipalName': u['upn'],
        'passwordProfile': {'forceChangePasswordNextSignIn': False, 'password': password}})
    if st not in (200, 201):
        print(f'FAILED ({st}): {error_text(resp, 200)}')
        continue
    print('created')
    created_users.append({**u, 'oid': resp['id'], 'password': password})
print(f"  Users ready: {len(created_users)}/{len(S['users'])}")
if not created_users:
    sys.exit('  No users available. Your account needs the User Administrator role.')
with open(CREDS_FILE, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['Username', 'DisplayName', 'ObjectId'])
    for u in created_users:
        w.writerow([u['upn'], u['display'], u['oid']])
print(f'  Credentials saved: {CREDS_FILE}')
with open(PASSWORDS_FILE, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['Username', 'Password'])
    for u in created_users:
        w.writerow([u['upn'], u['password']])
print(f'  User passwords saved: {PASSWORDS_FILE}')

PARTICIPANTS_GROUP_OID = None
found, _ = rest('GET', f"{GRAPH_API}/groups?$filter={odata_eq('displayName', S['participants_group'])}&$select=id",
                graph_token)
found = found.get('value', [])
if found:
    PARTICIPANTS_GROUP_OID = found[0]['id']
else:
    nick = ''.join(c for c in S['participants_group'] if c.isalnum()) or 'diworkshopparticipants'
    grp, st = rest('POST', f'{GRAPH_API}/groups', graph_token,
                   {'displayName': S['participants_group'], 'mailEnabled': False, 'mailNickname': nick,
                    'securityEnabled': True})
    PARTICIPANTS_GROUP_OID = grp.get('id') if st in (200, 201) else None
    if not PARTICIPANTS_GROUP_OID:
        problem(f"Group {S['participants_group']} not created ({st}): {error_text(grp)}")
if PARTICIPANTS_GROUP_OID:
    for u in created_users:
        rest('POST', f'{GRAPH_API}/groups/{PARTICIPANTS_GROUP_OID}/members/$ref', graph_token,
             {'@odata.id': f"{GRAPH_API}/directoryObjects/{u['oid']}"})
    print(f"  Group {S['participants_group']}: {len(created_users)} workshop users")

CA_STATUS = 'not changed'
if S['ca_exclude'] and PARTICIPANTS_GROUP_OID:
    done = []
    for pol in S['ca_policies']:
        cur, st = rest('GET', f"{GRAPH_API}/identity/conditionalAccess/policies/{pol['id']}", graph_token)
        users = (cur.get('conditions') or {}).get('users') or {}
        if st != 200:
            problem(f"Could not read Conditional Access policy \"{pol['name']}\" ({st}): {error_text(cur, 200)}")
            continue
        if PARTICIPANTS_GROUP_OID in (users.get('excludeGroups') or []):
            done.append(pol['name'])
            continue
        users['excludeGroups'] = (users.get('excludeGroups') or []) + [PARTICIPANTS_GROUP_OID]
        body = {'conditions': {'users': {k: users[k] for k in ('includeUsers', 'excludeUsers', 'includeGroups',
                                                               'excludeGroups', 'includeRoles', 'excludeRoles')
                                         if k in users}}}
        resp, st = rest('PATCH', f"{GRAPH_API}/identity/conditionalAccess/policies/{pol['id']}", graph_token, body)
        if st in (200, 204):
            done.append(pol['name'])
        else:
            problem(f"Could not exclude {S['participants_group']} from \"{pol['name']}\" ({st}): {error_text(resp, 200)}")
    if done:
        print(f"  {S['participants_group']} excluded from {len(done)} MFA Conditional Access policies")
        CA_STATUS = f"{S['participants_group']} excluded from: " + '; '.join(done)

# ============================================================
# Step 11b: Create per-user databases for Fabric mirroring
# ============================================================
print('\n[Step 11b] Per-user SQL databases for mirroring...')
USER_DATABASES = {}
for u in created_users:
    user_suffix = u['name'][len(S['user_prefix']):]
    db_name = f"{S['sql_database']}-{S['user_prefix']}{user_suffix}"
    print(f"  {db_name}:", end=' ')
    if az(['az', 'sql', 'db', 'show', '-g', RG, '-s', SQL, '-n', db_name]).returncode == 0:
        print('exists')
    else:
        r = az(['az', 'sql', 'db', 'create', '-g', RG, '-s', SQL, '-n', db_name,
                '--service-objective', S['sql_service_objective'], '--sample-name', 'AdventureWorksLT',
                '--backup-storage-redundancy', 'Local'])
        if r.returncode == 0:
            print(f'created ({S["sql_service_objective"]}, AdventureWorksLT)')
        else:
            problem(f"Database {db_name} not created: {r.stderr.strip()[:300]}")
    USER_DATABASES[u['oid']] = db_name

# ============================================================
# Step 12: Fabric capacity state
# ============================================================
print('\n[Step 12] Fabric capacity state...')
cap = az_json(['az', 'fabric', 'capacity', 'show', '-g', RG, '--capacity-name', S['capacity_name']], {})
state = (cap.get('state') or cap.get('properties', {}).get('state') or '').lower()
print(f'  State: {state or "unknown"}')
if state != 'active':
    if state == 'paused':
        az(['az', 'fabric', 'capacity', 'resume', '-g', RG, '--capacity-name', S['capacity_name']])
    print('  Waiting 30s for the capacity...')
    time.sleep(30)

# ============================================================
# Step 13: Fabric workspaces
# ============================================================
print('\n[Step 13] Fabric workspaces...')
fabric_token = token_for('https://api.fabric.microsoft.com')
if not fabric_token:
    sys.exit('  Failed to get a Fabric token')
cap_id = None
for url in (f'{FABRIC_API}/admin/capacities', f'{FABRIC_API}/capacities'):
    caps, st = rest('GET', url, fabric_token)
    cap_id = next((c['id'] for c in caps.get('value', [])
                   if c.get('displayName', '').lower() == S['capacity_name'].lower()), None) if st == 200 else None
    if cap_id:
        break
if cap_id:
    print(f'  Capacity ID: {cap_id}')
else:
    problem(f'Fabric capacity {S["capacity_name"]} is active in Azure but is not visible to the Fabric API '
            f'identity {ME_UPN}. The configured capacity administrator is {S["fabric_admin_upn"]}. '
            'Sign in with that Entra user (or assign the signed-in user as a Fabric capacity administrator) '
            'and re-run setup. Stopping before creating workspaces that cannot be assigned to a capacity.')
    sys.exit('  Fabric capacity access is required before workspace provisioning can continue.')

admin_oid = ME_OID
if S['fabric_admin_upn'].lower() != ME_UPN.lower():
    found, st = rest('GET', f"{GRAPH_API}/users/{urllib.parse.quote(S['fabric_admin_upn'])}?$select=id", graph_token)
    admin_oid = found.get('id') if st == 200 else None
    if not admin_oid:
        print(f"  WARNING: Fabric admin {S['fabric_admin_upn']} not found")

all_ws, _ = rest('GET', f'{FABRIC_API}/workspaces', fabric_token)
ws_by_name = {w['displayName']: w['id'] for w in all_ws.get('value', [])}
WORKSPACE_IDS = {}
for u in created_users:
    ws_name = f"{S['workspace_prefix']}{u['name']}"
    print(f'  {ws_name}:', end=' ')
    ws_id = ws_by_name.get(ws_name)
    if ws_id:
        print('exists', end='')
    else:
        ws, st = rest('POST', f'{FABRIC_API}/workspaces', fabric_token, {'displayName': ws_name})
        if st not in (200, 201):
            print(f'FAILED ({st}): {error_text(ws, 200)}')
            PROBLEMS.append(f'Workspace {ws_name} not created')
            continue
        ws_id = ws['id']
        print('created', end='')
    WORKSPACE_IDS[ws_name] = ws_id
    if cap_id:
        resp, st = rest('POST', f'{FABRIC_API}/workspaces/{ws_id}/assignToCapacity', fabric_token, {'capacityId': cap_id})
        print(', capacity assigned' if st in (200, 202) else f', capacity assign failed ({st})', end='')
    roles, _ = rest('GET', f'{FABRIC_API}/workspaces/{ws_id}/roleAssignments', fabric_token)
    current = {ra.get('principal', {}).get('id'): ra for ra in roles.get('value', [])}
    # Fabric has no separate Owner role; Admin is the full workspace-owner equivalent.
    # Add both the workspace user and the setup user (admin) as admins
    wanted = [(u['oid'], 'Admin')]  # The workspace user
    if u['oid'] != ME_OID:  # If they're different, add the setup user too
        wanted.append((ME_OID, 'Admin'))  # The setup user as admin to all workspaces
    for oid, role in wanted:
        ra = current.get(oid)
        if ra and ra.get('role') == role:
            continue
        if ra:
            resp, st = rest('PATCH', f"{FABRIC_API}/workspaces/{ws_id}/roleAssignments/{ra['id']}", fabric_token, {'role': role})
        else:
            resp, st = rest('POST', f'{FABRIC_API}/workspaces/{ws_id}/roleAssignments', fabric_token,
                            {'principal': {'id': oid, 'type': 'User'}, 'role': role})
        if st not in (200, 201):
            print(f', role {role} failed ({st})', end='')
    admin_users = ', '.join([u['name'], ME_UPN.split('@')[0]] if u['name'] != ME_UPN.split('@')[0] else [u['name']])
    print(f", {admin_users} = Admin")

# ============================================================
# Step 14: Fabric VNet data gateway
# ============================================================
print('\n[Step 14] Fabric VNet data gateway...')
fabric_token = token_for('https://api.fabric.microsoft.com')
GATEWAY_ID = None
gws, st = rest('GET', f'{FABRIC_API}/gateways', fabric_token)
GATEWAY_ID = next((g['id'] for g in gws.get('value', []) if g.get('displayName') == S['gateway_name']), None)
if GATEWAY_ID:
    print(f"  Exists: {S['gateway_name']} ({GATEWAY_ID})")
elif cap_id:
    gw_body = {
        'type': 'VirtualNetwork', 'displayName': S['gateway_name'], 'capacityId': cap_id,
        'virtualNetworkAzureResource': {'subscriptionId': S['subscription_id'], 'resourceGroupName': RG,
                                        'virtualNetworkName': VNET, 'subnetName': GATEWAY_SUBNET},
        'inactivityMinutesBeforeSleep': S['gateway_sleep'], 'numberOfMemberGateways': 1}
    # A just-created capacity/VNet can return 404 EntityNotFound for a few minutes.
    for attempt in range(10):
        resp, st = rest('POST', f'{FABRIC_API}/gateways', fabric_token, gw_body)
        if st != 404 or attempt == 9:
            break
        if attempt == 0:
            print('  Gateway service cannot see the capacity/VNet yet — retrying for up to 5 minutes...', flush=True)
        time.sleep(30)
    if st in (200, 201):
        GATEWAY_ID = resp['id']
        print(f"  Created: {S['gateway_name']} ({GATEWAY_ID})")
    else:
        problem(f'VNet data gateway not created ({st}): {error_text(resp)}')
else:
    problem('VNet data gateway not created — no Fabric capacity')
if GATEWAY_ID:
    existing, _ = rest('GET', f'{FABRIC_API}/gateways/{GATEWAY_ID}/roleAssignments', fabric_token)
    have = {ra.get('principal', {}).get('id') for ra in existing.get('value', [])}
    grants = [(u['upn'], u['oid'], 'ConnectionCreator') for u in created_users]
    if admin_oid and admin_oid != ME_OID:
        grants.append((S['fabric_admin_upn'], admin_oid, 'Admin'))
    for upn, oid, role in grants:
        if oid in have:
            continue
        resp, st = rest('POST', f'{FABRIC_API}/gateways/{GATEWAY_ID}/roleAssignments', fabric_token,
                        {'principal': {'id': oid, 'type': 'User'}, 'role': role})
        if st not in (200, 201):
            print(f'  Gateway role {role} for {upn} failed ({st}): {error_text(resp, 150)}')
    print('  Workshop users have ConnectionCreator on the gateway')

# ============================================================
# Step 15: Participant access to the database
# ============================================================
def run_tsql_with_temporary_access(statements, database=None):
    """The SQL server is private-only. Open it to this machine just long enough to run T-SQL as the Entra
    admin, then remove the firewall rules and disable public access again. Returns an error string or None."""
    if database is None:
        database = S['sql_database']
    try:
        import pyodbc
    except ImportError:
        subprocess.run([sys.executable, '-m', 'pip', 'install', 'pyodbc', '-q'], capture_output=True)
        try:
            import pyodbc
        except ImportError:
            return 'pyodbc could not be installed'
    driver = next((d for d in ('ODBC Driver 18 for SQL Server', 'ODBC Driver 17 for SQL Server')
                   if d in pyodbc.drivers()), None)
    if not driver:
        return 'ODBC Driver 18 for SQL Server is not installed on this machine'
    rules = []

    def allow(ip):
        name = f'setup-temp-{len(rules) + 1}'
        az(['az', 'sql', 'server', 'firewall-rule', 'create', '-g', RG, '-s', SQL, '-n', name,
            '--start-ip-address', ip, '--end-ip-address', ip])
        rules.append(name)

    az(['az', 'sql', 'server', 'update', '-n', SQL, '-g', RG, '--enable-public-network', 'true'])
    try:
        eff = az(['az', 'sql', 'server', 'show', '-n', SQL, '-g', RG, '--query', 'publicNetworkAccess',
                  '-o', 'tsv']).stdout.strip()
        if eff != 'Enabled':
            return f'public network access could not be enabled temporarily (it is {eff}; likely an Azure Policy)'
        print(f"  Temporarily allowing {S['my_ip']} through the SQL firewall...", flush=True)
        seen = {S['my_ip']}
        allow(S['my_ip'])
        conn_str = (f'DRIVER={{{driver}}};SERVER=tcp:{SQL}.database.windows.net,1433;DATABASE={database};'
                    'Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;')
        last = ''
        for attempt in range(16):
            token = token_for('https://database.windows.net/').encode('utf-16-le')
            try:
                with pyodbc.connect(conn_str, autocommit=True,
                                    attrs_before={1256: struct.pack(f'<I{len(token)}s', len(token), token)}) as cn:
                    cur = cn.cursor()
                    for stmt in statements:
                        cur.execute(stmt)
                return None
            except pyodbc.Error as e:
                last = str(e)
                # A VPN/proxy can make SQL see a different client IP; the error names it.
                m = re.search(r"Client with IP address '([0-9.]+)'", last)
                if m and m.group(1) not in seen:
                    print(f'  SQL sees this machine as {m.group(1)} — allowing it temporarily', flush=True)
                    seen.add(m.group(1))
                    allow(m.group(1))
                elif attempt == 0:
                    print('  Waiting for the firewall rule to apply...', flush=True)
                time.sleep(20)
        return f'could not connect: {last[:300]}'
    finally:
        for name in rules:
            az(['az', 'sql', 'server', 'firewall-rule', 'delete', '-g', RG, '-s', SQL, '-n', name])
        az(['az', 'sql', 'server', 'update', '-n', SQL, '-g', RG, '--enable-public-network', 'false'])
        print('  SQL public network access disabled again; temporary firewall rules removed')


print('\n[Step 15] Participant access to the data...')
PARTICIPANT_SQL_STATUS = 'not configured'
grp_ident = S['participants_group'].replace(']', ']]')
grp_literal = S['participants_group'].replace("'", "''")
PARTICIPANT_TSQL = [
    f"IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'{grp_literal}') "
    f"CREATE USER [{grp_ident}] FROM EXTERNAL PROVIDER;",
    f'ALTER ROLE db_datareader ADD MEMBER [{grp_ident}];',
    f'ALTER ROLE db_datawriter ADD MEMBER [{grp_ident}];',
    # Participants create the mirroring connection with their own sign-in, so they need the mirroring permissions
    # (SELECT comes from db_datareader).
    f'GRANT ALTER ANY EXTERNAL MIRROR, VIEW DATABASE PERFORMANCE STATE, VIEW DATABASE SECURITY STATE TO [{grp_ident}];',
]
if not PARTICIPANTS_GROUP_OID:
    problem('Participant database access not configured — no participants group')
else:
    # Configure each per-user database with participant permissions
    configured_dbs = []
    for u in created_users:
        user_db = USER_DATABASES.get(u['oid'])
        if not user_db:
            continue
        err = run_tsql_with_temporary_access(PARTICIPANT_TSQL, database=user_db)
        if err:
            problem(f"Participant access not configured on {user_db}: {err}")
        else:
            configured_dbs.append(user_db)
    if configured_dbs:
        PARTICIPANT_SQL_STATUS = (f"{S['participants_group']}: db_datareader + db_datawriter + mirroring permissions "
                                  f"on {len(configured_dbs)} per-user databases")
        print(f'  {PARTICIPANT_SQL_STATUS}')
    else:
        problem("Participant database access not configured on any per-user database")

# Lab connections to ADLS use the participant's own sign-in, so the group needs data-plane read access.
if PARTICIPANTS_GROUP_OID and storage_id:
    r = az(['az', 'role', 'assignment', 'create', '--assignee-object-id', PARTICIPANTS_GROUP_OID,
            '--assignee-principal-type', 'Group', '--role', 'Storage Blob Data Reader', '--scope', storage_id])
    if r.returncode == 0 or 'already exists' in r.stderr.lower():
        print(f"  {S['participants_group']}: Storage Blob Data Reader on {SA}")
        PARTICIPANT_SQL_STATUS += f'; Storage Blob Data Reader on {SA}'
    else:
        problem(f"Storage Blob Data Reader for {S['participants_group']} on {SA} failed: {r.stderr.strip()[:300]}")

# ============================================================
# Step 16: Managed private endpoints for workspace notebooks
# ============================================================
print('\n[Step 16] Managed private endpoints for notebooks...')
fabric_token = token_for('https://api.fabric.microsoft.com')
MPE_STATUS = 'not configured'
MPE_MARKER = f'DI workshop notebooks - {SQL}'
mpe_pending = {}
for ws_name, ws_id in WORKSPACE_IDS.items():
    mpes, st = rest('GET', f'{FABRIC_API}/workspaces/{ws_id}/managedPrivateEndpoints', fabric_token)
    mpe = next((m for m in mpes.get('value', [])
                if m.get('targetPrivateLinkResourceId', '').lower() == sql_id.lower()), None)
    if mpe:
        print(f'  {ws_name}: managed private endpoint exists')
    else:
        mpe, st = rest('POST', f'{FABRIC_API}/workspaces/{ws_id}/managedPrivateEndpoints', fabric_token, {
            'name': f'mpe-{SQL}'[:64], 'targetPrivateLinkResourceId': sql_id,
            'targetSubresourceType': 'sqlServer', 'requestMessage': MPE_MARKER})
        if st not in (200, 201):
            problem(f'{ws_name}: managed private endpoint not created ({st}): {error_text(mpe, 200)}')
            continue
        print(f'  {ws_name}: managed private endpoint requested')
    mpe_pending[ws_id] = (ws_name, mpe['id'])

if mpe_pending:
    print('  Approving the private endpoint connections on the SQL server (first-time provisioning can take '
          'several minutes)...', flush=True)
approved = 0
deadline = time.time() + 25 * 60
while mpe_pending and time.time() < deadline:
    fabric_token = token_for('https://api.fabric.microsoft.com')
    for c in az_json(['az', 'network', 'private-endpoint-connection', 'list', '--id', sql_id], []):
        state = c.get('properties', {}).get('privateLinkServiceConnectionState', {})
        if state.get('status') == 'Pending' and MPE_MARKER in (state.get('description') or ''):
            az(['az', 'network', 'private-endpoint-connection', 'approve', '--id', c['id'],
                '--description', 'Approved by setup_all.py'])
    for ws_id, (ws_name, mpe_id) in list(mpe_pending.items()):
        m, st = rest('GET', f'{FABRIC_API}/workspaces/{ws_id}/managedPrivateEndpoints/{mpe_id}', fabric_token)
        if m.get('provisioningState') == 'Failed':
            problem(f'{ws_name}: managed private endpoint provisioning failed')
            del mpe_pending[ws_id]
        elif m.get('provisioningState') == 'Succeeded' and (m.get('connectionState') or {}).get('status') == 'Approved':
            print(f'  {ws_name}: approved')
            approved += 1
            del mpe_pending[ws_id]
    if mpe_pending:
        time.sleep(30)
for ws_id, (ws_name, _) in mpe_pending.items():
    problem(f'{ws_name}: managed private endpoint not approved yet — re-run setup, or approve it on the SQL server '
            '(Networking > Private access)')
if WORKSPACE_IDS and not mpe_pending:
    MPE_STATUS = f'SQL reachable from notebooks in {len(WORKSPACE_IDS)} workspace(s)'

# ============================================================
# Step 17: Fabric tenant settings
# ============================================================
print('\n[Step 17] Fabric tenant settings...')
fabric_token = token_for('https://api.fabric.microsoft.com')
graph_token = token_for('https://graph.microsoft.com')
TENANT_SETTINGS_STATUS = 'not configured'
if not S['configure_tenant']:
    print('  Skipped')
    TENANT_SETTINGS_STATUS = 'left unchanged (configure manually)'
else:
    grp_name = S['spn_api_group']
    found, _ = rest('GET', f"{GRAPH_API}/groups?$filter={odata_eq('displayName', grp_name)}&$select=id", graph_token)
    found = found.get('value', [])
    if found:
        SPN_GROUP_OID = found[0]['id']
        print(f'  Entra group exists: {grp_name}')
    else:
        nick = ''.join(c for c in grp_name if c.isalnum()) or 'diworkshopspns'
        grp, st = rest('POST', f'{GRAPH_API}/groups', graph_token,
                       {'displayName': grp_name, 'mailEnabled': False, 'mailNickname': nick, 'securityEnabled': True})
        SPN_GROUP_OID = grp.get('id') if st in (200, 201) else None
        if SPN_GROUP_OID:
            print(f'  Entra group created: {grp_name}')
        else:
            problem(f'Group {grp_name} not created ({st}): {error_text(grp)}')
    if SPN_GROUP_OID:
        resp, st = rest('POST', f'{GRAPH_API}/groups/{SPN_GROUP_OID}/members/$ref', graph_token,
                        {'@odata.id': f'{GRAPH_API}/directoryObjects/{SPN_OID}'})
        if st in (200, 204) or 'already exist' in error_text(resp).lower():
            print(f'  Group member: {app_name}')

    settings, st = rest('GET', f'{FABRIC_API}/admin/tenantsettings', fabric_token)
    if st != 200:
        problem(f'Could not read Fabric tenant settings ({st}) — are you a Fabric administrator? {error_text(settings, 200)}')
    else:
        by_name = {t['settingName']: t for t in settings.get('tenantSettings', [])}
        results = []
        for name, want_group in ((TENANT_SETTING_SPN_APIS, True), (TENANT_SETTING_ONELAKE_EXTERNAL, False)):
            cur = by_name.get(name)
            if not cur:
                problem(f'Tenant setting {name} not found')
                continue
            groups = cur.get('enabledSecurityGroups') or []
            title = cur.get('title', name)
            if cur.get('enabled') and (not groups or not want_group):
                print(f'  "{title}": already enabled for {"specific groups" if groups else "the entire organization"}')
                results.append(f'{title}: already enabled')
                continue
            if want_group and not SPN_GROUP_OID:
                problem(f'"{title}" not configured — no group for the lab service principal')
                continue
            if want_group and any(g.get('graphId') == SPN_GROUP_OID for g in groups) and cur.get('enabled'):
                print(f'  "{title}": already enabled for {grp_name}')
                results.append(f'{title}: enabled for {grp_name}')
                continue
            body = {'enabled': True}
            if want_group:
                body['enabledSecurityGroups'] = ([{'graphId': g['graphId'], 'name': g.get('name', '')} for g in groups]
                                                 if cur.get('enabled') else []) + [{'graphId': SPN_GROUP_OID, 'name': grp_name}]
            if cur.get('excludedSecurityGroups'):
                body['excludedSecurityGroups'] = cur['excludedSecurityGroups']
            resp, st = rest('POST', f'{FABRIC_API}/admin/tenantsettings/{name}/update', fabric_token, body)
            if st == 200:
                scope = f'group {grp_name}' if want_group else 'the entire organization'
                print(f'  "{title}": enabled for {scope}')
                results.append(f'{title}: enabled for {scope}')
            else:
                problem(f'Could not update "{title}" ({st}): {error_text(resp, 200)}')
        TENANT_SETTINGS_STATUS = '; '.join(results) or 'see problems'
        print('  Tenant setting changes can take up to 15 minutes to take effect')

# ============================================================
# Step 18: Nightly capacity pause (Logic App)
# ============================================================
print('\n[Step 18] Nightly capacity pause...')
PAUSE_STATUS = 'not scheduled'
if not S['pause_nightly']:
    print('  Skipped')
else:
    cap_rid = az(['az', 'fabric', 'capacity', 'show', '-g', RG, '--capacity-name', S['capacity_name'],
                  '--query', 'id', '-o', 'tsv']).stdout.strip()
    if not cap_rid:
        problem('Nightly pause not scheduled — capacity not found')
    else:
        hour, minute = (int(x) for x in S['pause_time'].split(':'))
        cap_url = f'https://management.azure.com{cap_rid}'
        msi = {'type': 'ManagedServiceIdentity', 'audience': 'https://management.azure.com/'}
        definition = {
            '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#',
            'contentVersion': '1.0.0.0',
            'triggers': {'Nightly': {'type': 'Recurrence', 'recurrence': {
                'frequency': 'Day', 'interval': 1, 'timeZone': S['pause_tz'],
                'schedule': {'hours': [hour], 'minutes': [minute]}}}},
            'actions': {
                'Get_capacity': {'type': 'Http', 'inputs': {
                    'method': 'GET', 'uri': f'{cap_url}?api-version={FABRIC_CAPACITY_API}', 'authentication': msi}},
                'Suspend_if_active': {
                    'type': 'If', 'runAfter': {'Get_capacity': ['Succeeded']},
                    'expression': {'and': [{'equals': ["@body('Get_capacity')?['properties']?['state']", 'Active']}]},
                    'actions': {'Suspend': {'type': 'Http', 'inputs': {
                        'method': 'POST', 'uri': f'{cap_url}/suspend?api-version={FABRIC_CAPACITY_API}',
                        'authentication': msi}}}},
            },
            'outputs': {},
        }
        la_url = (f"https://management.azure.com/subscriptions/{S['subscription_id']}/resourceGroups/{RG}"
                  f"/providers/Microsoft.Logic/workflows/{S['pause_logic_app']}?api-version=2019-05-01")
        resp, st = rest('PUT', la_url, token_for('https://management.azure.com/'), {
            'location': LOC, 'identity': {'type': 'SystemAssigned'},
            'properties': {'state': 'Enabled', 'definition': definition}})
        la_pid = resp.get('identity', {}).get('principalId') if st in (200, 201) else None
        if not la_pid:
            problem(f"Logic App {S['pause_logic_app']} not deployed ({st}): {error_text(resp, 300)}")
        else:
            print(f"  Logic App deployed: {S['pause_logic_app']} — runs daily at {S['pause_time']} {S['pause_tz']}")
            # A brand-new managed identity can take a moment to become assignable.
            for attempt in range(8):
                r = az(['az', 'role', 'assignment', 'create', '--assignee-object-id', la_pid,
                        '--assignee-principal-type', 'ServicePrincipal', '--role', 'Contributor', '--scope', cap_rid])
                if r.returncode == 0 or 'already exists' in r.stderr.lower():
                    break
                time.sleep(15)
            if r.returncode == 0 or 'already exists' in r.stderr.lower():
                print('  Logic App identity: Contributor on the capacity')
                PAUSE_STATUS = f"daily at {S['pause_time']} {S['pause_tz']} (Logic App {S['pause_logic_app']})"
            else:
                problem(f'Logic App role assignment failed: {r.stderr.strip()[:300]}')

# ============================================================
# Step 19: Participant handout
# ============================================================
print('\n[Step 19] Participant handout...')
first_ws = f"{S['workspace_prefix']}{created_users[0]['name']}" if created_users else S['workspace_prefix']
handout = f"""# DI Workshop — Environment Values

Generated by `setup_all.py` on {datetime.date.today().isoformat()}. The lab instructions use the placeholders
in the first column; substitute the values below. **Secrets are not written here** — the facilitator shares them.

| Placeholder | Value |
|-------------|-------|
| `<tenant-id>` | `{S['tenant_id']}` |
| `<subscription-id>` | `{S['subscription_id']}` |
| `<resource-group>` | `{RG}` |
| `<region>` | `{LOC}` |
| `<your-workspace>` | `{S['workspace_prefix']}<your user name>` (e.g. `{first_ws}`) |
| `<sql-server>` | `{SQL}` (FQDN `{SQL}.database.windows.net`, region {S['sql_location']}) |
| `<storage-account>` | `{SA}` (DFS URL `https://{SA}.dfs.core.windows.net`) |
| `<lab-spn-client-id>` | `{APP_ID}` ({app_name}) |
| `<key-vault>` | `{KV}` (facilitator only) |

## Secrets (facilitator)

- Lab service principal secret: `az keyvault secret show --vault-name {KV} -n {KV_SECRET_CLIENT_SECRET} --query value -o tsv`
""" + (f"- SQL admin password: `az keyvault secret show --vault-name {KV} -n {KV_SECRET_SQL_PASSWORD} --query value -o tsv`\n"
    if not S['sql_entra_only'] else '') + f"""- Workshop user passwords: `workshop_user_passwords.csv`

## Operations

- Nightly capacity pause: {PAUSE_STATUS}
- Fabric tenant settings: {TENANT_SETTINGS_STATUS}
- Participant database access: {PARTICIPANT_SQL_STATUS}
- Conditional Access: {CA_STATUS}
- Notebook access to SQL (managed private endpoints): {MPE_STATUS}

## Notes for participants

- Notebooks connect to SQL as **you** (your Entra sign-in) — no SQL passwords.
- Workspaces use a managed virtual network, so the first Spark session can take a few minutes to start.
"""
with open(HANDOUT_FILE, 'w', encoding='utf-8') as f:
    f.write(handout)
print(f'  Written: {HANDOUT_FILE}')

# ============================================================
# Summary
# ============================================================
print('\n' + '='*60)
print('SETUP FINISHED WITH PROBLEMS' if PROBLEMS else 'SETUP COMPLETE')
print('='*60)
print(f"""
Azure (subscription {S['subscription_id']}, resource group {RG}, {LOC}):
  Fabric capacity:   {S['capacity_name']} ({S['capacity_sku']}, {S['capacity_location']})
  ADLS Gen2:         {SA} (container {S['container']}; public access only from {', '.join(S['allowed_ips'])})
  Key Vault:         {KV}
  SQL:               {SQL}.database.windows.net / {S['sql_database']} ({S['sql_location']}, private only)
  VNet:              {VNET} ({PE_SUBNET}, {GATEWAY_SUBNET}); private endpoints for SQL, blob, dfs, vault
  VNet gateway:      {S['gateway_name']} ({GATEWAY_ID or 'NOT CREATED'})

Lab service principal:
  Name / client ID:  {app_name} / {APP_ID}
  Tenant ID:         {S['tenant_id']}
  Client secret:     Key Vault {KV}, secret "{KV_SECRET_CLIENT_SECRET}"
                     az keyvault secret show --vault-name {KV} -n {KV_SECRET_CLIENT_SECRET} --query value -o tsv
  SQL access:        member of Entra group "{S['sql_admin_group']}" (SQL server Entra admin)""")
if not S['sql_entra_only']:
    print(f"  SQL admin login:   {S['sql_admin_user']} — password in Key Vault secret \"{KV_SECRET_SQL_PASSWORD}\"")
print('\nUsers & workspaces:')
for u in created_users:
    print(f"  {u['upn']:<50} {S['workspace_prefix']}{u['name']}")
print(f"""
Credentials file:    {CREDS_FILE}
User passwords file:  {PASSWORDS_FILE}
Handout:             {HANDOUT_FILE}
Nightly pause:       {PAUSE_STATUS}
Tenant settings:     {TENANT_SETTINGS_STATUS}
Database access:     {PARTICIPANT_SQL_STATUS}
Conditional Access:  {CA_STATUS}
Notebook SQL access: {MPE_STATUS}
Fabric admin:        {S['fabric_admin_upn']}   User role: Admin

Notes:
  - Lab connections to SQL/ADLS must use the VNet data gateway "{S['gateway_name']}".
  - Share {HANDOUT_FILE} with participants (it contains no secrets).""")
if PROBLEMS:
    print('\nProblems (fix, then re-run setup with the same answers — completed steps are reused):')
    for p_ in PROBLEMS:
        print(f'  - {p_}')
    sys.exit(1)
