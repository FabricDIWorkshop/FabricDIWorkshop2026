#!/usr/bin/env python3
"""
Azure DI Workshop — Single Cleanup Script

Prompts for what to remove, shows everything it found, and after confirmation deletes:
  1. Fabric workspaces (<workspace prefix>*), after removing their managed private endpoints
  2. Fabric VNet data gateway
  3. Entra ID workshop users (<user prefix>*)
  4. Entra groups (SQL admin, Fabric API access, workshop participants), after removing them from
     any Conditional Access policy that references them
  5. Lab service principal app registration
  6. Key Vault(s) in the resource group (deleted and purged so names can be reused)
  7. The resource group (Fabric capacity, storage, SQL, networking, nightly-pause Logic App)

Fabric tenant settings are left as they are.

Uses your Azure CLI login — run `az login --tenant <tenant>` first.
Pass --yes to skip the final "type DELETE" confirmation.
"""
import sys
import time
import urllib.parse

from workshop_common import (
    FABRIC_API, GRAPH_API, GATEWAY_NAME,
    az, az_json, token_for, rest, error_text, section, ask, ask_yes_no,
)


def odata_filter(expr):
    return '$filter=' + urllib.parse.quote(expr)


def odata_str(value):
    return "'" + value.replace("'", "''") + "'"


print('='*60)
print('DI WORKSHOP — CLEANUP')
print('='*60)
print('Press Enter to accept the [default] shown for any prompt.')

# ============================================================
# Sign-in & discovery
# ============================================================
section('Azure sign-in')
account = az_json(['az', 'account', 'show'])
if not account:
    sys.exit('  Not logged in. Run: az login --tenant <tenant-id-or-domain>')
tenant_id = account['tenantId']
graph_token = token_for('https://graph.microsoft.com')
org, _ = rest('GET', f'{GRAPH_API}/organization?$select=displayName', graph_token)
me = az_json(['az', 'ad', 'signed-in-user', 'show', '--query', 'userPrincipalName'])
print(f"  Signed in as:  {me or account['user']['name']}")
print(f"  Tenant:        {(org.get('value') or [{}])[0].get('displayName', '?')} ({tenant_id})")
if not ask_yes_no('Clean up in this tenant?', True):
    sys.exit('  Run `az login --tenant <tenant-id-or-domain>` for the right tenant, then re-run.')
subs = az_json(['az', 'account', 'list', '--query',
                f"[?tenantId=='{tenant_id}' && state=='Enabled'].{{id:id, name:name}}"], [])
if not subs:
    sys.exit('  No enabled subscriptions are available in this tenant.')
current_subscription = account['id'].lower()
print('  Subscriptions in this tenant:')
for number, subscription in enumerate(subs, 1):
    marker = ' (current)' if subscription['id'].lower() == current_subscription else ''
    print(f"    {number}. {subscription['name']}  ({subscription['id']}){marker}")
default_number = next((number for number, subscription in enumerate(subs, 1)
                       if subscription['id'].lower() == current_subscription), 1)
selection = ask('Select subscription (Type a number)', default_number,
                lambda value: None if value.isdigit() and 1 <= int(value) <= len(subs)
                else f'Enter a number from 1 to {len(subs)}.')
subscription_id = subs[int(selection) - 1]['id']
az(['az', 'account', 'set', '--subscription', subscription_id])

section('What to remove')
preferred_rg = 'rg-di-workshop'
for arg in sys.argv[1:]:
    if arg.startswith('--resource-group='):
        preferred_rg = arg.split('=', 1)[1].strip() or preferred_rg
resource_groups = az_json(['az', 'group', 'list', '--query',
                           '[].{name:name, location:location, state:properties.provisioningState}'], [])
resource_groups.sort(key=lambda group: group['name'].lower())
if not resource_groups:
    sys.exit('  No resource groups are available in the selected subscription.')
print('  Resource groups in this subscription:')
for number, group in enumerate(resource_groups, 1):
    marker = ' (default)' if group['name'].lower() == preferred_rg.lower() else ''
    print(f"    {number}. {group['name']}  ({group.get('location', '?')}, {group.get('state', '?')}){marker}")
