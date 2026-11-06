# Azure DI Workshop - Infrastructure Provisioning

Provisions a complete Azure Data & Intelligence workshop environment (5 participants by default) using your Azure CLI login, AzCopy, the Fabric REST API, Microsoft Graph, and Python. `setup_all.py` **prompts** for every value it needs — there is no config file to edit.

---

## What Gets Created

Names in the table are the prompt defaults; `<suffix>` is the short suffix you enter to make global names unique. Four names are **fixed** (not prompted) because the lab instructions use them literally: capacity `fabriccapdiws`, container `opendatasets`, database `sqldb-di-workshop`, and gateway `vnetgw-di-workshop-2026`.

### Azure Resources (in `rg-di-workshop`, region you choose)

| # | Resource | Default name | Details |
|---|----------|--------------|---------|
| 1 | Resource Group | `rg-di-workshop` | Container for all Azure resources |
| 2 | Fabric Capacity | `fabriccapdiws` | SKU you choose (default F64), region you choose |
| 3 | ADLS Gen2 Storage | `adlsdiws<suffix>` | HNS enabled, Standard_LRS. Public endpoint denies all traffic except allow-listed IPs; private endpoints for blob + dfs |
| 4 | Blob Container | `opendatasets` | NYC Taxi data (`nyctlc/yellow` by default, or the folders you choose) |
| 5 | Key Vault | `kv-diws-<suffix>` | RBAC authorization, same firewall model as storage, private endpoint. Holds the lab service principal secret (and the SQL admin password if SQL auth is used) |
| 6 | SQL Server | `sqldiws<suffix>` | **Public network access disabled**; private endpoint only. Entra-only auth by default. Can be in a different region (private endpoint stays in the VNet) |
| 7 | SQL Database | `sqldb-di-workshop` | S3, pre-loaded with the AdventureWorksLT sample |
| 8 | VNet | `vnet-di-workshop` | `10.60.0.0/16` with `snet-private-endpoints` and `snet-fabric-gateway` (delegated to `Microsoft.PowerPlatform/vnetaccesslinks`) |
| 9 | Private DNS zones | `privatelink.database.windows.net`, `privatelink.blob.core.windows.net`, `privatelink.dfs.core.windows.net`, `privatelink.vaultcore.azure.net` | Linked to the VNet |
| 10 | Logic App | `la-pause-fabric-capacity` | Pauses (suspends) the capacity every night at the time and time zone you choose (default 20:00 in your local zone). Uses a managed identity with Contributor on the capacity only; skips the call if the capacity is already paused |

### Entra ID Objects

| Object | Default name | Details |
|--------|--------------|---------|
| Workshop users | `diuser01@<domain>` … `diuserNN@<domain>` | The setup prompt determines how many users are created. Each gets a generated password with its username suffix, saved to `workshop_user_passwords.csv` (gitignored) |
| Lab service principal | `DIworkshop` | App registration + service principal used by the mirroring lab. Its client secret is created by the script, **stored in Key Vault, and read back from Key Vault** (validated with a client-credentials sign-in) |
| SQL admin group | `DI Workshop SQL Admins` | Security group containing you and the lab service principal; set as the SQL server's Entra admin |
| Participants group | `DI Workshop Participants` | All workshop users; has `db_datareader` + `db_datawriter` and the mirroring permissions on the database and `Storage Blob Data Reader` on the storage account, so notebooks and lab connections sign in as the participant (no passwords or keys) |
| Fabric API group | `DI Workshop Service Principals` | Security group containing the lab service principal; used to scope the "Service principals can call Fabric public APIs" tenant setting |

### Fabric Items