default_number = next((number for number, group in enumerate(resource_groups, 1)
                       if group['name'].lower() == preferred_rg.lower()), 1)
selection = ask('Select resource group to delete (Type a number)', default_number,
                lambda value: None if value.isdigit() and 1 <= int(value) <= len(resource_groups)
                else f'Enter a number from 1 to {len(resource_groups)}.')
RG = resource_groups[int(selection) - 1]['name']
rg_exists = az(['az', 'group', 'exists', '-n', RG]).stdout.strip() == 'true'
resources = az_json(['az', 'resource', 'list', '-g', RG, '--query', '[].type'], []) if rg_exists else []
resource_types = {}
for resource_type in resources:
    resource_types[resource_type] = resource_types.get(resource_type, 0) + 1
capacities = az_json(['az', 'fabric', 'capacity', 'list', '-g', RG, '--query', '[].{name:name, state:state}'], []) \
    if rg_exists else []
vaults = az_json(['az', 'keyvault', 'list', '-g', RG, '--query',
                  '[].{name:name, purgeProtection:properties.enablePurgeProtection}'], []) if rg_exists else []
logic_apps = az_json(['az', 'resource', 'list', '-g', RG, '--resource-type', 'Microsoft.Logic/workflows',
                      '--query', '[].name'], []) if rg_exists else []

fabric_token = token_for('https://api.fabric.microsoft.com')
capacity_names = {c['name'].lower() for c in capacities}
capacity_ids = set()
for capacity in capacities:
    resource = az_json(['az', 'fabric', 'capacity', 'show', '-g', RG, '--capacity-name', capacity['name']], {})
    if resource.get('id'):
        capacity_ids.add(resource['id'].lower())
for url in (f'{FABRIC_API}/admin/capacities', f'{FABRIC_API}/capacities'):
    fabric_capacities, code = rest('GET', url, fabric_token)
    if code == 200:
        capacity_ids.update(c['id'].lower() for c in fabric_capacities.get('value', [])
                             if c.get('displayName', '').lower() in capacity_names)
all_ws, st = rest('GET', f'{FABRIC_API}/workspaces', fabric_token)
workspaces = [w for w in all_ws.get('value', [])
              if str(w.get('capacityId') or (w.get('capacity') or {}).get('id') or '').lower() in capacity_ids]

user_prefix = 'diuser'
user_filter = odata_filter(f'startswith(userPrincipalName,{odata_str(user_prefix)})')
users, _ = rest('GET', f'{GRAPH_API}/users?{user_filter}&$select=id,userPrincipalName&$top=999', graph_token)
users = users.get('value', [])

gws, _ = rest('GET', f'{FABRIC_API}/gateways', fabric_token)
gateways = [g for g in gws.get('value', [])
            if str(g.get('capacityId') or (g.get('capacity') or {}).get('id') or '').lower() in capacity_ids]
gateway_name = ', '.join(g.get('displayName', GATEWAY_NAME) for g in gateways) or GATEWAY_NAME

groups = []
for name in ('DI Workshop SQL Admins', 'DI Workshop Service Principals', 'DI Workshop Participants'):
    group_filter = odata_filter(f'displayName eq {odata_str(name)}')
    found, _ = rest('GET', f'{GRAPH_API}/groups?{group_filter}&$select=id,displayName', graph_token)
    groups += found.get('value', [])

app_name = 'DIworkshop'
app_filter = odata_filter(f'displayName eq {odata_str(app_name)}')
apps, _ = rest('GET', f'{GRAPH_API}/applications?{app_filter}&$select=id,appId', graph_token)
apps = apps.get('value', [])

section('Deletion plan')
azure_resources = ', '.join(f'{count} {resource_type}' for resource_type, count in sorted(resource_types.items()))
plan = [
    ('Azure resources in group', azure_resources or 'none found'),
    (f'Workspaces ({len(workspaces)})', ', '.join(w['displayName'] for w in workspaces) or 'none found'),
    ('VNet data gateway', gateway_name if gateways else 'none found'),
    (f'Users ({len(users)})', ', '.join(u['userPrincipalName'] for u in users) or 'none found'),
    (f'Entra groups ({len(groups)})', ', '.join(g['displayName'] for g in groups) or 'none found'),
    ('App registration', ', '.join(f"{app_name} ({a['appId']})" for a in apps) or 'none found'),
    ('Key Vault(s)', (', '.join(v['name'] for v in vaults) + ' (deleted + purged)') if vaults else 'none found'),
    ('Resource group', (RG + (f" (capacity: {', '.join(c['name'] for c in capacities)})" if capacities else '')
                        + (f" (Logic App: {', '.join(logic_apps)})" if logic_apps else ''))
     if rg_exists else 'not found'),
]
for label, value in plan:
    print(f'  {label + ":":<22} {value}')
if not any([workspaces, gateways, users, groups, apps, vaults, rg_exists]):
    sys.exit('\n  Nothing to delete.')
if '--yes' not in sys.argv:
    try:
        if input('\n  Type DELETE to permanently remove everything above: ').strip() != 'DELETE':
            sys.exit('  Aborted — nothing was deleted.')
    except EOFError:
        sys.exit('\n  Aborted — nothing was deleted.')

# ============================================================
# Step 1: Resume paused capacities (workspace APIs need an active capacity)
# ============================================================
print('\n[Step 1] Fabric capacity state...')
resumed = False
for c in capacities:
    print(f"  {c['name']}: {c.get('state')}")
    if (c.get('state') or '').lower() == 'paused':
        az(['az', 'fabric', 'capacity', 'resume', '-g', RG, '--capacity-name', c['name']])
        resumed = True
if resumed:
    print('  Resumed — waiting 30s...')
    time.sleep(30)

# ============================================================
# Step 2: Workspaces
# ============================================================
print('\n[Step 2] Fabric workspaces...')
# A workspace can't be deleted while it contains managed private endpoints, so remove those first.
for w in workspaces:
    mpes, _ = rest('GET', f"{FABRIC_API}/workspaces/{w['id']}/managedPrivateEndpoints", fabric_token, fatal=False)
    for m in mpes.get('value', []):
        resp, code = rest('DELETE', f"{FABRIC_API}/workspaces/{w['id']}/managedPrivateEndpoints/{m['id']}",
                          fabric_token, fatal=False)
        print(f"  {w['displayName']}: managed private endpoint {m['name']} "
              + ('deleted' if code in (200, 204) else f'delete FAILED ({code}): {error_text(resp, 150)}'))
pending = {w['id']: w['displayName'] for w in workspaces}
for attempt in range(30):
    for ws_id, name in list(pending.items()):
        resp, code = rest('DELETE', f'{FABRIC_API}/workspaces/{ws_id}', fabric_token, fatal=False)
        if code in (200, 204, 404):
            print(f'  {name}: deleted')
            del pending[ws_id]
        elif code == 0:
            print(f'  {name}: no response from Fabric; will retry')
        elif 'WorkspaceContainsManagedEndpoints' not in error_text(resp):
            print(f'  {name}: FAILED ({code}): {error_text(resp, 150)}')
            del pending[ws_id]
    if not pending:
        break
    if attempt == 0:
        print('  Waiting for managed private endpoints to finish deleting...', flush=True)
    time.sleep(20)
for name in pending.values():
    print(f'  {name}: FAILED — managed private endpoints still deleting; re-run this script')
if not workspaces:
    print('  None')

# ============================================================
# Step 3: VNet data gateway
# ============================================================
print('\n[Step 3] Fabric VNet data gateway...')
for g in gateways:
    name = g.get('displayName', GATEWAY_NAME)
    resp, code = rest('DELETE', f"{FABRIC_API}/gateways/{g['id']}", fabric_token, fatal=False)
    if code in (200, 204, 404):
        print(f'  {name}: deleted')
        continue
    if code == 0:
        # The request timed out; the delete often still completed, so confirm against the service.
        check, check_code = rest('GET', f"{FABRIC_API}/gateways/{g['id']}", fabric_token, fatal=False)
        if check_code == 404:
            print(f'  {name}: deleted (confirmed after the timeout)')
            continue
        print(f'  {name}: FAILED — no response from Fabric. Re-run this script, or delete the gateway '
              'in the Fabric portal under Settings -> Manage connections and gateways.')
        continue
    print(f'  {name}: FAILED ({code}): {error_text(resp, 150)}')