| Item | Default name | Details |
|------|--------------|---------|
| Workspaces | `DIWORKSPACE_diuser01` … | One per user, on the capacity. You are Admin; each user is **Admin** of their own workspace (Fabric's full workspace-owner equivalent) |
| VNet data gateway | `vnetgw-di-workshop-2026` | Runs in `snet-fabric-gateway` so Fabric can reach the private endpoints. Workshop users get **ConnectionCreator** |

> **Lab connections:** Azure SQL and ADLS connections in the labs (Copy Job, pipelines, mirroring) must select the VNet data gateway `vnetgw-di-workshop-2026`.

---

## Architecture

```
┌────────────────────────────── Resource group: rg-di-workshop ──────────────────────────────┐
│                                                                                            │
│  Fabric capacity (F64)            VNet vnet-di-workshop (10.60.0.0/16)                     │
│  fabriccapdiws                    ┌──────────────────────────────────────────────────┐     │
│   ├─ DIWORKSPACE_diuser01..05     │ snet-fabric-gateway ◄── Fabric VNet data gateway │     │
│   └─ vnetgw-di-workshop-2026 ─────────►│ snet-private-endpoints                           │     │
│                                   │   ├─ pe-sql   ──► SQL server (public: disabled)  │     │
│                                   │   ├─ pe-blob  ─┐                                 │     │
│                                   │   ├─ pe-dfs   ─┴► ADLS Gen2 (opendatasets/nyctlc)│     │
│                                   │   └─ pe-vault ──► Key Vault (lab SPN secret)     │     │
│                                   └──────────────────────────────────────────────────┘     │
│  Storage + Key Vault public endpoints: deny all except allow-listed IPs (setup machine)   │
└────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Prerequisites

| Requirement | Details |
|-------------|---------|
| **Azure CLI** | v2.61+ ([Install](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli)), signed in with `az login --tenant <tenant-id-or-domain>` |
| **Python** | 3.8+ (standard library only) |
| **Fabric administrator** | Needed for setup to configure the Fabric tenant settings (see [Fabric tenant settings](#fabric-tenant-settings)) |

### Required Permissions for the Signed-in Account

The scripts run as **your** Azure CLI user. That account needs:

| Scope | Role | Why |
|-------|------|-----|
| **Azure Subscription** | **Owner** (or Contributor + User Access Administrator) | Create resources; assign Storage Blob Data Contributor and Key Vault Secrets Officer to yourself |
| **Entra ID** | **User Administrator** | Create/delete workshop users |
| **Entra ID** | **Application Administrator** (or Cloud Application Administrator) | Create the lab app registration and its secret |
| **Entra ID** | **Groups Administrator** (or User Administrator) | Create the SQL admin and Fabric API groups |
| **Entra ID** | **Privileged Role Administrator** or **Global Administrator** | Assign Directory Readers to the SQL server's managed identity |
| **Entra ID** | **Security Administrator** or **Global Administrator** | Only if you choose to disable Security Defaults |
| **Entra ID** | **Conditional Access Administrator** (or Security/Global Administrator) | Only if you exclude participants from MFA Conditional Access policies |
| **Fabric** | **Fabric Administrator** | Update the Fabric tenant settings and look up the capacity via the admin API |

---

## Scripts

### Setup: `setup_all.py`

```powershell
az login --tenant <tenant-id-or-domain>
python setup_all.py
```

Every prompt shows a `[default]` — press Enter to accept it. The selected Azure CLI user performs the entire deployment (including AzCopy and Fabric API calls); choosing another user starts an interactive sign-in and verifies the account before setup proceeds. Capacity and workspace administration is selected separately because Fabric capacity creation rejects guest `#EXT#` UPNs. Inputs are validated as you go (region names, IP/CIDR ranges, password complexity), and globally unique names (storage, SQL, Key Vault) are **checked for availability** before anything is created. A review screen is shown before deployment starts. Re-running with the same answers is safe; existing resources are reused.

**Prompts**

| Group | Prompt | Default |
|-------|--------|---------|
| Sign-in | Entra user to run the entire setup (Azure CLI sign-in) | signed-in user; choosing another starts an interactive device-code sign-in and verifies the resulting account |
| | Tenant ID to deploy into | current signed-in tenant ID; selecting another tenant triggers device-code sign-in and verifies both user and tenant before setup proceeds |
| | Entra user who will administer the deployed infrastructure | selected setup user when it is a tenant member; guest `#EXT#` users must enter a tenant member UPN for capacity/workspace administration |
| | Subscription | current `az` subscription |
| | Domain for workshop user accounts | tenant default verified domain |
| Location | Azure region | `eastus2` |
| | Resource group | `rg-di-workshop` |
| Unique names | Suffix for globally unique names | — (required) |
| | Storage account / SQL server / Key Vault names | `adlsdiws<suffix>` / `sqldiws<suffix>` / `kv-diws-<suffix>` |
| Fabric | Pause the capacity every night? Pause time / time zone | Yes / `20:00` / your local Windows time zone (e.g. `Central Standard Time`) |
| | Configure the Fabric tenant settings? | Yes |
| | Capacity SKU / region | `F64` / same as region — the region must have enough Fabric capacity quota (CUs) for the SKU; otherwise a region with quota is suggested |
| | Entra user(s) administering the Fabric capacity | signed-in user when it is a tenant member; guest `#EXT#` users must enter a tenant member UPN |
| Network | Additional IPs/CIDRs allowed through the storage + Key Vault firewall | none (your current public IP is always added, plus any different IP Azure actually sees — see below) |
| | Tags for storage + Key Vault | none (e.g. `SecurityControl=Ignore` for an allowed governance-policy exemption) |
| Data | NYC Taxi folders to copy (`all` / `none` / list) | `yellow` |
| SQL | SQL server region | same as region, or the first region where this subscription is allowed to create SQL servers |
| | Entra-only authentication? | Yes (default — notebooks and connections use Entra sign-in). If No: SQL admin login (`sqladmin`) and password (blank = generated, stored in Key Vault) |
| Users | Number of workshop users | `5` |
| | Exclude the participants group from Conditional Access policies that require MFA? (only asked if such policies exist; lists them first) | Yes if another policy blocks MFA registration (new users couldn't sign in otherwise), else No |
| Advanced (optional) | Database SKU, SQL admin group, lab app name, user/workspace prefixes, VNet name and ranges, gateway auto-sleep, Logic App name, Fabric API group name, disable Security Defaults | see the tables above; workshop users are always Admins of their own workspaces |

**What it does**

| Step | Action |
|------|--------|
| 1 | Creates the resource group (resource providers are registered during the prompts, before names are checked) |
| 2 | Creates the Fabric capacity |
| 3 | Creates the storage account (tags applied first), locks the firewall to allow-listed IPs, creates the container |
| 4 | Creates the Key Vault (RBAC, same firewall), grants you Key Vault Secrets Officer |
| 5 | Downloads AzCopy if needed and copies the NYC Taxi folders using your CLI login |
| 6 | Creates/reuses the lab app registration + service principal. Reuses the secret in Key Vault if it still works; otherwise creates a new one (1 year) and stores it. Then **pulls the secret from Key Vault** and verifies a sign-in with it |
| 7 | Creates the SQL admin group (you + lab SPN), the SQL server (public access disabled) and the AdventureWorksLT database |
| 8 | Creates the VNet, subnets, private endpoints (SQL, blob, dfs, vault) and private DNS zones |
| 9 | Mirroring prep: SQL managed identity + Directory Readers, group set as Entra admin |
| 10 | Disables Security Defaults — only if you opted in |
| 11 | Creates the workshop users (Graph API), writes `workshop_users.csv`, creates the participants group and — if you chose to — excludes it from the MFA Conditional Access policies |
| 12 | Makes sure the capacity is active |
| 13 | Creates one workspace per user, assigns the capacity, and makes that user an Admin of their own workspace |
| 14 | Creates the Fabric VNet data gateway and grants users ConnectionCreator |
| 15 | Participant data access: grants the participants group `Storage Blob Data Reader` on the storage account (lab ADLS connections use the participant's own sign-in) and `db_datareader` + `db_datawriter` plus the mirroring permissions (`ALTER ANY EXTERNAL MIRROR`, `VIEW DATABASE PERFORMANCE STATE`, `VIEW DATABASE SECURITY STATE`) on the database — participants create the mirroring connection with their own sign-in. Because SQL is private-only, setup briefly enables public access for this machine's IP, runs the T-SQL as the Entra admin, then removes the firewall rules and disables public access again |
| 16 | Creates a Fabric managed private endpoint to the SQL server in every workshop workspace and approves the connections on the SQL server, so notebooks can reach SQL privately |
| 17 | Fabric tenant settings: enables "Service principals can call Fabric public APIs" for the Fabric API group and "Users can access data stored in OneLake with apps external to Fabric" — settings already enabled more broadly are left alone, and existing groups are kept |
| 18 | Deploys the nightly-pause Logic App and grants its managed identity Contributor on the capacity |
| 19 | Writes `workshop_environment.md` — the participant handout mapping each lab placeholder to this deployment's values (no secrets) |

Retrieve the lab secret later with:

```powershell
az keyvault secret show --vault-name <key-vault> -n lab-spn-client-secret --query value -o tsv
```

Key Vault secrets: `lab-spn-client-id`, `lab-spn-client-secret`, `lab-spn-tenant-id`, and `sql-admin-password` (SQL auth only). Reading them requires your IP to be allow-listed.

---

### Cleanup: `delete_all.py`

```powershell
python delete_all.py
```

Lists enabled subscriptions in the confirmed tenant and asks you to choose one by number, then lists resource groups in that subscription and asks you to choose one by number (default `rg-di-workshop`; `--resource-group=<name>` selects a different default). It inventories every Azure resource type in that group, discovers only Fabric workspaces and VNet gateways attached to capacities in that group, plus the workshop Entra objects, then **shows what it found** and asks you to type `DELETE` (pass `--yes` to skip that confirmation).

| Step | Action |
|------|--------|
| 1 | Resumes any paused capacity in the resource group (workspace deletes need it active) |
| 2 | Deletes each workspace's managed private endpoints, then the workspaces (retrying while the endpoints finish deleting) |
| 3 | Deletes the VNet data gateway |
| 4-6 | Deletes the workshop users; removes the workshop groups from any Conditional Access policy, then deletes the groups; deletes the lab app registration |
| 7 | Deletes **and purges** Key Vaults in the resource group so their names can be reused |
| 8 | Deletes the resource group in the background (including the nightly-pause Logic App) |

Fabric tenant settings are left as they are.

> The gateway subnet is released by Power Platform up to 2-3 hours after the gateway is deleted. If the resource group delete leaves the VNet behind, re-run `delete_all.py` later.

---

## Files in This Directory

| File | Purpose |
|------|---------|
| `setup_all.py` | **Create everything** — interactive, end-to-end provisioning |
| `delete_all.py` | **Delete everything** — interactive teardown |
| `workshop_common.py` | Shared helpers (Azure CLI/REST wrappers, prompts, validators) |
| `workshop_users.csv` | Generated credentials file (gitignored) |
| `workshop_environment.md` | Generated participant handout — lab placeholder values, no secrets (gitignored) |
| `azcopy_install/` | Downloaded AzCopy binary (auto-created, gitignored) |
| `FabricInfra.md` | This file |

---

## Data Sources

| Dataset | Source | Destination | Notes |
|---------|--------|-------------|-------|
| NYC Taxi (nyctlc) | `azureopendatastorage.blob.core.windows.net/nyctlc` | ADLS Gen2 → `opendatasets/nyctlc/` | `yellow` (~3,500 parquet files) by default; `all` copies green, yellow and FHV (~8,100 files) |
| AdventureWorksLT | Azure built-in `--sample-name` | SQL Database `sqldb-di-workshop` | `SalesLT.Customer`, `SalesLT.Product`, `SalesLT.SalesOrderHeader`, … |

---

## Fabric Tenant Settings

Setup configures the two settings the labs need (answer **Yes** to "Configure these Fabric tenant settings?"; requires Fabric administrator):

| Setting (API name) | What setup does |
|--------------------|-----------------|
| Service principals can call Fabric public APIs (`ServicePrincipalAccessPermissionAPIs`) | If off: enables it for the Fabric API group only. If on for specific groups: adds the Fabric API group and keeps the others. If on for the whole organization: leaves it alone |
| Users can access data stored in OneLake with apps external to Fabric (`OneLakeForThirdParty`) | Enables it if it's off |

Changes can take up to 15 minutes to take effect. To do it by hand: **Fabric admin portal → Tenant settings → Developer settings / OneLake settings**.

## Participant Handout

`workshop_environment.md` (generated, gitignored) maps every lab placeholder — `<sql-server>`, `<storage-account>`, `<your-workspace>`, `<lab-spn-client-id>`, `<tenant-id>`, … — to this deployment's values. Placeholders are only used for values that differ between deployments or participants; fixed names and the static parts of URLs (for example `https://<storage-account>.dfs.core.windows.net`) are written out literally. The lab instructions contain **no environment-specific names, IDs or passwords**. Secrets (lab service principal secret, SQL admin password, user passwords) are shared by the facilitator from Key Vault / `workshop_users.csv`.

### Service Principal — Fabric/Power BI Admin API Access (optional)

If an automation service principal needs Fabric/Power BI *admin APIs*:

1. Create a **security group** and add the service principal.
2. In **Fabric admin portal → Tenant settings → Admin API settings**, enable **Service principals can access read-only admin APIs** and/or **Service principals can access admin APIs used for updates**, scoped to that group.
3. Only a Fabric administrator or Global Administrator can change these settings.

---

## MFA / Security Defaults

The setup only touches Security Defaults if you opt in under **Advanced**. That setting is **tenant-wide** — use it only in a throwaway demo tenant. To change it manually: **Microsoft Entra ID → Properties → Manage Security Defaults**.

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `az` not recognized | Refresh PATH: `$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")` |
| Wrong tenant shown at the first prompt | Answer **n**, run `az login --tenant <tenant-id-or-domain>`, re-run |
| "Not available" for a storage / SQL / Key Vault name | The name is taken globally — pick another suffix or name |
| Key Vault name held by a soft-deleted vault | Setup offers to purge it, or choose another name |
| "public access was forced off (likely an Azure Policy)" | A governance policy disabled the public endpoint. Re-run and supply the policy's exemption tag under **Tags**, or run setup from inside the VNet |
| `Key Vault firewall blocked this machine` / storage `AuthorizationFailure` | Your public IP changed, or a VPN/proxy sends Azure traffic out through a different IP than other traffic. Setup reads the address Key Vault reports ("Client address: …") and allow-lists it on Key Vault and storage automatically — re-run setup. Those egress IPs can rotate, so each run may add another one |
| "Only N CU of Fabric quota free" / `RegionalQuota` error | New subscriptions often have 0 CU of Fabric quota in some regions. Choose a region with quota or [request an increase](https://learn.microsoft.com/fabric/enterprise/fabric-quotas) |
| VNet gateway `404 EntityNotFound` | The Fabric gateway service can't see a just-created capacity/VNet yet. Setup retries for 5 minutes; if it still fails, re-run setup |
| Fabric capacity "not ready to be updated" | Capacity is transitioning. Wait 2–5 minutes and retry |
| `Cannot reach api.fabric.microsoft.com over HTTPS` / `WinError 10060` | Fabric API is unreachable from this machine; verify outbound TCP 443, VPN, DNS and proxy configuration for `api.fabric.microsoft.com`. Azure CLI sign-in can succeed while this separate endpoint is blocked. The script retries one GET before stopping. |
| Capacity not visible in the Fabric API | It must be **Active** and visible to the signed-in Fabric API user. Run setup signed in as the configured capacity administrator, or add the signed-in user as a capacity administrator; otherwise setup stops before creating unassigned workspaces |
| AzCopy shows "N skipped" | Data already exists — expected on re-runs |
| SQL database creation takes long | Normal — the AdventureWorksLT sample takes a few minutes |
| "Fabric is not usable … UserNotLicensed" | Fabric has never been used in the tenant. Sign in once at https://app.fabric.microsoft.com with the same account to get a Fabric (Free) license, then re-run |
| `RegionDoesNotAllowProvisioning` for SQL | The subscription can't create SQL servers in that region. The SQL region prompt only accepts regions where provisioning is allowed |
| SQL server creation denied by policy | The tenant requires Entra-only auth — answer **Yes** to Entra-only |
| User or app creation fails | Check the Entra roles listed under [Prerequisites](#prerequisites) |
| Notebook can't connect to SQL (timeout) | In **Workspace settings → Network security**, the managed private endpoint to the SQL server must be **Approved**. Re-run setup — it re-approves pending connections |
| "Participant database access not configured" | Setup needs the ODBC Driver 18 for SQL Server locally and must be able to enable SQL public access briefly (a policy may block it). The problem message includes the T-SQL to run as the SQL Entra admin instead |
| Workshop user sign-in fails with error **53003** ("does not meet the criteria to access this resource") | Conditional Access requires MFA and blocks MFA registration from the user's location. Re-run setup and answer **Yes** to excluding the participants group from the MFA policies (or exclude it in **Entra → Protection → Conditional Access**). Tenants with Microsoft-managed policies may reapply them — re-check the exclusions if sign-ins start failing again |
| Resource group delete fails with `InUseSubnetCannotBeDeleted` / `PowerPlatformSAL` | The gateway subnet is released 2-3 hours after the gateway is deleted. Re-run `delete_all.py` later |

## License

Internal workshop material.