if not gateways:
    print('  None')

# ============================================================
# Step 4-6: Entra ID objects
# ============================================================
print('\n[Step 4] Entra ID users...')
for u in users:
    resp, code = rest('DELETE', f"{GRAPH_API}/users/{u['id']}", graph_token)
    print(f"  {u['userPrincipalName']}: " + ('deleted' if code == 204 else f'FAILED ({code}): {error_text(resp, 150)}'))
if not users:
    print('  None')

print('\n[Step 5] Entra groups...')
# Remove the groups from any Conditional Access policy first, so policies aren't left pointing at deleted groups.
group_ids = {g['id'] for g in groups}
if group_ids:
    pols, st = rest('GET', f'{GRAPH_API}/identity/conditionalAccess/policies', graph_token)
    if st != 200:
        print(f'  Could not read Conditional Access policies ({st}) — check them for stale group references')
    for pol in pols.get('value', []):
        users = (pol.get('conditions') or {}).get('users') or {}
        if not any(set(users.get(k) or []) & group_ids for k in ('includeGroups', 'excludeGroups')):
            continue
        for k in ('includeGroups', 'excludeGroups'):
            users[k] = [x for x in (users.get(k) or []) if x not in group_ids]
        body = {'conditions': {'users': {k: users[k] for k in ('includeUsers', 'excludeUsers', 'includeGroups',
                                                               'excludeGroups', 'includeRoles', 'excludeRoles')
                                         if k in users}}}
        resp, code = rest('PATCH', f"{GRAPH_API}/identity/conditionalAccess/policies/{pol['id']}", graph_token, body)
        print(f"  Conditional Access \"{pol['displayName']}\": "
              + ('workshop group removed' if code in (200, 204) else f'update FAILED ({code}): {error_text(resp, 150)}'))
for g in groups:
    resp, code = rest('DELETE', f"{GRAPH_API}/groups/{g['id']}", graph_token)
    print(f"  {g['displayName']}: " + ('deleted' if code == 204 else f'FAILED ({code}): {error_text(resp, 150)}'))
if not groups:
    print('  None')

print('\n[Step 6] Lab service principal app registration...')
for a in apps:
    resp, code = rest('DELETE', f"{GRAPH_API}/applications/{a['id']}", graph_token)
    print(f"  {app_name} ({a['appId']}): " + ('deleted' if code == 204 else f'FAILED ({code}): {error_text(resp, 150)}'))
if not apps:
    print('  None')

# ============================================================
# Step 7: Key Vault(s) — delete and purge so the names can be reused
# ============================================================
print('\n[Step 7] Key Vault...')
for v in vaults:
    r = az(['az', 'keyvault', 'delete', '-n', v['name'], '-g', RG])
    if r.returncode != 0:
        print(f"  {v['name']}: delete FAILED: {r.stderr[:200]}")
        continue
    if v.get('purgeProtection'):
        print(f"  {v['name']}: deleted (purge protection is on — name stays reserved until retention ends)")
        continue
    r = az(['az', 'keyvault', 'purge', '-n', v['name'], '--no-wait'])
    print(f"  {v['name']}: deleted, purge started" if r.returncode == 0 else f"  {v['name']}: purge FAILED: {r.stderr[:200]}")
if not vaults:
    print('  None')

# ============================================================
# Step 8: Resource group
# ============================================================
print('\n[Step 8] Resource group...')
if rg_exists:
    r = az(['az', 'group', 'delete', '-n', RG, '--yes', '--no-wait'])
    print(f'  Deleting "{RG}" in the background' if r.returncode == 0 else f'  FAILED: {r.stderr[:200]}')
    if gateways:
        print('  NOTE: Power Platform releases the gateway subnet up to 2-3 hours after the gateway is deleted.')
        print('  If the VNet is left behind, re-run this script later to finish.')
else:
    print('  Not found')

print('\n' + '='*60)
print('CLEANUP STARTED' if rg_exists else 'CLEANUP COMPLETE')
print('='*60)
if rg_exists:
    print(f'  Check progress: az group exists -n {RG}')
